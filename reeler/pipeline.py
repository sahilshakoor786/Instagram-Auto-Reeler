"""The end-to-end flow: scan folder -> prepare (analyse + render + caption) -> post."""

from __future__ import annotations

import json
import logging
import random
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from reeler import media
from reeler.analyzer import analyze_offline, analyze_with_claude
from reeler.audio import pick_trending_audio, recently_used_audio
from reeler.graph import GraphClient, GraphError
from reeler.hashtags import pool_from_state, refresh_trends, select_hashtags
from reeler.niche import _has_api_key, load_profile
from reeler.state import State

log = logging.getLogger(__name__)


def say(msg: str) -> None:
    """Plain progress line for the person watching the window."""
    print(f"  [{datetime.now():%H:%M}] {msg}", flush=True)


class Reeler:
    def __init__(self, cfg):
        self.cfg = cfg
        cfg.work_path.mkdir(parents=True, exist_ok=True)
        state_file = cfg.get("state_file")
        self.state = State(cfg.resolve(state_file) if state_file else cfg.work_path / "state.db")
        self._graph: GraphClient | None | bool = False
        # a project that switches niche must not keep the old niche's hashtags
        prev = self.state.kv_get("niche")
        if prev != cfg.ai.niche:
            if prev is not None:
                log.info("Niche changed (%r -> %r): starting a fresh hashtag pool", prev, cfg.ai.niche)
                self.state.reset_trend_pool()
            self.state.kv_set("niche", cfg.ai.niche)

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
        say(f"checking the videos in {folder} ...")
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
        say(f"preparing {src.name}: looking at the video ...")
        frames = media.extract_frames(src, clip_dir / "frames", count=int(cfg.ai.frames))
        if analysis is None:
            stats = media.energy_stats(src)
            if cfg.ai.enabled and _has_api_key(cfg):
                try:
                    analysis = analyze_with_claude(frames, cfg, trending, src.name, stats)
                except Exception as exc:  # noqa: BLE001 - any AI failure falls back to heuristics
                    log.warning("AI analysis failed for %s (%s) - using offline heuristics", src.name, exc)
            if analysis is None:
                analysis = analyze_offline(stats, len(frames), seed=row["fingerprint"], profile=load_profile(cfg))
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
        max_duration = float(v.max_duration)
        if "youtube" in cfg.platforms:  # Shorts must stay within YouTube's limit
            max_duration = min(max_duration, float(cfg.youtube.max_seconds))
        say("making it vertical with the hook text (this can take a few minutes) ...")
        media.render_reel(
            src, render_path, width=v.width, height=v.height, fps=v.fps, fit=v.fit,
            max_duration=max_duration, crf=v.crf, audio_bitrate=v.audio_bitrate,
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
    @staticmethod
    def as_map(uploader) -> dict:
        """Accept one uploader or {platform: uploader}."""
        if isinstance(uploader, dict):
            return uploader
        return {getattr(uploader, "platform", "instagram"): uploader}

    @staticmethod
    def all_dry(uploaders: dict) -> bool:
        return all(u.name == "dry-run" for u in uploaders.values())

    def _pick_audio(self, uploader, analysis: dict) -> dict | None:
        cfg = self.cfg
        if cfg.audio.mode != "trending" or not uploader.supports_trending_audio:
            return None
        used = recently_used_audio(self.state.last_posted(int(cfg.audio.avoid_repeat_posts)))
        terms = analysis.get("music_search_terms", [])
        if hasattr(uploader, "pick_audio"):  # app-login uploader: Instagram's in-app music catalog
            try:
                choice, track = uploader.pick_audio(terms, used)
                if choice:
                    return dict(choice, _track=track)
            except Exception as exc:  # noqa: BLE001 - music is optional
                log.warning("could not pick trending music: %s", exc)
            return None
        if self.graph is not None:
            return pick_trending_audio(self.graph, cfg, terms, used)
        return None

    def post(self, vid: int, uploader) -> dict:
        """Post one clip to every platform that hasn't had it yet (each platform gets each clip once)."""
        uploaders = self.as_map(uploader)
        row = self.state.get(vid)
        if row["status"] not in ("prepared", "failed") or not row["processed"] or not Path(row["processed"]).exists():
            self.prepare(vid)
            row = self.state.get(vid)
        analysis = json.loads(row["analysis"] or "{}")
        todo = [(p, u) for p, u in uploaders.items() if not self.state.platform_posted(vid, p)]

        self.state.update(vid, status="posting", attempts=row["attempts"] + 1)
        results: dict[str, dict] = {}
        errors: dict[str, Exception] = {}
        notes: list[str] = []
        for platform, up in todo:
            audio = self._pick_audio(up, analysis)
            extra = {}
            if platform == "youtube":
                from reeler.uploaders.youtube import youtube_metadata
                extra["meta"] = youtube_metadata(analysis, row["caption"] or "", json.loads(row["hashtags"] or "[]"),
                                                 self.cfg)
            say(f"uploading to {platform} ..." + (" (adding music can take a few minutes)" if audio else ""))
            try:
                res = up.upload(
                    Path(row["processed"]), row["caption"], cover=Path(row["cover"]) if row["cover"] else None,
                    thumb_offset_ms=int(float(analysis.get("cover_time", 0)) * 1000), audio=audio, **extra,
                )
            except Exception as exc:  # noqa: BLE001 - record, try the other platforms, report below
                log.error("%s upload failed for clip %s: %s", platform, vid, exc)
                errors[platform] = exc
                self.state.record_post(vid, platform, "failed", error=str(exc)[:1000])
                continue
            if up.name != "dry-run":
                self.state.record_post(vid, platform, "posted", media_id=res.media_id, permalink=res.permalink,
                                       audio=res.audio_used, error="; ".join(res.notes) or None)
            results[platform] = {"media_id": res.media_id, "permalink": res.permalink,
                                 "audio": res.audio_used, "notes": res.notes}
            notes += [f"{platform}: {n}" if len(uploaders) > 1 else n for n in res.notes]

        dry = self.all_dry(uploaders)
        complete = not errors and (dry or all(self.state.platform_posted(vid, p) for p in uploaders))
        if not results and errors and not any(self.state.platform_posted(vid, p) for p in uploaders):
            first = next(iter(errors.values()))
            self.state.update(vid, status="failed", error=str(first)[:1000])
            raise first
        primary = results.get("instagram") or next(iter(results.values()), {})
        fields = {}
        if primary:
            fields.update(media_id=primary.get("media_id"), permalink=primary.get("permalink"))
        if "instagram" in results:
            fields["audio"] = results["instagram"].get("audio")
        if complete:
            self.state.update(vid, status="posted", posted_at=time.time(), error="; ".join(notes) or None, **fields)
            if not dry:
                self._after_post(Path(row["path"]), vid)
        else:
            msg = "; ".join(f"{p} failed: {e}" for p, e in errors.items())
            self.state.update(vid, status="failed", error=msg[:1000], **fields)
            notes.append(f"{msg} (will try that platform again later)")
        return {"id": vid, "media_id": primary.get("media_id"), "permalink": primary.get("permalink"),
                "audio": primary.get("audio") if "instagram" in results else None, "notes": notes,
                "platforms": results, "complete": complete}

    def _after_post(self, src: Path, vid: int) -> None:
        """Best-effort cleanup. Never raises: the Reel is already live at this point."""
        import gc

        gc.collect()  # release file handles left open by video libraries (Windows locks open files)
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
            log.info("Could not %s %s yet (%s) - will retry later", action, src, exc)
            if action == "delete":
                self._remember_cleanup(src)
        # rendered copy no longer needed
        reel = self.cfg.work_path / "clips" / f"{vid:05d}" / "reel.mp4"
        try:
            reel.unlink(missing_ok=True)
        except OSError:
            self._remember_cleanup(reel)

    def _remember_cleanup(self, path: Path) -> None:
        pending = self.state.kv_get("cleanup_later", []) or []
        if str(path) not in pending:
            pending.append(str(path))
        self.state.kv_set("cleanup_later", pending)

    def sweep_cleanup(self) -> None:
        pending = self.state.kv_get("cleanup_later", []) or []
        left = []
        for p in pending:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                left.append(p)
        if left != pending:
            self.state.kv_set("cleanup_later", left)

    def post_next(self, uploader) -> dict | None:
        """Local-folder mode: post the next clip in the queue."""
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


    # ---- cloud mode: Google Drive folder -> one Reel per run ---------------
    def cloud_post(self, uploader, *, now_ts: float | None = None, ignore_cap: bool = False) -> dict:
        """Pick the next un-posted clip from Drive, download only that one, post it."""
        from reeler import gdrive
        from reeler.scheduler import get_tz

        cfg = self.cfg
        self.sweep_cleanup()
        d = cfg.drive
        tz = get_tz(cfg.schedule.timezone)
        now_ts = now_ts or time.time()
        today = datetime.fromtimestamp(now_ts, tz).date()
        posted = self.state.posted_since(now_ts - 2 * 86400)
        today_count = sum(1 for r in posted if datetime.fromtimestamp(r["posted_at"], tz).date() == today)
        uploaders = self.as_map(uploader)
        dry = self.all_dry(uploaders)
        if not dry and not ignore_cap:
            if today_count >= int(cfg.schedule.max_posts_per_day):
                return {"skipped": f"daily cap reached ({today_count} today)"}
            if posted and now_ts - posted[-1]["posted_at"] < int(cfg.schedule.min_gap_minutes) * 60:
                return {"skipped": "too soon after the previous post"}

        manifest = cfg.resolve(d.manifest) if d.get("manifest") else None
        files = gdrive.list_folder(gdrive.folder_id_from(d.folder), api_key=cfg.secrets.google_api_key,
                                   manifest=manifest)
        done: dict = self.state.kv_get("drive_done", {}) or {}
        fails: dict = self.state.kv_get("drive_fail", {}) or {}
        todo = [f for f in files if f["id"] not in done]
        if not todo:
            return {"skipped": f"all {len(files)} Drive clips are already posted"}
        if cfg.order == "random":
            rnd = random.Random(self.state.kv_get("shuffle_seed") or self._new_seed())
            todo.sort(key=lambda f: rnd.random())
        elif cfg.order == "newest":
            todo.reverse()
        else:
            todo.sort(key=lambda f: _natural_key(f["name"]))

        cache = cfg.videos_path
        for f in todo[:3]:
            local = cache / re.sub(r"[^A-Za-z0-9._-]", "_", f["name"])
            try:
                if not local.exists():
                    log.info("downloading %s from Drive", f["name"])
                    say(f"downloading {f['name']} from Google Drive ...")
                    gdrive.download(f["id"], local)
                media.probe(local)
                vid, _ = self.state.upsert_video(local)
                row = self.state.get(vid)
                if row["status"] == "posted":  # same clip uploaded twice to Drive
                    done[f["id"]] = "duplicate"
                    self.state.kv_set("drive_done", done)
                    continue
            except Exception as exc:  # noqa: BLE001 - bad file: count and move on
                fails[f["id"]] = fails.get(f["id"], 0) + 1
                if fails[f["id"]] >= 3:
                    done[f["id"]] = "failed"
                log.error("skipping %s: %s", f["name"], exc)
                self.state.kv_set("drive_fail", fails)
                self.state.kv_set("drive_done", done)
                local.unlink(missing_ok=True)
                continue
            try:
                res = self.post(vid, uploaders)
            except Exception:
                fails[f["id"]] = fails.get(f["id"], 0) + 1
                if fails[f["id"]] >= 3:
                    done[f["id"]] = "failed"
                self.state.kv_set("drive_fail", fails)
                self.state.kv_set("drive_done", done)
                raise
            if not dry and res.get("complete", True):
                done[f["id"]] = "posted"
                self.state.kv_set("drive_done", done)
            elif not dry:  # posted on some platforms only: retry the rest, give up after 3 tries
                fails[f["id"]] = fails.get(f["id"], 0) + 1
                if fails[f["id"]] >= 3:
                    done[f["id"]] = "partial"
                    self._after_post(local, vid)
                self.state.kv_set("drive_fail", fails)
                self.state.kv_set("drive_done", done)
            res.update({"file": f["name"], "remaining": len(todo) - 1})
            return res
        return {"skipped": "no usable clip in this run (see errors above)"}


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
