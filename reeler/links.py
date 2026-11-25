"""A posting queue made of links (one per line in a text file, e.g. queue/links.txt).

Each line can be:
  * a Google Drive FILE link            -> that video
  * a Google Drive FOLDER link          -> every video in the folder (new ones are picked up too)
  * a Dropbox link or a direct .mp4 link
  * a video page (YouTube, TikTok, Instagram, ...) -> downloaded with yt-dlp (best effort)
Lines starting with # are ignored. Only post videos you own or have permission to use.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse, urlencode

import requests

from reeler import gdrive

log = logging.getLogger(__name__)

DIRECT_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def read_links(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in out:
            out.append(line)
    return out


def drive_file_id(url: str) -> str | None:
    m = re.search(r"drive\.google\.com/file/d/([A-Za-z0-9_-]{10,})", url) \
        or re.search(r"drive\.google\.com/(?:open|uc)\?(?:.*&)?id=([A-Za-z0-9_-]{10,})", url) \
        or re.search(r"drive\.usercontent\.google\.com/.*[?&]id=([A-Za-z0-9_-]{10,})", url)
    return m.group(1) if m else None


def classify(url: str) -> str:
    u = url.lower()
    if "drive.google.com" in u and "/folders/" in u:
        return "drive_folder"
    if drive_file_id(url):
        return "drive_file"
    if "dropbox.com" in u:
        return "dropbox"
    if urlparse(u).path.endswith(DIRECT_EXT):
        return "direct"
    return "site"


def link_id(url: str) -> str:
    return "L" + hashlib.sha1(url.strip().encode()).hexdigest()[:16]


def expand(urls: list[str], *, api_key: str | None = None) -> list[dict]:
    """Turn the link list into postable items: {id, name, url, kind}. Order is kept."""
    items: list[dict] = []
    seen: set[str] = set()

    def add(item: dict) -> None:
        if item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)

    for url in urls:
        kind = classify(url)
        if kind == "drive_folder":
            try:
                files = gdrive.list_folder(gdrive.folder_id_from(url), api_key=api_key)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not list Drive folder %s: %s", url, exc)
                continue
            for f in files:  # same id as Drive-folder mode, so history is shared
                add({"id": f["id"], "name": f["name"], "url": url, "kind": "drive_file"})
        elif kind == "drive_file":
            fid = drive_file_id(url)
            add({"id": fid, "name": f"{fid}.mp4", "url": url, "kind": kind})
        else:
            add({"id": link_id(url), "name": f"{link_id(url)}.mp4", "url": url, "kind": kind})
    return items


def _dropbox_direct(url: str) -> str:
    p = urlparse(url)
    q = parse_qs(p.query)
    q["dl"] = ["1"]
    return urlunparse(p._replace(query=urlencode({k: v[0] for k, v in q.items()})))


def _http_download(url: str, dest: Path) -> Path:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, headers=gdrive.UA, stream=True, timeout=300) as r:
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            raise RuntimeError(f"{url} is a web page, not a video file (is it shared publicly?)")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    return dest


def _site_download(url: str, dest: Path) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("pip install yt-dlp (needed for YouTube/TikTok/Instagram links)") from exc
    work = dest.parent / (dest.stem + "_dl")
    work.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(work / "video.%(ext)s"),
        "format": "bv*[ext=mp4][height<=1920]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 500 * 1024 * 1024,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        got = sorted(work.glob("video.*"), key=lambda p: p.stat().st_size, reverse=True)
        if not got:
            raise RuntimeError(f"nothing downloaded from {url}")
        shutil.move(str(got[0]), dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dest


def fetch(item: dict, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    kind = item["kind"]
    if kind == "drive_file":
        return gdrive.download(item["id"], dest)
    if kind == "dropbox":
        return _http_download(_dropbox_direct(item["url"]), dest)
    if kind == "direct":
        return _http_download(item["url"], dest)
    return _site_download(item["url"], dest)
