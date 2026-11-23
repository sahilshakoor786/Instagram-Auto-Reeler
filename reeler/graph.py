"""Thin client for the official Instagram Graph API (Facebook Login or Instagram Login tokens)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests


class GraphError(RuntimeError):
    def __init__(self, message: str, payload: dict | None = None, status: int | None = None):
        super().__init__(message)
        self.payload = payload or {}
        self.status = status

    @property
    def code(self) -> int | None:
        return (self.payload.get("error") or {}).get("code")

    @property
    def subcode(self) -> int | None:
        return (self.payload.get("error") or {}).get("error_subcode")


class GraphClient:
    def __init__(self, ig_user_id: str, access_token: str, host: str = "graph.facebook.com",
                 version: str = "v25.0", timeout: int = 60):
        if not ig_user_id or not access_token:
            raise GraphError("IG_USER_ID and IG_ACCESS_TOKEN must be set in .env for the Graph API uploader")
        self.user_id = ig_user_id.strip()
        self.token = access_token.strip()
        # Tokens from "Instagram Login" (no Facebook Page needed) start with "IG" and only work on
        # graph.instagram.com; Facebook-Login tokens start with "EAA". Pick the right host automatically.
        if self.token.startswith("IG"):
            host = "graph.instagram.com"
        elif self.token.startswith("EAA"):
            host = "graph.facebook.com"
        self.host = host
        self.version = version
        self.timeout = timeout
        self.session = requests.Session()

    @property
    def base(self) -> str:
        return f"https://{self.host}/{self.version}"

    def _handle(self, resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:500]}
        if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            err = data.get("error", {}) if isinstance(data, dict) else {}
            msg = err.get("error_user_msg") or err.get("message") or resp.text[:300]
            raise GraphError(f"Graph API {resp.status_code}: {msg}", data, resp.status_code)
        return data

    def get(self, path: str, **params: Any) -> dict:
        params["access_token"] = self.token
        resp = self.session.get(f"{self.base}/{path.lstrip('/')}", params=params, timeout=self.timeout)
        return self._handle(resp)

    def post(self, path: str, **params: Any) -> dict:
        params["access_token"] = self.token
        resp = self.session.post(f"{self.base}/{path.lstrip('/')}", data=params, timeout=self.timeout)
        return self._handle(resp)

    # ---- account --------------------------------------------------------
    def me(self) -> dict:
        return self.get(self.user_id, fields="id,username,followers_count,media_count")

    def publishing_limit(self) -> dict:
        data = self.get(f"{self.user_id}/content_publishing_limit", fields="quota_usage,config")
        return (data.get("data") or [{}])[0]

    # ---- reels publishing (resumable upload) ------------------------------
    def create_reel_container(self, caption: str, *, share_to_feed: bool = True,
                              thumb_offset_ms: int | None = None,
                              audio_configuration: dict | None = None,
                              audio_name: str | None = None) -> dict:
        params: dict[str, Any] = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
        }
        if thumb_offset_ms is not None:
            params["thumb_offset"] = int(thumb_offset_ms)
        if audio_configuration:
            params["audio_configuration"] = json.dumps(audio_configuration)
        elif audio_name:
            params["audio_name"] = audio_name
        return self.post(f"{self.user_id}/media", **params)

    def upload_video(self, container_id: str, video: Path, upload_uri: str | None = None) -> dict:
        url = upload_uri or f"https://rupload.facebook.com/ig-api-upload/{self.version}/{container_id}"
        size = video.stat().st_size
        headers = {
            "Authorization": f"OAuth {self.token}",
            "offset": "0",
            "file_size": str(size),
            "Content-Type": "application/octet-stream",
        }
        with open(video, "rb") as fh:
            resp = self.session.post(url, headers=headers, data=fh, timeout=max(self.timeout, 600))
        return self._handle(resp)

    def container_status(self, container_id: str) -> dict:
        return self.get(container_id, fields="status_code,status")

    def wait_until_ready(self, container_id: str, poll: float = 5, timeout: float = 600) -> dict:
        deadline = time.time() + timeout
        while True:
            st = self.container_status(container_id)
            code = st.get("status_code")
            if code == "FINISHED":
                return st
            if code in ("ERROR", "EXPIRED"):
                raise GraphError(f"Container {container_id} {code}: {st.get('status')}", st)
            if time.time() > deadline:
                raise GraphError(f"Timed out waiting for container {container_id} (last: {code})", st)
            time.sleep(poll)

    def publish(self, container_id: str) -> str:
        last: GraphError | None = None
        for attempt in range(5):
            try:
                return self.post(f"{self.user_id}/media_publish", creation_id=container_id)["id"]
            except GraphError as exc:  # media not ready yet (9007) is retryable
                last = exc
                if exc.code in (9007, 2, 4) or (exc.status and exc.status >= 500):
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise last  # type: ignore[misc]

    def permalink(self, media_id: str) -> str | None:
        try:
            return self.get(media_id, fields="permalink").get("permalink")
        except GraphError:
            return None

    # ---- trends research ---------------------------------------------------
    def hashtag_id(self, tag: str) -> str | None:
        data = self.get("ig_hashtag_search", user_id=self.user_id, q=tag)
        items = data.get("data") or []
        return items[0]["id"] if items else None

    def hashtag_media(self, hashtag_id: str, edge: str = "top_media", limit: int = 50) -> list[dict]:
        data = self.get(
            f"{hashtag_id}/{edge}", user_id=self.user_id, limit=limit,
            fields="id,caption,like_count,comments_count,timestamp,media_type,media_product_type",
        )
        return data.get("data") or []

    # ---- Instagram Audio API (Facebook Login apps) -------------------------
    def search_audio(self, audio_type: str = "music", query: str | None = None, limit: int = 25) -> list[dict]:
        params: dict[str, Any] = {"user_id": self.user_id, "audio_type": audio_type, "limit": limit}
        if query:
            params["search_query"] = query
        data = self.get("ig_audio", **params)
        items = data.get("audio") or data.get("data") or []
        return [i for i in items if isinstance(i, dict)]

    # ---- token maintenance -------------------------------------------------
    def exchange_long_lived(self, app_id: str, app_secret: str) -> dict:
        if self.host == "graph.instagram.com":
            resp = self.session.get(
                f"https://{self.host}/refresh_access_token",
                params={"grant_type": "ig_refresh_token", "access_token": self.token},
                timeout=self.timeout,
            )
        else:
            resp = self.session.get(
                f"{self.base}/oauth/access_token",
                params={"grant_type": "fb_exchange_token", "client_id": app_id,
                        "client_secret": app_secret, "fb_exchange_token": self.token},
                timeout=self.timeout,
            )
        return self._handle(resp)
