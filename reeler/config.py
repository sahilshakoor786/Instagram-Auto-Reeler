"""Configuration loading: config.yaml (settings) + .env (secrets)."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import dotenv_values, load_dotenv
except ImportError:  # pragma: no cover - optional
    load_dotenv = dotenv_values = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = PROJECT_ROOT / "presets"
DATA_DIR = Path(__file__).resolve().parent / "data"

# Secrets that belong to ONE social account. With several projects side by side these are read
# from the project's own `account_env` file, never from the shared .env, so accounts can't mix.
ACCOUNT_KEYS = ("IG_USER_ID", "IG_ACCESS_TOKEN", "IG_USERNAME", "IG_PASSWORD", "IG_SESSIONID")


DEFAULTS: dict[str, Any] = {
    # ---- what the page is about (short settings; see presets/ for ready-made ones) ----
    "niche": "",          # free text, e.g. "cooking recipes", "football skills", "funny cats"
    "language": "",       # caption language, e.g. English, Hindi, Spanish (empty = caption.language)
    "brand": "",          # optional page / brand name the AI may mention
    "signature": "",      # optional last caption line, e.g. "Follow @me for daily recipes"
    "posts_per_day": 0,   # 0 = use schedule.post_times as written
    "platforms": ["instagram"],   # instagram, youtube
    "preset": "",
    "profile": "",
    "account_env": "",    # per-project secrets file (sessions), set by the START_HERE setup
    "niche_profile": {},  # optional: topic, categories, hook_examples, music, offline templates
    "hashtags": {},       # optional seed hashtags: {broad: {tag: weight}, niche: {...}, categories: {...}}
    "videos_dir": "./videos",
    "work_dir": "./.reeler",
    # graph = official Instagram Graph API (recommended)
    # instagrapi = unofficial private API (works with any account, risk of ban)
    # dry-run = do everything except the actual upload
    "uploader": "graph",
    "order": "name",  # name | oldest | newest | random
    "after_post": "move",  # move | keep | delete
    "posted_dir": "./videos/posted",
    "schedule": {
        "timezone": "local",
        "post_times": ["11:00", "15:00", "19:00", "22:00"],
        "jitter_minutes": 12,
        "max_posts_per_day": 4,
        "min_gap_minutes": 120,
    },
    "video": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "fit": "blur",  # blur | crop | pad
        "max_duration": 90,
        "crf": 20,
        "audio_bitrate": "128k",
        "hook_text": True,
        "hook_seconds": 2.5,
        "font_file": "",  # auto-detect when empty
        "watermark": "",  # e.g. "@yourhandle" (small text, bottom)
        "cover_from_ai": True,
    },
    "caption": {
        "max_hashtags": 5,
        "add_cta": True,
        "signature": "",  # appended line, e.g. "Follow @yourhandle for daily GTA 6 clips"
        "emoji": True,
        "language": "English",
    },
    "ai": {
        "enabled": True,
        "model": "claude-opus-5-5",
        "effort": "low",
        "frames": 6,
        "use_fallbacks": True,
        "niche": "short videos",
    },
    "audio": {
        # trending = attach a trending track from Instagram's Audio API (Facebook Login only)
        # off = keep the clip's own game audio
        "mode": "trending",
        "audio_type": "music",  # music | original_sound
        "audio_volume": 35,  # 1-100, the added track
        "video_volume": 70,  # 1-100, the clip's own audio
        "avoid_repeat_posts": 6,
        "original_audio_name": "",  # optional rename of game audio when mode=off
    },
    "trends": {
        "live_research": True,
        "refresh_hours": 24,
        "weekly_query_budget": 25,  # Instagram allows 30 unique hashtag lookups / 7 days
        "seed_tags": [],
        "seed_file": "",        # optional YAML of seed hashtags (see reeler/data/gta6_tags.yaml)
        "discovery_hints": [],  # words a discovered trending tag must contain (default: niche words)
    },
    "drive": {
        "folder": "",  # Google Drive folder link or ID (shared "Anyone with the link")
        "manifest": "",  # optional committed snapshot of the folder listing
    },
    "youtube": {
        "client_secrets": "client_secret.json",  # OAuth "Desktop app" file from Google Cloud (see README)
        "privacy": "public",          # public | unlisted | private
        "category_id": "22",          # 22 People & Blogs, 20 Gaming, 26 Howto & Style, 17 Sports, 15 Pets
        "made_for_kids": False,
        "notify_subscribers": True,
        "max_seconds": 180,           # Shorts can be up to 3 minutes (vertical or square)
    },
    "graph": {
        "host": "graph.facebook.com",  # graph.instagram.com for Instagram-Login tokens
        "version": "v25.0",
        "share_to_feed": True,
        "poll_seconds": 5,
        "poll_timeout": 600,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class _Section(dict):
    """dict with attribute access (cfg.video.width)."""

    def __getattr__(self, item: str) -> Any:
        try:
            value = self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc
        return _Section(value) if isinstance(value, dict) else value


@dataclass
class Secrets:
    anthropic_api_key: str | None
    ig_user_id: str | None
    ig_access_token: str | None
    fb_app_id: str | None
    fb_app_secret: str | None
    ig_username: str | None
    ig_password: str | None
    google_api_key: str | None = None
    ig_sessionid: str | None = None


class Config(_Section):
    path: Path
    root: Path
    secrets: Secrets

    def resolve(self, value: str) -> Path:
        p = Path(os.path.expanduser(value))
        return p if p.is_absolute() else (self.root / p).resolve()

    @property
    def videos_path(self) -> Path:
        return self.resolve(self["videos_dir"])

    @property
    def work_path(self) -> Path:
        return self.resolve(self["work_dir"])

    @property
    def posted_path(self) -> Path:
        return self.resolve(self["posted_dir"])


def find_preset(name: str, root: Path | None = None) -> Path:
    cand = [Path(name).expanduser()]
    for d in ([root / "presets"] if root else []) + [PRESETS_DIR]:
        cand += [d / name, d / f"{name}.yaml"]
    for c in cand:
        if c.is_file():
            return c
    known = ", ".join(sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))) or "none"
    raise FileNotFoundError(f"Preset '{name}' not found (available: {known})")


def load_preset(name: str, root: Path | None = None) -> dict:
    with open(find_preset(name, root), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def apply_shortcuts(data: dict) -> dict:
    """Map the simple top-level settings onto the detailed sections."""
    from reeler.scheduler import spread_times

    if data.get("niche"):
        data["ai"]["niche"] = data["niche"]
    else:
        data["niche"] = data["ai"]["niche"]
    if data.get("language"):
        data["caption"]["language"] = data["language"]
    if data.get("signature"):
        data["caption"]["signature"] = data["signature"]
    n = int(data.get("posts_per_day") or 0)
    if n > 0:
        times, gap = spread_times(n)
        data["schedule"]["post_times"] = times
        data["schedule"]["max_posts_per_day"] = n
        data["schedule"]["min_gap_minutes"] = gap
    plats = data.get("platforms") or ["instagram"]
    if isinstance(plats, str):
        plats = [p.strip() for p in plats.replace("+", ",").split(",")]
    data["platforms"] = [p.lower() for p in plats if p]
    return data


def load_config(path: str | os.PathLike | None = None, root: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path or os.environ.get("REELER_CONFIG", "config.yaml")).expanduser()
    if root is not None:
        root = Path(root).resolve()
    else:
        root = cfg_path.resolve().parent if cfg_path.exists() else Path.cwd()

    if load_dotenv is not None:
        load_dotenv(root / ".env")

    data: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    base = DEFAULTS
    if data.get("preset"):
        base = _deep_merge(DEFAULTS, load_preset(str(data["preset"]), root))
    cfg = Config(apply_shortcuts(_deep_merge(base, data)))
    cfg.path = cfg_path
    cfg.root = root
    env = os.environ.get
    acct: dict = dict(os.environ)
    if cfg.get("account_env"):
        # per-project account secrets: only from that project's file
        acct_file = cfg.resolve(cfg["account_env"])
        acct = {k: v for k, v in os.environ.items() if k not in ACCOUNT_KEYS}
        if acct_file.exists() and dotenv_values is not None:
            acct.update({k: v for k, v in dotenv_values(acct_file).items() if v})
    a = acct.get
    cfg.secrets = Secrets(
        anthropic_api_key=env("ANTHROPIC_API_KEY"),
        ig_user_id=a("IG_USER_ID"),
        ig_access_token=a("IG_ACCESS_TOKEN"),
        fb_app_id=env("FB_APP_ID"),
        fb_app_secret=env("FB_APP_SECRET"),
        ig_username=a("IG_USERNAME"),
        ig_password=a("IG_PASSWORD"),
        google_api_key=env("GOOGLE_API_KEY"),
        ig_sessionid=a("IG_SESSIONID"),
    )
    return cfg


def secrets_file(cfg) -> Path:
    """Where account secrets (e.g. a captured Instagram session) are saved for this project."""
    return cfg.resolve(cfg["account_env"]) if cfg.get("account_env") else cfg.root / ".env"


def save_secret(cfg, key: str, value: str) -> None:
    env = secrets_file(cfg)
    env.parent.mkdir(parents=True, exist_ok=True)
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")] + [f"{key}={value}"]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
