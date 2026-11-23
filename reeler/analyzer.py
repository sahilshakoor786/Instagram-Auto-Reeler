"""Understand what happens in a clip (Claude vision on keyframes) -> category, mood, hook, caption, tags."""

from __future__ import annotations

import base64
import json
import logging
import random
from pathlib import Path

log = logging.getLogger(__name__)

CATEGORIES = ["car_chase", "shootout", "heist", "stunt", "open_world", "scenery", "nightlife", "funny",
              "police", "vehicles", "boats_air", "character", "beach", "other"]
MOODS = ["hype", "intense", "chill", "funny", "cinematic", "dramatic", "epic", "mysterious"]

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "mood": {"type": "string", "enum": MOODS},
        "energy": {"type": "integer", "description": "1 (calm) to 10 (chaotic)"},
        "description": {"type": "string"},
        "hook_text": {"type": "string"},
        "caption": {"type": "string"},
        "question_cta": {"type": "string"},
        "hashtag_picks": {"type": "array", "items": {"type": "string"}},
        "music_search_terms": {"type": "array", "items": {"type": "string"}},
        "best_cover_frame": {"type": "integer"},
    },
    "required": ["category", "mood", "energy", "description", "hook_text", "caption", "question_cta",
                 "hashtag_picks", "music_search_terms", "best_cover_frame"],
    "additionalProperties": False,
}

SYSTEM = """You are the social media editor for an Instagram page that posts short {niche}.
For every clip you see a few keyframes in time order. Work out what actually happens and write
copy that makes people stop scrolling, watch to the end, and comment.

Rules:
- hook_text: on-screen text for the first 2 seconds, max 6 words, no hashtags, no emojis, creates curiosity
  (e.g. "WAIT FOR THE ENDING", "VICE CITY HITS DIFFERENT", "COPS HAD NO CHANCE").
- caption: 1-2 short punchy lines describing the moment in {language}. {emoji_rule} No hashtags in it.
  Never claim things you cannot see. Do not invent release dates or leaks.
- question_cta: one short question that invites comments (e.g. "Would you survive this chase?").
- hashtag_picks: 3-6 specific hashtags (without #) for THIS clip, preferring ones from the trending list
  provided when they fit. No spam tags like fyp, viral, explore, follow4follow.
- music_search_terms: 2-4 short search terms for a fitting trending track (genre/vibe words such as
  "phonk", "synthwave", "80s miami", "drill", "cinematic trailer", "chill lofi").
- best_cover_frame: index (0-based) of the most eye-catching keyframe for the Reel cover.
- energy: integer 1-10.
"""


def _b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("ascii")


def analyze_with_claude(frames: list[tuple[float, Path]], cfg, trending_tags: list[str],
                        filename: str, stats: dict) -> dict:
    import anthropic  # local import so the rest works without the SDK configured

    ai = cfg.ai
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
    system = SYSTEM.format(
        niche=ai.niche, language=cfg.caption.language,
        emoji_rule="Use 1-2 fitting emojis." if cfg.caption.emoji else "No emojis.",
    )
    kwargs = dict(
        model=ai.model,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": ai.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
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

HOOKS = {
    "hype": ["WAIT FOR IT", "THIS IS INSANE", "VICE CITY GOES CRAZY", "NO WAY THIS HAPPENED"],
    "intense": ["COPS HAD NO CHANCE", "WATCH TILL THE END", "THIS GOT INTENSE", "FULL SEND"],
    "chill": ["VICE CITY HITS DIFFERENT", "JUST LOOK AT THIS", "GTA 6 IS BEAUTIFUL", "POV: SUNSET IN LEONIDA"],
    "cinematic": ["THIS LOOKS REAL", "NEXT GEN IS HERE", "PURE CINEMA"],
}
CAPTIONS = {
    "hype": ["GTA 6 never misses 🔥", "Vice City chaos at its finest 😤", "This game is on another level 🚀"],
    "intense": ["Full send in Vice City 🚔💨", "When the wanted level hits 5 stars 😳", "No brakes, no regrets 🏎️"],
    "chill": ["Just cruising through Leonida 🌴", "Vice City sunsets hit different 🌅", "The details in this game 😮‍💨"],
    "cinematic": ["Rockstar really cooked with this one 🎬", "Graphics so real it hurts 🤯"],
}
QUESTIONS = ["Would you try this? 👇", "Rate this clip 1-10 👇", "What would you do here? 👇",
             "Who else is hyped for this? 👇", "Best GTA ever? 👇"]
MUSIC = {
    "hype": ["phonk", "drift phonk"], "intense": ["phonk", "trap"], "chill": ["synthwave", "80s miami"],
    "cinematic": ["cinematic", "synthwave"],
}


def analyze_offline(stats: dict, frames_count: int, seed: str = "") -> dict:
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
    return {
        "category": "other",
        "mood": mood,
        "energy": energy,
        "description": f"Gameplay clip ({mood}, {cuts} cuts/s)",
        "hook_text": rnd.choice(HOOKS[mood]),
        "caption": rnd.choice(CAPTIONS[mood]),
        "question_cta": rnd.choice(QUESTIONS),
        "hashtag_picks": [],
        "music_search_terms": MUSIC[mood],
        "best_cover_frame": frames_count // 2,
        "source": "heuristic",
    }
