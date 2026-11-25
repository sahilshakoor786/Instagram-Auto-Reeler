"""Understand what happens in a clip (Claude vision on keyframes) -> category, mood, hook, caption, tags."""

from __future__ import annotations

import base64
import json
import logging
import random
from pathlib import Path

log = logging.getLogger(__name__)

from reeler.niche import GENERIC_CATEGORIES, GENERIC_OFFLINE, Profile, load_profile

CATEGORIES = GENERIC_CATEGORIES  # default list; each niche gets its own (see reeler/niche.py)
MOODS = ["hype", "intense", "chill", "funny", "cinematic", "dramatic", "epic", "mysterious"]


def build_schema(categories: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": list(categories)},
            "mood": {"type": "string", "enum": MOODS},
            "energy": {"type": "integer", "description": "1 (calm) to 10 (chaotic)"},
            "description": {"type": "string"},
            "hook_text": {"type": "string"},
            "caption": {"type": "string"},
            "question_cta": {"type": "string"},
            "title": {"type": "string"},
            "hashtag_picks": {"type": "array", "items": {"type": "string"}},
            "music_search_terms": {"type": "array", "items": {"type": "string"}},
            "best_cover_frame": {"type": "integer"},
        },
        "required": ["category", "mood", "energy", "description", "hook_text", "caption", "question_cta",
                     "title", "hashtag_picks", "music_search_terms", "best_cover_frame"],
        "additionalProperties": False,
    }


SCHEMA = build_schema(CATEGORIES)

SYSTEM = """You are the social media editor for a short-video page (Instagram Reels and YouTube Shorts)
that posts: {niche}.{brand_line}
For every clip you see a few keyframes in time order. Work out what actually happens and write
copy that makes people stop scrolling, watch to the end, and comment. Write for fans of this topic.

Rules:
- category: the closest match from the allowed list.
- hook_text: on-screen text for the first 2 seconds, max 6 words, no hashtags, no emojis, creates curiosity.
  Style examples for this page: {hook_examples}.
- caption: 1-2 short punchy lines describing the moment in {language}. {emoji_rule} No hashtags in it.
  Never claim things you cannot see. Do not invent facts, names, prices, dates or news.
- question_cta: one short question in {language} that invites comments.
- title: a YouTube Shorts title in {language}, max 70 characters, no hashtags, no clickbait lies.
- hashtag_picks: 3-6 specific hashtags (without #) for THIS clip, preferring ones from the trending list
  provided when they fit. No spam tags like fyp, viral, explore, follow4follow.
- music_search_terms: 2-4 short search terms for a fitting trending track (genre/vibe words).
  Good fits for this page: {music}.
- best_cover_frame: index (0-based) of the most eye-catching keyframe for the cover.
- energy: integer 1-10.
"""


def system_prompt(cfg, profile: Profile) -> str:
    return SYSTEM.format(
        niche=profile.niche,
        brand_line=f" The page is called {profile.brand}." if profile.brand else "",
        hook_examples=", ".join(f'"{h}"' for h in profile.hook_examples[:5]),
        music=", ".join(profile.music[:6]),
        language=cfg.caption.language,
        emoji_rule="Use 1-2 fitting emojis." if cfg.caption.emoji else "No emojis.",
    )


def _b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("ascii")


def analyze_with_claude(frames: list[tuple[float, Path]], cfg, trending_tags: list[str],
                        filename: str, stats: dict) -> dict:
    import anthropic  # local import so the rest works without the SDK configured

    ai = cfg.ai
    profile = load_profile(cfg)
    client = anthropic.Anthropic(api_key=cfg.secrets.anthropic_api_key) if cfg.secrets.anthropic_api_key \
        else anthropic.Anthropic()
    content: list[dict] = []
    for i, (t, fp) in enumerate(frames):
        content.append({"type": "text", "text": f"Frame {i} at {t:.1f}s"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(fp)}})
    content.append({
        "type": "text",
        "text": (
            f"Clip file name: {filename}\n"
            f"Clip stats: {json.dumps(stats)}\n"
            f"Currently trending hashtags for this niche (best first): {', '.join(trending_tags[:40])}\n"
            "Analyse the clip and return the JSON."
        ),
    })
    system = system_prompt(cfg, profile)
    kwargs = dict(
        model=ai.model,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": ai.effort, "format": {"type": "json_schema", "schema": build_schema(profile.categories)}},
    )
    if ai.use_fallbacks:
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined to analyse this clip")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    data = json.loads(text)
    data["source"] = "claude"
    return data


# ----------------------------------------------------------------------------
# Offline fallback (no API key): heuristics from motion + loudness
# ----------------------------------------------------------------------------

HOOKS = GENERIC_OFFLINE["hooks"]
CAPTIONS = GENERIC_OFFLINE["captions"]
QUESTIONS = GENERIC_OFFLINE["questions"]
MUSIC = GENERIC_OFFLINE["music"]


def _pick(rnd: random.Random, table, mood: str, fallback, topic: str = ""):
    if isinstance(table, dict):
        options = table.get(mood) or fallback.get(mood) or next(iter(fallback.values()))
    else:
        options = table or fallback
    options = list(options)
    if not topic:  # templates that need a topic name only make sense when we have one
        options = [o for o in options if "{topic}" not in o] or options
    return rnd.choice(options).replace("{topic}", topic or "this")


def analyze_offline(stats: dict, frames_count: int, seed: str = "", profile: Profile | None = None) -> dict:
    rnd = random.Random(seed)
    cuts = stats.get("scene_cuts_per_sec") or 0
    vol = stats.get("mean_volume_db")
    loud = vol is not None and vol > -22
    if cuts > 0.6 or (loud and cuts > 0.3):
        mood, energy = "hype", 8
    elif cuts > 0.25 or loud:
        mood, energy = "intense", 6
    elif cuts < 0.08:
        mood, energy = "cinematic", 3
    else:
        mood, energy = "chill", 4
    off = profile.offline if profile else GENERIC_OFFLINE
    topic = profile.topic if profile and profile.topic != "this" else ""
    caption = _pick(rnd, off.get("captions"), mood, CAPTIONS, topic)
    hook = _pick(rnd, off.get("hooks"), mood, HOOKS)
    return {
        "category": "other",
        "mood": mood,
        "energy": energy,
        "description": f"Clip ({mood}, {cuts} cuts/s)",
        "hook_text": hook,
        "caption": caption,
        "question_cta": _pick(rnd, off.get("questions"), mood, QUESTIONS),
        "title": _title_from(caption, hook),
        "hashtag_picks": [],
        "music_search_terms": list(_pick_list(off.get("music"), mood, MUSIC)),
        "best_cover_frame": frames_count // 2,
        "source": "heuristic",
    }


def _pick_list(table, mood, fallback):
    if isinstance(table, dict):
        return table.get(mood) or fallback.get(mood) or ["trending"]
    return table or fallback.get(mood) or ["trending"]


def _title_from(caption: str, hook: str) -> str:
    text = (caption or "").strip() or (hook or "").title()
    return text[:70]
