"""YouTube Shorts uploader - official YouTube Data API v3 (videos.insert), OAuth "Desktop app" sign-in.

A vertical (or square) video up to 3 minutes long is shown as a Short automatically; "#Shorts" in the
title/description is added as well. Sign-in happens once in your browser; the token is stored in the
project's work folder (never in chat, never in the repo).

Google rule to know: videos uploaded through the API by a Google Cloud project that has NOT passed
YouTube's (free) API compliance audit stay PRIVATE. See README -> "Turn on YouTube Shorts".
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from reeler.uploaders.base import UploadResult, Uploader

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
TITLE_MAX = 100
DESC_MAX = 5000
TAGS_MAX_CHARS = 450  # YouTube allows 500 characters for all tags together

SETUP_HELP = """
YouTube is not connected yet: the file '{file}' is missing.
Follow README -> "Turn on YouTube Shorts" (about 10 minutes, one time), put client_secret.json
in the project folder, then start again.
"""


CLOUD_HELP = """
No working YouTube login in the cloud. On your PC, open .reeler\\<project>\\youtube_token.json with
Notepad, copy everything, and paste it into GitHub -> Settings -> Secrets and variables -> Actions ->
YOUTUBE_TOKEN_JSON. (If it stopped working after ~7 days: publish the app in Google Cloud, see README.)
"""


CONNECT_TRIES = 4


def _connection_problem(exc: BaseException) -> bool:
    """Dropped / reset / timed-out connections (worth a retry on a fresh connection)."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10053, 10054, 10060):
        return True
    name = type(exc).__name__.lower()
    return any(k in name for k in ("timeout", "connection", "remotedisconnected", "brokenpipe", "servernotfound"))


def _clean(text: str) -> str:
    return re.sub(r"[<>]", "", text or "").strip()


def youtube_metadata(analysis: dict, caption: str, hashtags: list[str], cfg) -> dict:
    """Title / description / tags for a Short, from the same caption used on Instagram."""
    raw_title = analysis.get("title") or analysis.get("caption") or (analysis.get("hook_text") or "").title()
    raw_title = re.sub(r"#\w+", "", raw_title.split("\n")[0])
    suffix = " #Shorts"
    title = _clean(raw_title)[: TITLE_MAX - len(suffix)].rstrip() or "New Short"
    title += suffix
    desc = _clean(caption)
    if "#shorts" not in desc.lower():
        desc += "\n\n#Shorts"
    tags: list[str] = []
    total = 0
    for t in list(hashtags) + ["shorts"]:
        t = t.lstrip("#")
        if t and t not in tags and total + len(t) + 1 <= TAGS_MAX_CHARS:
            tags.append(t)
            total += len(t) + 1
    return {"title": title, "description": desc[:DESC_MAX], "tags": tags,
            "category_id": str(cfg.youtube.category_id)}


def find_client_secrets(cfg) -> Path:
    """client_secret.json - or the file exactly as Google names it when downloaded
    (client_secret_1234-abc.apps.googleusercontent.com.json, sometimes with an extra .json)."""
    wanted = cfg.resolve(cfg.youtube.client_secrets)
    if wanted.exists():
        return wanted
    found = sorted(p for p in cfg.root.glob("client_secret*.json*") if p.is_file())
    return found[0] if found else wanted


class YouTubeUploader(Uploader):
    name = "youtube"
    platform = "youtube"
    supports_trending_audio = False

    def __init__(self, cfg, service=None):
        super().__init__(cfg)
        self.token_file = cfg.work_path / "youtube_token.json"
        self.secrets_file = find_client_secrets(cfg)
        self._service = service
        self._injected = service is not None  # tests pass a fake service; keep it

    # ---- sign-in -------------------------------------------------------------
    def _credentials(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise RuntimeError("Run: pip install google-api-python-client google-auth-oauthlib") from exc

        creds = None
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
            except Exception as exc:  # noqa: BLE001
                log.info("saved YouTube sign-in unusable (%s)", exc)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save(creds)
                return creds
            except Exception as exc:  # noqa: BLE001
                log.info("YouTube sign-in expired (%s) - signing in again", exc)
        if os.environ.get("CI"):  # cloud run: nobody can click "Allow" in a browser here
            raise RuntimeError(CLOUD_HELP)
        if not self.secrets_file.exists():
            raise RuntimeError(SETUP_HELP.format(file=self.secrets_file.name))
        print("\nA browser window is opening: sign in to Google and choose the YouTube channel to post to.")
        print("(If Google says the app isn't verified: click 'Advanced' -> 'Go to ... (unsafe)'. It is your own app.)\n")
        flow = InstalledAppFlow.from_client_secrets_file(str(self.secrets_file), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        self._save(creds)
        print("YouTube connected.")
        return creds

    def _save(self, creds) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(creds.to_json(), encoding="utf-8")

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build
            self._service = build("youtube", "v3", credentials=self._credentials(), cache_discovery=False)
        return self._service

    def check(self) -> str:
        resp = self.service.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items") or []
        if not items:
            raise RuntimeError("This Google account has no YouTube channel yet - create one on youtube.com first")
        return f"YouTube channel '{items[0]['snippet']['title']}'"

    # ---- upload --------------------------------------------------------------
    def _media(self, video: Path):
        from googleapiclient.http import MediaFileUpload
        return MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)

    def _insert(self, video: Path, body: dict, yt) -> dict:
        media = self._media(video)
        try:
            request = self.service.videos().insert(part="snippet,status", body=body, media_body=media,
                                                   notifySubscribers=bool(yt.notify_subscribers))
            response = None
            tries = 0
            while response is None:
                try:
                    _, response = request.next_chunk()
                except Exception as exc:  # noqa: BLE001 - retry server hiccups only
                    status = getattr(getattr(exc, "resp", None), "status", None)
                    tries += 1
                    if status not in (500, 502, 503, 504) or tries > 5:
                        raise
                    time.sleep(2 ** tries)
            return response
        finally:
            fd = getattr(media, "_fd", None)  # release the file so Windows can delete it afterwards
            if fd is not None:
                try:
                    fd.close()
                except OSError:
                    pass

    def upload(self, video, caption, *, cover=None, thumb_offset_ms=None, audio=None, meta=None) -> UploadResult:
        yt = self.cfg.youtube
        meta = meta or youtube_metadata({"caption": caption}, caption, [], self.cfg)
        privacy = str(yt.privacy or "public").lower()
        body = {
            "snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"],
                        "categoryId": meta.get("category_id") or str(yt.category_id)},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(yt.made_for_kids)},
        }
        response = None
        for attempt in range(1, CONNECT_TRIES + 1):
            if not self._injected:
                self._service = None  # fresh connection every upload: idle ones get cut off (WinError 10053)
            try:
                response = self._insert(Path(video), body, yt)
                break
            except Exception as exc:  # noqa: BLE001
                if not _connection_problem(exc) or attempt == CONNECT_TRIES:
                    raise
                log.warning("YouTube connection dropped (%s) - retrying (%d/%d)", exc, attempt, CONNECT_TRIES)
                time.sleep(10 * attempt)
        vid = response["id"]
        notes = []
        got = ((response.get("status") or {}).get("privacyStatus") or privacy).lower()
        if got != privacy:
            notes.append(f"YouTube set this Short to '{got}' (Google keeps API uploads private until the "
                         "Google project passes the free audit - see README)")
        return UploadResult(media_id=vid, permalink=f"https://youtube.com/shorts/{vid}", notes=notes)
