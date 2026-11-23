"""Google Drive source: list a link-shared folder and download clips one at a time (no Google login).

Listing strategies (results are merged):
  1. Drive API v3 with an API key (GOOGLE_API_KEY) - official, sees new uploads.
  2. The public "embedded folder view" page - no key needed, sees new uploads.
  3. A committed manifest (drive_manifest.json) - always works, frozen snapshot.
The folder (or its files) must be shared as "Anyone with the link".
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

import requests

log = logging.getLogger(__name__)

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) reeler/1.0"}


def folder_id_from(value: str) -> str:
    m = re.search(r"folders/([A-Za-z0-9_-]{10,})", value or "") or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", value or "")
    return m.group(1) if m else (value or "").strip()


def _list_api(folder_id: str, api_key: str, session: requests.Session) -> list[dict]:
    out, token = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken, files(id,name,size,mimeType)",
            "pageSize": 1000, "key": api_key,
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }
        if token:
            params["pageToken"] = token
        r = session.get("https://www.googleapis.com/drive/v3/files", params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        for f in data.get("files", []):
            if f.get("mimeType", "").startswith("video/") or f["name"].lower().endswith(VIDEO_EXT):
                out.append({"id": f["id"], "name": f["name"], "size": int(f.get("size") or 0)})
        token = data.get("nextPageToken")
        if not token:
            return out


def _list_embedded(folder_id: str, session: requests.Session) -> list[dict]:
    r = session.get("https://drive.google.com/embeddedfolderview", params={"id": folder_id}, headers=UA, timeout=60)
    r.raise_for_status()
    out = []
    for m in re.finditer(r'id="entry-([A-Za-z0-9_-]{10,})".*?class="flip-entry-title">(.*?)</div>', r.text, re.S):
        name = html.unescape(m.group(2)).strip()
        if name.lower().endswith(VIDEO_EXT):
            out.append({"id": m.group(1), "name": name, "size": 0})
    return out


def list_folder(folder_id: str, *, api_key: str | None = None, manifest: Path | None = None,
                session: requests.Session | None = None) -> list[dict]:
    session = session or requests.Session()
    found: dict[str, dict] = {}
    if manifest and manifest.exists():
        for f in json.loads(manifest.read_text(encoding="utf-8")):
            found[f["id"]] = f
    sources = []
    if api_key:
        sources.append(("api", lambda: _list_api(folder_id, api_key, session)))
    sources.append(("embedded", lambda: _list_embedded(folder_id, session)))
    for name, fn in sources:
        try:
            items = fn()
        except Exception as exc:  # noqa: BLE001 - any listing failure just falls back
            log.warning("Drive listing via %s failed: %s", name, exc)
            continue
        new = [f for f in items if f["id"] not in found]
        for f in items:
            found.setdefault(f["id"], f)
        log.info("Drive listing via %s: %d files (%d not in manifest)", name, len(items), len(new))
        if name == "api":
            break  # the API sees everything; no need to scrape
    return sorted(found.values(), key=lambda f: f["name"])


def download(file_id: str, dest: Path, session: requests.Session | None = None) -> Path:
    """Download a link-shared Drive file, handling the large-file confirmation page."""
    session = session or requests.Session()
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = "https://drive.usercontent.google.com/download"
    params = {"id": file_id, "export": "download", "confirm": "t"}
    for _ in range(3):
        r = session.get(url, params=params, headers=UA, stream=True, timeout=300)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "text/html" not in ctype:
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
            tmp.replace(dest)
            if dest.stat().st_size < 1024:
                raise RuntimeError(f"Drive returned a tiny file for {file_id} - is it shared 'Anyone with the link'?")
            return dest
        page = r.text
        if "accounts.google.com" in page or "ServiceLogin" in page:
            raise RuntimeError(f"Drive file {file_id} is not public - share the folder as 'Anyone with the link'")
        action = re.search(r'<form[^>]+action="([^"]+)"', page)
        fields = dict(re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', page))
        if not action or not fields:
            raise RuntimeError(f"Unexpected Drive response for {file_id} (quota exceeded or not shared?)")
        url, params = html.unescape(action.group(1)), fields
    raise RuntimeError(f"Could not download Drive file {file_id}")
