"""Uploader that logs in like the Instagram app (instagrapi). No Facebook / Meta developer app needed.

Pros: works with any account, can attach real trending Instagram music.
Cons: unofficial - Instagram may ask for a verification code or, if abused, restrict the account.
Keep the pace human (a few Reels a day) and run it from your own PC/home internet.
"""

from __future__ import annotations

import getpass
import logging
import random
from pathlib import Path

from reeler.uploaders.base import UploadResult, Uploader

log = logging.getLogger(__name__)


def _ask(prompt: str) -> str:
    print(prompt, end="", flush=True)
    return input().strip()


class InstagrapiUploader(Uploader):
    name = "instagrapi"
    supports_trending_audio = True

    def __init__(self, cfg):
        super().__init__(cfg)
        try:
            from instagrapi import Client
        except ImportError as exc:
            raise RuntimeError('Run: pip install "instagrapi[video]" && pip install --no-deps moviepy==2.2.1') from exc
        self.session_file = cfg.work_path / "instagram.session.json"
        self.cl = Client()
        self.cl.delay_range = [2, 5]
        self.cl.challenge_code_handler = lambda username, choice: _ask(
            f"\nInstagram sent a security code to your {getattr(choice, 'name', choice)}. Type it here: ")
        self._logged_in = False

    # ---- login ---------------------------------------------------------------
    def _login(self) -> None:
        if self._logged_in:
            return
        from instagrapi.exceptions import LoginRequired, TwoFactorRequired

        s = self.cfg.secrets
        username = s.ig_username or _ask("Instagram username: ")
        password = s.ig_password or getpass.getpass("Instagram password (hidden while typing): ")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        if self.session_file.exists():
            try:
                self.cl.load_settings(self.session_file)
                self.cl.login(username, password)
                self.cl.get_timeline_feed()  # session still valid?
                self._logged_in = True
                self.cl.dump_settings(self.session_file)
                return
            except LoginRequired:
                log.info("Saved session expired, logging in again")
                old = self.cl.get_settings()
                self.cl.set_settings({})
                self.cl.set_uuids(old.get("uuids", {}))
            except Exception as exc:  # noqa: BLE001
                log.info("Saved session not usable (%s), logging in again", exc)
        try:
            self.cl.login(username, password)
        except TwoFactorRequired:
            code = _ask("Two-factor code (from SMS / authenticator app): ")
            self.cl.login(username, password, verification_code=code)
        self.cl.dump_settings(self.session_file)
        self._logged_in = True

    def check(self) -> str:
        self._login()
        info = self.cl.account_info()
        return f"@{info.username}"

    # ---- trending music ------------------------------------------------------
    def pick_audio(self, search_terms: list[str], recently_used: set[str]):
        """Return (choice_dict, Track) for a popular track matching the clip's vibe, or (None, None)."""
        self._login()
        from instagrapi.types import Track

        candidates: list[Track] = []
        try:
            raw = self.cl.music_trending()
            candidates += [t for t in _tracks_in(raw, Track)]
        except Exception as exc:  # noqa: BLE001
            log.info("trending music list unavailable: %s", exc)
        terms = [t for t in (search_terms or []) if t][:3] or ["phonk", "trending"]
        for term in terms:
            try:
                candidates += self.cl.search_music(term)[:8]
            except Exception as exc:  # noqa: BLE001
                log.info("music search '%s' failed: %s", term, exc)
        fresh = [t for t in candidates if str(t.id) not in recently_used and t.uri and not t.is_explicit]
        if not fresh:
            return None, None
        # trending first, but keep some variety
        track = random.choice(fresh[: max(3, len(fresh) // 3)])
        label = " - ".join(x for x in (track.title, track.display_artist) if x)
        return {"audio_id": str(track.id), "label": label or str(track.id)}, track

    # ---- upload ----------------------------------------------------------------
    def upload(self, video: Path, caption: str, *, cover=None, thumb_offset_ms=None, audio=None) -> UploadResult:
        self._login()
        notes: list[str] = []
        track = audio.pop("_track", None) if isinstance(audio, dict) else None
        media = None
        if track is not None:
            try:
                media = self.cl.clip_upload_as_reel_with_music(Path(video), caption, track)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"music attach failed ({exc}); posted with game audio")
                log.warning("Upload with music failed (%s) - posting with original audio", exc)
                audio = None
        if media is None:
            media = self.cl.clip_upload(Path(video), caption, thumbnail=Path(cover) if cover else None)
        link = f"https://www.instagram.com/reel/{media.code}/" if getattr(media, "code", None) else None
        return UploadResult(media_id=str(media.pk), permalink=link, audio_used=audio if (track is not None and not notes) else None,
                            notes=notes)


def _tracks_in(obj, Track, depth: int = 0):
    """Walk a raw music/trending response and yield anything that parses as a Track."""
    if depth > 6:
        return
    if isinstance(obj, dict):
        if obj.get("uri") or obj.get("progressive_download_url"):
            try:
                data = dict(obj)
                data.setdefault("uri", data.get("progressive_download_url"))
                data.setdefault("id", data.get("audio_asset_id") or data.get("audio_cluster_id"))
                defaults = {"title": "", "subtitle": "", "display_artist": "", "highlight_start_times_in_ms": [],
                            "is_explicit": False, "dash_manifest": "", "has_lyrics": False, "allows_saving": True,
                            "duration_in_ms": 0, "territory_validity_periods": {}}
                for k, v in defaults.items():
                    if data.get(k) is None:
                        data[k] = v
                data.setdefault("audio_cluster_id", data["id"])
                data.setdefault("audio_asset_id", data["id"])
                yield Track(**data)
                return
            except Exception:  # noqa: BLE001
                pass
        for v in obj.values():
            yield from _tracks_in(v, Track, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _tracks_in(v, Track, depth + 1)
