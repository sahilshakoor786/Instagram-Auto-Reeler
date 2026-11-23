"""Configuration loading: config.yaml (settings) + .env (secrets)."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional
    load_dotenv = None


DEFAULTS: dict[str, Any] = {
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
        "niche": "GTA 6 (Grand Theft Auto VI) gameplay clips",
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
        "seed_tags": ["gta6", "gtavi", "grandtheftauto6", "vicecity", "gta6gameplay"],
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


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path or os.environ.get("REELER_CONFIG", "config.yaml")).expanduser()
    root = cfg_path.resolve().parent if cfg_path.exists() else Path.cwd()

    if load_dotenv is not None:
        load_dotenv(root / ".env")

    data: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    cfg = Config(_deep_merge(DEFAULTS, data))
    cfg.path = cfg_path
    cfg.root = root
    env = os.environ.get
    cfg.secrets = Secrets(
        anthropic_api_key=env("ANTHROPIC_API_KEY"),
        ig_user_id=env("IG_USER_ID"),
        ig_access_token=env("IG_ACCESS_TOKEN"),
        fb_app_id=env("FB_APP_ID"),
        fb_app_secret=env("FB_APP_SECRET"),
        ig_username=env("IG_USERNAME"),
        ig_password=env("IG_PASSWORD"),
    )
    return cfg
