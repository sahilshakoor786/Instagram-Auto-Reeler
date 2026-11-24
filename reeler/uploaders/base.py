from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class UploadResult:
    media_id: str
    permalink: str | None = None
    audio_used: dict | None = None
    notes: list[str] = field(default_factory=list)


class Uploader:
    name = "base"
    platform = "instagram"
    supports_trending_audio = False

    def __init__(self, cfg):
        self.cfg = cfg

    def check(self) -> str:
        """Verify credentials; return a human readable account description."""
        raise NotImplementedError

    def upload(self, video: Path, caption: str, *, cover: Path | None = None,
               thumb_offset_ms: int | None = None, audio: dict | None = None) -> UploadResult:
        raise NotImplementedError


class DryRunUploader(Uploader):
    name = "dry-run"
    supports_trending_audio = False

    def __init__(self, cfg, platform: str = "instagram"):
        super().__init__(cfg)
        self.platform = platform

    def check(self) -> str:
        return f"dry-run for {self.platform} (nothing is uploaded)"

    def upload(self, video, caption, *, cover=None, thumb_offset_ms=None, audio=None, meta=None) -> UploadResult:
        return UploadResult(media_id=f"dryrun-{int(time.time())}", permalink=None, audio_used=audio,
                            notes=["dry-run: not uploaded"])
