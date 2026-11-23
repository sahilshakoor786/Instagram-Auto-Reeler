"""The end-to-end flow: scan folder -> prepare (analyse + render + caption) -> post."""

from __future__ import annotations

import json
import logging
import random
import shutil
import time
from pathlib import Path

from reeler import media
from reeler.analyzer import analyze_offline, analyze_with_claude
from reeler.audio import pick_trending_audio, recently_used_audio
from reeler.graph import GraphClient, GraphError
from reeler.hashtags import pool_from_state, refresh_trends, select_hashtags
from reeler.state import State

log = logging.getLogger(__name__)


class Reeler:
    def __init__(self, cfg):
        self.cfg = cfg
        cfg.work_path.mkdir(parents=True, exist_ok=True)
        self.state = State(cfg.work_path / "state.db")
        self._graph: GraphClient | None | bool = False

    # ---- helpers --------------------------------------------------------
    @property
    def graph(self) -> GraphClient | None:
        """Graph client for trends/audio research (works even if uploading via instagrapi)."""
        if self._graph is False:
            s = self.cfg.secrets
            if s.ig_user_id and s.ig_access_token:
                self._graph = GraphClient(s.ig_user_id, s.ig_access_token,
                                          host=self.cfg.graph.host, version=self.cfg.graph.version)
            else:
                self._graph = None
        return self._graph  # type: ignore[return-value]

    def _clip_dir(self, vid: int) -> Path:
        d = self.cfg.work_path / "clips" / f"{vid:05d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- 1. scan --------------------------------------------------------
    def scan(self) -> tuple[int, int]:
        folder = self.cfg.videos_path
        folder.mkdir(parents=True, exist_ok=True)
        posted = self.cfg.posted_path.resolve()
        found = new = 0
        for p in sorted(folder.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in media.VIDEO_EXTS:
                continue
            if posted in p.resolve().parents or p.name.startswith("."):
                continue
            found += 1
            _, is_new = self.state.upsert_video(p)
            new += int(is_new)
        # files that vanished before being posted
        for row in self.state.list(["new", "prepared", "failed"]):
            if not Path(row["path"]).exists():
                self.state.update(row["id"], status="skipped", error="source file missing")
        return found, new

    def queue(self) -> list:
        rows = [r for r in self.state.list(["new", "prepared", "failed"]) if r["attempts"] < 3]
        order = self.cfg.order
        if order == "random":
            rnd = random.Random(self.state.kv_get("shuffle_seed") or self._new_seed())
            rows = sorted(rows, key=lambda r: rnd.random())
        elif order == "newest":
            rows = sorted(rows, key=lambda r: -(r["mtime"] or 0))
        elif order == "oldest":
            rows = sorted(rows, key=lambda r: r["mtime"] or 0)
        else:
            rows = sorted(rows, key=lambda r: _natural_key(Path(r["path"]).name))
        # retryable failures go to the back of the line
        return sorted(rows, key=lambda r: r["status"] == "failed")

    def _new_seed(self) -> int:
        seed = random.randint(1, 10**9)
        self.state.kv_set("shuffle_seed", seed)
        return seed

    # ---- 2. trends -------------------------------------------------------
    def refresh_trends(self, force: bool = False) -> dict:
        return refresh_trends(self.state, self.graph, self.cfg, force=force)

    # ---- 3. prepare ------------------------------------------------------
    def prepare(self, vid: int, reanalyse: bool = False) -> dict:
        cfg = self.cfg
        row = self.state.get(vid)
        if row is None:
            raise KeyError(f"No clip with id {vid}")
        src = Path(row["path"])
        if not src.exists():
            raise FileNotFoundError(src)
        clip_dir = self._clip_dir(vid)

        if not self.state.hashtags():
            self.refresh_trends()
        pool = pool_from_state(self.state)
        trending = [t for t, _ in sorted(((c.tag, c.score) for c in pool.values()), key=lambda x: -x[1])]

        analysis = json.loads(row["analysis"]) if row["analysis"] and not reanalyse else None
        frames = media.extract_frames(src, clip_dir / "frames", count=int(cfg.ai.frames))
        if analysis is None:
            stats = media.energy_stats(src)
            if cfg.ai.enabled:
                try:
                    analysis = analyze_with_claude(frames, cfg, trending, src.name, stats)
                except Exception as exc:  # noqa: BLE001 - any AI failure falls back to heuristics
                    log.warning("AI analysis failed for %s (%s) - using offline heuristics", src.name, exc)
            if analysis is None:
                analysis = analyze_offline(stats, len(frames), seed=row["fingerprint"])
            analysis["stats"] = stats

        # cover frame
        idx = int(analysis.get("best_cover_frame", len(frames) // 2))
        idx = idx if 0 <= idx < len(frames) else len(frames) // 2
        cover_t = frames[idx][0] if cfg.video.cover_from_ai else frames[len(frames) // 2][0]
        cover = media.grab_cover(src, clip_dir / "cover.jpg", cover_t)
        analysis["cover_time"] = cover_t

        # render 9:16 reel
        v = cfg.video
        font = media.find_font(v.font_file)
        if (v.hook_text or v.watermark) and font is None:
            log.warning("No font found for text overlays - set video.font_file in config.yaml")
        out = render_path = clip_dir / "reel.mp4"
        media.render_reel(
            src, render_path, width=v.width, height=v.height, fps=v.fps, fit=v.fit,
            max_duration=v.max_duration, crf=v.crf, audio_bitrate=v.audio_bitrate,
            hook_lines=media.wrap_hook(analysis.get("hook_text", "")) if v.hook_text else None,
            hook_seconds=v.hook_seconds, watermark=v.watermark, font=font,
        )

        recent = [json.loads(r["hashtags"] or "[]") for r in self.state.last_posted(6)]
        tags = select_hashtags(pool, category=analysis.get("category", ""),
                               ai_picks=analysis.get("hashtag_picks"), recently_used=recent,
                               max_n=int(cfg.caption.max_hashtags), seed=row["fingerprint"])
        caption = build_caption(analysis, tags, cfg)
        self.state.update(vid, status="prepared", processed=str(out), cover=str(cover), analysis=analysis,
                          caption=caption, hashtags=tags, prepared_at=time.time(), error=None)
        return {"id": vid, "file": src.name, "caption": caption, "hashtags": tags, "analysis": analysis,
                "reel": str(out)}

    # ---- 4. post ---------------------------------------------------------
    def post(self, vid: int, uploader) -> dict:
        cfg = self.cfg
        row = self.state.get(vid)
        if row["status"] not in ("prepared", "failed") or not row["processed"] or not Path(row["processed"]).exists():
            self.prepare(vid)
            row = self.state.get(vid)
        analysis = json.loads(row["analysis"] or "{}")

        audio = None
        if cfg.audio.mode == "trending" and uploader.supports_trending_audio and self.graph is not None:
            used = recently_used_audio(self.state.last_posted(int(cfg.audio.avoid_repeat_posts)))
            audio = pick_trending_audio(self.graph, cfg, analysis.get("music_search_terms", []), used)

        self.state.update(vid, status="posting", attempts=row["attempts"] + 1)
        try:
            res = uploader.upload(
                Path(row["processed"]), row["caption"], cover=Path(row["cover"]) if row["cover"] else None,
                thumb_offset_ms=int(float(analysis.get("cover_time", 0)) * 1000), audio=audio,
            )
        except Exception as exc:
            self.state.update(vid, status="failed", error=str(exc)[:1000])
            raise
        self.state.update(vid, status="posted", media_id=res.media_id, permalink=res.permalink,
                          audio=res.audio_used, posted_at=time.time(),
                          error="; ".join(res.notes) or None)
        if uploader.name != "dry-run":
            self._after_post(Path(row["path"]), vid)
        return {"id": vid, "media_id": res.media_id, "permalink": res.permalink,
                "audio": res.audio_used, "notes": res.notes}

    def _after_post(self, src: Path, vid: int) -> None:
        action = self.cfg.after_post
        try:
            if action == "move" and src.exists():
                dest_dir = self.cfg.posted_path
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / src.name
                if dest.exists():
                    dest = dest_dir / f"{src.stem}_{vid}{src.suffix}"
                shutil.move(str(src), dest)
                self.state.update(vid, path=str(dest))
            elif action == "delete" and src.exists():
                src.unlink()
        except OSError as exc:
            log.warning("Could not %s %s: %s", action, src, exc)
        # rendered copy no longer needed
        reel = self.cfg.work_path / "clips" / f"{vid:05d}" / "reel.mp4"
        reel.unlink(missing_ok=True)

    def post_next(self, uploader) -> dict | None:
        self.scan()
        if time.time() - (self.state.kv_get("trends_refreshed_at", 0) or 0) > self.cfg.trends.refresh_hours * 3600:
            try:
                self.refresh_trends()
            except GraphError as exc:
                log.warning("trend refresh failed: %s", exc)
        for row in self.queue():
            try:
                return self.post(row["id"], uploader)
            except (media.MediaError, FileNotFoundError) as exc:
                log.error("Skipping %s: %s", row["path"], exc)
                self.state.update(row["id"], status="failed", error=str(exc)[:1000])
                continue
        return None


def build_caption(analysis: dict, tags: list[str], cfg) -> str:
    parts = [analysis.get("caption", "").strip()]
    if cfg.caption.add_cta and analysis.get("question_cta"):
        parts.append(analysis["question_cta"].strip())
    if cfg.caption.signature:
        parts.append(cfg.caption.signature.strip())
    body = "\n\n".join(p for p in parts if p)
    if tags:
        body += "\n\n" + " ".join(f"#{t}" for t in tags)
    return body[:2200]  # Instagram caption limit


def _natural_key(name: str):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]
