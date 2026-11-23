"""Unofficial uploader using instagrapi (Instagram private API).

Works with personal accounts and needs no Meta app, BUT it is against Instagram's terms and can
trigger checkpoints or bans. Prefer the official `graph` uploader. Trending audio is not supported here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from reeler.uploaders.base import UploadResult, Uploader

log = logging.getLogger(__name__)


class InstagrapiUploader(Uploader):
    name = "instagrapi"

    def __init__(self, cfg):
        super().__init__(cfg)
        try:
            from instagrapi import Client
        except ImportError as exc:
            raise RuntimeError("pip install instagrapi  (needed for uploader: instagrapi)") from exc
        self.session_file = cfg.work_path / "instagrapi.session.json"
        self.cl = Client()
        self.cl.delay_range = [2, 6]
        self._logged_in = False

    def _login(self) -> None:
        if self._logged_in:
            return
        s = self.cfg.secrets
        if not s.ig_username or not s.ig_password:
            raise RuntimeError("Set IG_USERNAME and IG_PASSWORD in .env for uploader: instagrapi")
        if self.session_file.exists():
            self.cl.load_settings(self.session_file)
        self.cl.login(s.ig_username, s.ig_password)
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.cl.dump_settings(self.session_file)
        self._logged_in = True

    def check(self) -> str:
        self._login()
        info = self.cl.account_info()
        return f"@{info.username} (instagrapi, unofficial)"

    def upload(self, video: Path, caption: str, *, cover=None, thumb_offset_ms=None, audio=None) -> UploadResult:
        self._login()
        media = self.cl.clip_upload(Path(video), caption, thumbnail=Path(cover) if cover else None)
        link = f"https://www.instagram.com/reel/{media.code}/" if getattr(media, "code", None) else None
        return UploadResult(media_id=str(media.pk), permalink=link, audio_used=None,
                            notes=["trending audio not supported by instagrapi uploader"] if audio else [])
