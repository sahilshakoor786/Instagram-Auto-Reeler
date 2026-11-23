"""Official Instagram Graph API uploader (resumable Reels upload, no public video URL needed)."""

from __future__ import annotations

import logging
from pathlib import Path

from reeler.audio import audio_configuration
from reeler.graph import GraphClient, GraphError
from reeler.uploaders.base import UploadResult, Uploader

log = logging.getLogger(__name__)


class GraphUploader(Uploader):
    name = "graph"

    def __init__(self, cfg):
        super().__init__(cfg)
        g = cfg.graph
        self.client = GraphClient(cfg.secrets.ig_user_id, cfg.secrets.ig_access_token,
                                  host=g.host, version=g.version)
        self.supports_trending_audio = self.client.host == "graph.facebook.com"

    def check(self) -> str:
        me = self.client.me()
        try:
            lim = self.client.publishing_limit()
            quota = f", publishing quota used {lim.get('quota_usage', '?')}/{(lim.get('config') or {}).get('quota_total', 100)} (24h)"
        except GraphError:
            quota = ""
        return f"@{me.get('username')} ({me.get('followers_count', '?')} followers){quota}"

    def _attempt(self, video: Path, caption: str, thumb_offset_ms, audio_conf, audio_name) -> str:
        g = self.cfg.graph
        container = self.client.create_reel_container(
            caption, share_to_feed=bool(g.share_to_feed), thumb_offset_ms=thumb_offset_ms,
            audio_configuration=audio_conf, audio_name=audio_name,
        )
        cid = container["id"]
        log.info("container %s created, uploading %.1f MB", cid, video.stat().st_size / 1e6)
        self.client.upload_video(cid, video, container.get("uri"))
        self.client.wait_until_ready(cid, poll=g.poll_seconds, timeout=g.poll_timeout)
        return self.client.publish(cid)

    def upload(self, video, caption, *, cover=None, thumb_offset_ms=None, audio=None) -> UploadResult:
        notes: list[str] = []
        audio_conf = audio_configuration(audio, self.cfg) if self.supports_trending_audio else None
        audio_name = self.cfg.audio.original_audio_name or None
        try:
            media_id = self._attempt(video, caption, thumb_offset_ms, audio_conf, audio_name)
        except GraphError as exc:
            if not audio_conf:
                raise
            notes.append(f"trending audio rejected ({exc}); reposted with original audio")
            log.warning("Upload with trending audio failed (%s) - retrying with the clip's own audio", exc)
            audio, audio_conf = None, None
            media_id = self._attempt(video, caption, thumb_offset_ms, None, audio_name)
        return UploadResult(media_id=media_id, permalink=self.client.permalink(media_id),
                            audio_used=audio if audio_conf else None, notes=notes)
