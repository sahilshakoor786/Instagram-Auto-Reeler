"""Everything that depends on *what* the page is about (the niche).

Nothing here is hard-wired to one topic. A niche profile is built from, in order of priority:
1. `niche_profile:` / `hashtags:` in the config or a preset (presets/*.yaml),
2. a "starter kit" Claude writes once for the niche and caches (work_dir/niche_kit.json),
3. generic defaults that work for any kind of video.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

GENERIC_CATEGORIES = ["highlight", "funny", "fail", "tutorial", "tips", "reaction", "satisfying",
                      "transformation", "story", "behind_the_scenes", "showcase", "other"]
GENERIC_HOOK_EXAMPLES = ["WAIT FOR THE ENDING", "NOBODY EXPECTED THIS", "YOU NEED TO SEE THIS",
                         "WATCH TILL THE END"]
GENERIC_MUSIC = ["trending", "upbeat", "chill lofi", "cinematic", "phonk"]

# Offline (no API key) templates. They must read fine for ANY topic.
# {topic} is replaced with a short topic name, e.g. "cooking" or "football".
GENERIC_OFFLINE = {
    "hooks": {
        "hype": ["WAIT FOR IT", "THIS IS INSANE", "NO WAY THIS HAPPENED", "WATCH TILL THE END"],
        "intense": ["DON'T BLINK", "THIS GOT INTENSE", "WATCH TILL THE END", "HOLD ON"],
        "chill": ["JUST LOOK AT THIS", "SO SATISFYING", "TAKE A SECOND", "THIS HITS DIFFERENT"],
        "cinematic": ["PURE CINEMA", "LOOK AT THE DETAILS", "THIS LOOKS UNREAL"],
    },
    "captions": {
        "hype": ["This is on another level 🔥", "Had to share this one 😤", "Best {topic} moment this week 🚀"],
        "intense": ["Things escalated quickly 😳", "Watch closely 👀", "No one saw this coming 💨"],
        "chill": ["A little {topic} to slow down your day 🌿", "The details here 😮‍💨", "Save this for later ✨"],
        "cinematic": ["Shot like a movie 🎬", "Unreal quality 🤯"],
    },
    "questions": ["Would you try this? 👇", "Rate this 1-10 👇", "What would you do here? 👇",
                  "Tag someone who needs to see this 👇", "Seen anything better? 👇"],
    "music": {"hype": ["trending", "upbeat"], "intense": ["epic", "trap"], "chill": ["lofi", "chill"],
              "cinematic": ["cinematic", "ambient"]},
}

STOPWORDS = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "my", "your", "our", "about",
             "clips", "clip", "videos", "video", "shorts", "short", "reels", "reel", "content", "page", "posts",
             "best", "daily", "top", "from", "by", "at", "is", "are"}


@dataclass
class Profile:
    niche: str
    topic: str
    categories: list[str]
    hook_examples: list[str]
    music: list[str]
    offline: dict = field(default_factory=dict)
    brand: str = ""


# ----------------------------------------------------------------------------
def niche_words(niche: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (niche or "").lower())
    out = []
    for w in words:
        if w in STOPWORDS or len(w) < 3 or w in out:
            continue
        out.append(w)
    return out


def topic_of(niche: str) -> str:
    """Short topic name for templates: 'cooking recipes videos' -> 'cooking recipes'."""
    words = [w for w in re.findall(r"[\w'-]+", niche or "") if w.lower() not in
             {"clips", "clip", "videos", "video", "shorts", "reels", "content"}]
    topic = " ".join(words[:3]).strip()
    return topic or "this"


def generic_seed(niche: str) -> dict:
    """Starter hashtags made only from the niche words (used when nothing better is available)."""
    words = niche_words(niche)
    broad: dict[str, float] = {}
    nic: dict[str, float] = {}
    if len(words) >= 2:
        broad["".join(words[:2])] = 1.0
    for i, w in enumerate(words[:4]):
        broad.setdefault(w, round(0.9 - 0.1 * i, 2))
    for w in words[:3]:
        for suffix, weight in (("lovers", 0.5), ("tips", 0.45), ("daily", 0.4), ("community", 0.4),
                               ("life", 0.35)):
            nic[f"{w}{suffix}"] = weight
    if not broad:  # niche was empty / only stop words
        broad = {"shorts": 0.6, "shortvideo": 0.5, "dailyvideo": 0.4}
    return {"broad": broad, "niche": nic, "categories": {}}


# ----------------------------------------------------------------------------
# Claude "starter kit": categories, hook style, music vibes and hashtags for a new niche.
# ----------------------------------------------------------------------------
KIT_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "categories": {"type": "array", "items": {"type": "string"}},
        "hook_examples": {"type": "array", "items": {"type": "string"}},
        "music_vibes": {"type": "array", "items": {"type": "string"}},
        "broad_tags": {"type": "array", "items": {
            "type": "object", "properties": {"tag": {"type": "string"}, "weight": {"type": "number"}},
            "required": ["tag", "weight"], "additionalProperties": False}},
        "niche_tags": {"type": "array", "items": {
            "type": "object", "properties": {"tag": {"type": "string"}, "weight": {"type": "number"}},
            "required": ["tag", "weight"], "additionalProperties": False}},
        "category_tags": {"type": "array", "items": {
            "type": "object",
            "properties": {"category": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}},
            "required": ["category", "tags"], "additionalProperties": False}},
    },
    "required": ["topic", "categories", "hook_examples", "music_vibes", "broad_tags", "niche_tags", "category_tags"],
    "additionalProperties": False,
}

KIT_PROMPT = """A short-form video page (Instagram Reels / YouTube Shorts) posts: {niche}.
Language of the audience: {language}.
Build a starter kit for it:
- topic: 1-3 word short name of the topic (e.g. "cooking", "football skills").
- categories: 8-12 snake_case content categories you would sort this page's clips into, last one "other".
- hook_examples: 5 on-screen hook texts (max 6 words, UPPERCASE, curiosity-driven) typical for this niche.
- music_vibes: 4-6 short music search terms that fit this niche (genre / mood words).
- broad_tags: 5-7 big, popular hashtags for this niche (no #), weight 0.6-1.0 by popularity.
- niche_tags: 6-10 smaller, very on-topic hashtags, weight 0.35-0.7.
- category_tags: for each category, 2-4 hashtags that fit that kind of clip.
Never use spam tags such as fyp, viral, explore, follow4follow, reels, trending."""


def kit_path(cfg):
    return cfg.work_path / "niche_kit.json"


def _has_api_key(cfg) -> bool:
    return bool(cfg.secrets.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))


def cached_kit(cfg) -> dict | None:
    p = kit_path(cfg)
    try:
        kit = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return kit if kit.get("niche") == cfg.ai.niche else None


def generate_kit(cfg) -> dict:
    import anthropic

    ai = cfg.ai
    client = anthropic.Anthropic(api_key=cfg.secrets.anthropic_api_key) if cfg.secrets.anthropic_api_key \
        else anthropic.Anthropic()
    kwargs = dict(
        model=ai.model, max_tokens=3000,
        messages=[{"role": "user", "content": KIT_PROMPT.format(niche=ai.niche, language=cfg.caption.language)}],
        output_config={"effort": ai.effort, "format": {"type": "json_schema", "schema": KIT_SCHEMA}},
    )
    if ai.use_fallbacks:
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        resp = client.messages.create(**kwargs)
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    kit = json.loads(text)
    kit["niche"] = ai.niche
    return kit


def ensure_kit(cfg, force: bool = False) -> dict | None:
    """Return the cached kit, or ask Claude for one (once). None when offline / AI off / it fails."""
    if not force:
        kit = cached_kit(cfg)
        if kit:
            return kit
    if not cfg.ai.enabled or not _has_api_key(cfg):
        return None
    try:
        kit = generate_kit(cfg)
    except Exception as exc:  # noqa: BLE001 - optional nicety
        log.warning("Could not build a hashtag/category kit for '%s' (%s) - using generic ones", cfg.ai.niche, exc)
        return None
    p = kit_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(kit, ensure_ascii=False, indent=1), encoding="utf-8")
    return kit


def kit_seed(kit: dict) -> dict:
    def clamp(w) -> float:
        try:
            return max(0.1, min(1.0, float(w)))
        except (TypeError, ValueError):
            return 0.5
    cats: dict[str, dict] = {}
    for item in kit.get("category_tags") or []:
        cats[str(item.get("category", "other"))] = {t: 0.5 for t in item.get("tags") or []}
    return {
        "broad": {i["tag"]: clamp(i.get("weight")) for i in kit.get("broad_tags") or [] if i.get("tag")},
        "niche": {i["tag"]: clamp(i.get("weight")) for i in kit.get("niche_tags") or [] if i.get("tag")},
        "categories": cats,
    }


# ----------------------------------------------------------------------------
def _clean_categories(cats) -> list[str]:
    out = []
    for c in cats or []:
        c = re.sub(r"[^a-z0-9_]", "", str(c).lower().replace(" ", "_").replace("-", "_"))
        if c and c not in out:
            out.append(c)
    if "other" not in out:
        out.append("other")
    return out


def load_profile(cfg, *, use_kit: bool = True) -> Profile:
    np_ = dict(cfg.get("niche_profile") or {})
    kit = cached_kit(cfg) if use_kit else None
    kit = kit or {}
    niche = cfg.ai.niche
    categories = np_.get("categories") or kit.get("categories") or GENERIC_CATEGORIES
    offline = {k: dict(v) if isinstance(v, dict) else list(v) for k, v in GENERIC_OFFLINE.items()}
    for k, v in (np_.get("offline") or {}).items():
        offline[k] = v
    music = np_.get("music") or kit.get("music_vibes") or GENERIC_MUSIC
    return Profile(
        niche=niche,
        topic=np_.get("topic") or kit.get("topic") or topic_of(niche),
        categories=_clean_categories(categories),
        hook_examples=list(np_.get("hook_examples") or kit.get("hook_examples") or GENERIC_HOOK_EXAMPLES),
        music=list(music),
        offline=offline,
        brand=cfg.get("brand") or "",
    )
