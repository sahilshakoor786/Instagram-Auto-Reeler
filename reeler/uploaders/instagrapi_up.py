"""Uploader that logs in like the Instagram app (instagrapi). No Facebook / Meta developer app needed.

Pros: works with any account, can attach real trending Instagram music.
Cons: unofficial - Instagram may ask for a verification code or, if abused, restrict the account.
Keep the pace human (a few Reels a day) and run it from your own PC/home internet.
"""

from __future__ import annotations

import getpass
import os
import logging
import random
from pathlib import Path

from reeler.uploaders.base import UploadResult, Uploader

log = logging.getLogger(__name__)


def _ask(prompt: str) -> str:
    print(prompt, end="", flush=True)
    return input().strip()


SESSIONID_HELP = """
==================== Log in using your browser ====================
1. Open instagram.com in Chrome / Brave / Edge and make sure you are logged in.
2. Press F12 -> open the "Application" tab (Chrome/Brave/Edge) or "Storage" (Firefox).
3. Left side: Cookies -> https://www.instagram.com
4. Find the row named  sessionid  and copy its Value (double-click it, Ctrl+C).
   Keep it private - it works like your password. Don't log out on the browser after this.
====================================================================
"""


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
        self.on_new_sessionid = None  # callback to persist a pasted sessionid

    # ---- login ---------------------------------------------------------------
    # Instagram retired the old app password-login endpoint (it now answers "your version of Instagram is
    # out of date"). Reusing the login from your web browser (the `sessionid` cookie) avoids that endpoint.
    APP_VERSION = {"app_version": "456.0.0.53.78", "version_code": "386214528"}

    def _bump_app_version(self) -> None:
        try:
            self.cl.set_device({**self.cl.device_settings, **self.APP_VERSION})
            self.cl.set_user_agent()
        except Exception as exc:  # noqa: BLE001 - optional
            log.debug("could not bump app version: %s", exc)

    def _login_sessionid(self, sessionid: str) -> None:
        from urllib.parse import unquote

        sid = unquote(sessionid.strip().strip('"').strip("'"))
        self._bump_app_version()
        self.cl.login_by_sessionid(sid)
        self.cl.dump_settings(self.session_file)
        self._logged_in = True

    def _login(self) -> None:
        if self._logged_in:
            return
        from instagrapi.exceptions import TwoFactorRequired

        s = self.cfg.secrets
        self.session_file.parent.mkdir(parents=True, exist_ok=True)

        # 1. saved session from a previous run
        if self.session_file.exists():
            try:
                self.cl.load_settings(self.session_file)
                self._bump_app_version()
                self.cl.account_info()
                self._logged_in = True
                return
            except Exception as exc:  # noqa: BLE001
                log.info("Saved session not usable (%s)", exc)
                self.cl = type(self.cl)()
                self.cl.delay_range = [2, 5]

        # 2. browser session id (recommended)
        if s.ig_sessionid:
            try:
                self._login_sessionid(s.ig_sessionid)
                return
            except Exception as exc:  # noqa: BLE001
                print(f"\nThe saved browser session did not work ({exc}).")

        # 3. username + password - off by default: Instagram currently refuses it and repeated
        #    failed logins can lock the account. Enable with IG_TRY_PASSWORD=1 in .env.
        if s.ig_username and s.ig_password and os.environ.get("IG_TRY_PASSWORD") == "1":
            try:
                self._bump_app_version()
                try:
                    self.cl.login(s.ig_username, s.ig_password)
                except TwoFactorRequired:
                    code = _ask("Two-factor code (from SMS / authenticator app): ")
                    self.cl.login(s.ig_username, s.ig_password, verification_code=code)
                self.cl.dump_settings(self.session_file)
                self._logged_in = True
                return
            except Exception as exc:  # noqa: BLE001
                print(f"\nPassword login was refused by Instagram ({exc}).")

        # 4. ask for the browser session id
        print(SESSIONID_HELP)
        for attempt in range(3):
            sid = getpass.getpass("Paste your sessionid here (hidden) and press Enter: ")
            try:
                self._login_sessionid(sid)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"That sessionid did not work ({exc}). Copy the whole Value and try again.")
                self.cl = type(self.cl)()
                self.cl.delay_range = [2, 5]
        else:
            raise RuntimeError("Could not log in to Instagram with the browser session.")
        s.ig_sessionid = sid
        if self.on_new_sessionid:
            self.on_new_sessionid(sid)

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
