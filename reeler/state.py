"""SQLite state: which clips exist, what was prepared, what got posted, trend cache."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint   TEXT UNIQUE NOT NULL,
    path          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new',   -- new | prepared | posting | posted | failed | skipped
    processed     TEXT,
    cover         TEXT,
    analysis      TEXT,
    caption       TEXT,
    hashtags      TEXT,
    audio         TEXT,
    media_id      TEXT,
    permalink     TEXT,
    error         TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    added_at      REAL NOT NULL,
    mtime         REAL,
    prepared_at   REAL,
    posted_at     REAL
);
CREATE TABLE IF NOT EXISTS hashtags (
    tag           TEXT PRIMARY KEY,
    score         REAL NOT NULL DEFAULT 0,
    source        TEXT,
    category      TEXT,
    detail        TEXT,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS hashtag_lookups (
    tag           TEXT PRIMARY KEY,
    hashtag_id    TEXT,
    looked_up_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    video_id      INTEGER NOT NULL,
    platform      TEXT NOT NULL,                 -- instagram | youtube
    status        TEXT NOT NULL,                 -- posted | failed
    media_id      TEXT,
    permalink     TEXT,
    audio         TEXT,
    error         TEXT,
    posted_at     REAL,
    PRIMARY KEY (video_id, platform)
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def fingerprint(path: Path, chunk: int = 1 << 20) -> str:
    """Fast content fingerprint: size + first MB + last MB (survives renames)."""
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(chunk))
        if size > chunk:
            fh.seek(max(size - chunk, chunk))
            h.update(fh.read(chunk))
    return h.hexdigest()


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Older databases only knew Instagram: copy their history into the per-platform table."""
        if self.kv_get("posts_migrated"):
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO posts (video_id, platform, status, media_id, permalink, audio, posted_at) "
            "SELECT id, 'instagram', 'posted', media_id, permalink, audio, posted_at FROM videos "
            "WHERE status='posted'")
        self.conn.commit()
        self.kv_set("posts_migrated", True)

    # ---- per-platform posting history ---------------------------------
    def platform_posted(self, vid: int, platform: str) -> bool:
        row = self.conn.execute("SELECT status FROM posts WHERE video_id=? AND platform=?",
                                (vid, platform)).fetchone()
        return bool(row and row["status"] == "posted")

    def record_post(self, vid: int, platform: str, status: str, *, media_id: str | None = None,
                    permalink: str | None = None, audio: Any = None, error: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO posts (video_id, platform, status, media_id, permalink, audio, error, posted_at) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(video_id, platform) DO UPDATE SET status=excluded.status, "
            "media_id=excluded.media_id, permalink=excluded.permalink, audio=excluded.audio, "
            "error=excluded.error, posted_at=excluded.posted_at",
            (vid, platform, status, media_id, permalink,
             json.dumps(audio, ensure_ascii=False) if isinstance(audio, (dict, list)) else audio,
             error, time.time() if status == "posted" else None),
        )
        self.conn.commit()

    def posts_for(self, vid: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM posts WHERE video_id=? ORDER BY platform", (vid,)).fetchall()

    def platform_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT platform, COUNT(*) AS n FROM posts WHERE status='posted' GROUP BY platform").fetchall()
        return {r["platform"]: r["n"] for r in rows}

    def reset_trend_pool(self) -> None:
        """Forget learned hashtags (used when a project's niche changes)."""
        self.conn.execute("DELETE FROM hashtags")
        self.conn.commit()
        self.kv_set("trends_refreshed_at", 0)

    # ---- videos -------------------------------------------------------
    def upsert_video(self, path: Path) -> tuple[int, bool]:
        fp = fingerprint(path)
        row = self.conn.execute("SELECT id, path FROM videos WHERE fingerprint=?", (fp,)).fetchone()
        if row:
            if row["path"] != str(path):
                self.conn.execute("UPDATE videos SET path=? WHERE id=?", (str(path), row["id"]))
                self.conn.commit()
            return row["id"], False
        cur = self.conn.execute(
            "INSERT INTO videos (fingerprint, path, added_at, mtime) VALUES (?,?,?,?)",
            (fp, str(path), time.time(), path.stat().st_mtime),
        )
        self.conn.commit()
        return int(cur.lastrowid), True

    def get(self, vid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()

    def update(self, vid: int, **fields: Any) -> None:
        if not fields:
            return
        for k, v in list(fields.items()):
            if isinstance(v, (dict, list)):
                fields[k] = json.dumps(v, ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE videos SET {cols} WHERE id=?", (*fields.values(), vid))
        self.conn.commit()

    def list(self, statuses: Iterable[str] | None = None) -> list[sqlite3.Row]:
        if statuses:
            statuses = list(statuses)
            q = f"SELECT * FROM videos WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id"
            return self.conn.execute(q, statuses).fetchall()
        return self.conn.execute("SELECT * FROM videos ORDER BY id").fetchall()

    def posted_since(self, ts: float) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM videos WHERE status='posted' AND posted_at>=? ORDER BY posted_at", (ts,)
        ).fetchall()

    def last_posted(self, n: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM videos WHERE status='posted' ORDER BY posted_at DESC LIMIT ?", (n,)
        ).fetchall()

    # ---- hashtags -----------------------------------------------------
    def save_hashtag(self, tag: str, score: float, source: str, category: str = "", detail: dict | None = None):
        self.conn.execute(
            "INSERT INTO hashtags (tag, score, source, category, detail, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(tag) DO UPDATE SET score=excluded.score, source=excluded.source, "
            "category=CASE WHEN excluded.category!='' THEN excluded.category ELSE hashtags.category END, "
            "detail=excluded.detail, updated_at=excluded.updated_at",
            (tag, score, source, category, json.dumps(detail or {}), time.time()),
        )
        self.conn.commit()

    def hashtags(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM hashtags ORDER BY score DESC").fetchall()

    def lookup_count_since(self, ts: float) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM hashtag_lookups WHERE looked_up_at>=?", (ts,)
        ).fetchone()[0]

    def get_lookup(self, tag: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM hashtag_lookups WHERE tag=?", (tag,)).fetchone()

    def record_lookup(self, tag: str, hashtag_id: str | None) -> None:
        self.conn.execute(
            "INSERT INTO hashtag_lookups (tag, hashtag_id, looked_up_at) VALUES (?,?,?) "
            "ON CONFLICT(tag) DO UPDATE SET hashtag_id=excluded.hashtag_id, looked_up_at=excluded.looked_up_at",
            (tag, hashtag_id, time.time()),
        )
        self.conn.commit()

    # ---- kv -----------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        self.conn.commit()
