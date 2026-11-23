"""Command line interface:  python -m reeler <command>"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from reeler import __version__, media
from reeler.config import load_config

log = logging.getLogger("reeler")
ROOT = Path(__file__).resolve().parent.parent


def _p(msg: str = "") -> None:
    print(msg, flush=True)


def _app(args):
    from reeler.pipeline import Reeler
    cfg = load_config(args.config)
    if getattr(args, "dry_run", False):
        cfg["uploader"] = "dry-run"
    return cfg, Reeler(cfg)


def _uploader(cfg):
    from reeler.uploaders import make_uploader
    return make_uploader(cfg)


# ---------------------------------------------------------------------------
def cmd_init(args) -> int:
    target = Path(args.config).resolve().parent
    for src, dst in (("config.example.yaml", "config.yaml"), (".env.example", ".env")):
        d = target / dst
        if d.exists():
            _p(f"  exists  {d}")
        else:
            shutil.copyfile(ROOT / src, d)
            _p(f"  created {d}")
    (target / "videos").mkdir(exist_ok=True)
    _p("\nNext: put your clips in ./videos, fill in .env, then run:  python -m reeler check")
    return 0


def cmd_check(args) -> int:
    cfg, app = _app(args)
    ok = True
    try:
        media.require_ffmpeg()
        _p("[ok]   ffmpeg found")
    except media.MediaError as exc:
        ok = False
        _p(f"[FAIL] {exc}")
    font = media.find_font(cfg.video.font_file)
    _p(f"[{'ok' if font else 'warn'}]   font for hook text: {font or 'not found - set video.font_file'}")

    found, new = app.scan()
    _p(f"[ok]   {found} clip(s) in {cfg.videos_path} ({new} new)")

    if cfg.ai.enabled:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=cfg.secrets.anthropic_api_key) if cfg.secrets.anthropic_api_key \
                else anthropic.Anthropic()
            client.models.retrieve(cfg.ai.model)
            _p(f"[ok]   Claude API reachable ({cfg.ai.model})")
        except Exception as exc:  # noqa: BLE001
            _p(f"[warn] Claude API not usable ({str(exc)[:90] or exc.__class__.__name__}) - offline captions will be used")

    try:
        up = _uploader(cfg)
        _p(f"[ok]   uploader '{up.name}': {up.check()}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        _p(f"[FAIL] uploader '{cfg.uploader}': {exc}")

    g = app.graph
    if g is None:
        _p("[warn] no IG_USER_ID / IG_ACCESS_TOKEN - live trends + trending audio disabled")
    else:
        try:
            items = g.search_audio(cfg.audio.audio_type)
            _p(f"[ok]   Instagram Audio API: {len(items)} trending track(s) visible")
        except Exception as exc:  # noqa: BLE001
            _p(f"[warn] Instagram Audio API: {exc}")
        try:
            hid = g.hashtag_id("gta6")
            _p(f"[ok]   Hashtag search works (#gta6 -> {hid})")
            app.state.record_lookup("gta6", hid)
        except Exception as exc:  # noqa: BLE001
            _p(f"[warn] Hashtag search: {exc}")
    return 0 if ok else 1


def cmd_scan(args) -> int:
    _, app = _app(args)
    found, new = app.scan()
    _p(f"{found} clip(s) found, {new} new")
    return 0


def cmd_trends(args) -> int:
    _, app = _app(args)
    if args.refresh or not app.state.hashtags():
        rep = app.refresh_trends(force=True)
        if rep["live"]:
            _p(f"Live research on: {', '.join(rep['researched'])}")
            if rep["discovered"]:
                _p(f"Discovered trending: {', '.join('#' + t for t in rep['discovered'])}")
        else:
            _p(f"Using seed hashtag list (live research off/unavailable{': ' + rep['error'] if rep['error'] else ''})")
    _p(f"\n{'tag':28} {'score':>6}  {'source':10} category")
    for r in app.state.hashtags()[: args.limit]:
        _p(f"#{r['tag']:27} {r['score']:6.2f}  {r['source']:10} {r['category'] or ''}")
    return 0


def cmd_prepare(args) -> int:
    _, app = _app(args)
    media.require_ffmpeg()
    app.scan()
    ids = args.ids or [r["id"] for r in app.queue() if r["status"] != "prepared" or args.reanalyse][: args.limit]
    if not ids:
        _p("Nothing to prepare.")
    for vid in ids:
        t0 = time.time()
        try:
            res = app.prepare(int(vid), reanalyse=args.reanalyse)
        except Exception as exc:  # noqa: BLE001
            _p(f"[{vid}] FAILED: {exc}")
            continue
        a = res["analysis"]
        _p(f"[{vid}] {res['file']}  ({time.time() - t0:.0f}s, {a.get('source')})")
        _p(f"     {a.get('category')} / {a.get('mood')} / energy {a.get('energy')}  hook: {a.get('hook_text')!r}")
        _p("     " + res["caption"].replace("\n", "\n     "))
        _p()
    return 0


def cmd_queue(args) -> int:
    cfg, app = _app(args)
    app.scan()
    rows = app.state.list()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    _p("Status: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "No clips yet.")
    _p()
    for r in app.queue()[: args.limit]:
        _p(f"  [{r['id']:>4}] {r['status']:9} {Path(r['path']).name}" + (f"   ! {r['error'][:80]}" if r["error"] else ""))
    posted = app.state.last_posted(5)
    if posted:
        _p("\nRecently posted:")
        for r in posted:
            when = datetime.fromtimestamp(r["posted_at"]).strftime("%Y-%m-%d %H:%M")
            audio = json.loads(r["audio"] or "null")
            _p(f"  [{r['id']:>4}] {when}  {r['permalink'] or r['media_id']}"
               + (f"  ♪ {audio.get('label')}" if audio else ""))
    from reeler.scheduler import get_tz, next_slot
    tz = get_tz(cfg.schedule.timezone)
    times = [datetime.fromtimestamp(r["posted_at"], tz) for r in app.state.posted_since(time.time() - 3 * 86400)]
    _p(f"\nNext scheduled slot: {next_slot(datetime.now(tz), cfg.schedule, times):%a %Y-%m-%d %H:%M %Z}")
    return 0


def cmd_preview(args) -> int:
    _, app = _app(args)
    row = app.state.get(args.id)
    if row is None:
        _p("No such id")
        return 1
    if row["status"] not in ("prepared", "posted"):
        app.prepare(args.id)
        row = app.state.get(args.id)
    a = json.loads(row["analysis"] or "{}")
    _p(f"File:    {row['path']}\nReel:    {row['processed']}\nCover:   {row['cover']}")
    _p(f"Scene:   {a.get('description')}")
    _p(f"Vibe:    {a.get('category')} / {a.get('mood')} / energy {a.get('energy')}")
    _p(f"Hook:    {a.get('hook_text')}")
    _p(f"Music:   {', '.join(a.get('music_search_terms', []))}")
    _p("\n" + (row["caption"] or ""))
    return 0


def cmd_set_caption(args) -> int:
    _, app = _app(args)
    app.state.update(args.id, caption=args.text.replace("\\n", "\n"))
    _p("Caption updated.")
    return 0


def cmd_skip(args) -> int:
    _, app = _app(args)
    app.state.update(args.id, status="skipped")
    _p(f"Clip {args.id} skipped.")
    return 0


def cmd_retry(args) -> int:
    _, app = _app(args)
    app.state.update(args.id, status="prepared" if app.state.get(args.id)["processed"] else "new",
                     attempts=0, error=None)
    _p(f"Clip {args.id} re-queued.")
    return 0


def _post_once(app, uploader) -> bool:
    res = app.post_next(uploader)
    if res is None:
        _p("Queue is empty - add more clips to the videos folder.")
        return False
    _p(f"Posted clip {res['id']}: {res['permalink'] or res['media_id']}")
    if res.get("audio"):
        _p(f"  ♪ trending audio: {res['audio'].get('label')}")
    for n in res.get("notes", []):
        _p(f"  note: {n}")
    return True


def cmd_post(args) -> int:
    """Post the next clip. Without --now, only posts if a schedule slot is due (for cron/Task Scheduler)."""
    cfg, app = _app(args)
    from reeler.scheduler import due_now, get_tz
    tz = get_tz(cfg.schedule.timezone)
    if not args.now:
        times = [datetime.fromtimestamp(r["posted_at"], tz) for r in app.state.posted_since(time.time() - 2 * 86400)]
        if not due_now(datetime.now(tz), cfg.schedule, times):
            _p("No slot due right now (use --now to force).")
            return 0
    media.require_ffmpeg()
    _post_once(app, _uploader(cfg))
    return 0


def cmd_run(args) -> int:
    """Long-running scheduler: prepares clips ahead of time and posts at each slot."""
    cfg, app = _app(args)
    media.require_ffmpeg()
    uploader = _uploader(cfg)
    _p(f"Account: {uploader.check()}")
    from reeler.scheduler import get_tz, next_slot
    tz = get_tz(cfg.schedule.timezone)
    while True:
        times = [datetime.fromtimestamp(r["posted_at"], tz) for r in app.state.posted_since(time.time() - 2 * 86400)]
        slot = next_slot(datetime.now(tz), cfg.schedule, times)
        _p(f"[{datetime.now(tz):%H:%M}] next post at {slot:%a %H:%M}")
        # use the wait to get the next clip ready
        app.scan()
        upcoming = [r for r in app.queue() if r["status"] != "prepared"][:1]
        for r in upcoming:
            try:
                app.prepare(r["id"])
                _p(f"  prepared clip {r['id']} ({Path(r['path']).name})")
            except Exception as exc:  # noqa: BLE001
                _p(f"  could not prepare clip {r['id']}: {exc}")
        while (wait := (slot - datetime.now(tz)).total_seconds()) > 0:
            time.sleep(min(wait, 300))
        try:
            if not _post_once(app, uploader) and args.exit_when_empty:
                return 0
        except Exception as exc:  # noqa: BLE001
            log.exception("post failed: %s", exc)
            _p(f"  post failed: {exc} (will retry at next slot)")
        time.sleep(60)


def cmd_cloud_post(args) -> int:
    """One run of cloud mode (GitHub Actions): next Drive clip -> Reel."""
    cfg, app = _app(args)
    if not cfg.drive.folder:
        _p("Set drive.folder in the config first.")
        return 1
    media.require_ffmpeg()
    uploader = _uploader(cfg)
    count = max(1, args.count)
    posted = 0
    for i in range(count):
        res = app.cloud_post(uploader, ignore_cap=count > 1)
        if "skipped" in res:
            _p(f"Nothing posted: {res['skipped']}")
            break
        posted += 1
        _p(f"[{posted}/{count}] Posted {res['file']} -> {res.get('permalink') or res.get('media_id')}"
           f"  ({res['remaining']} clips left)")
        if res.get("audio"):
            _p(f"  trending audio: {res['audio'].get('label')}")
        for n in res.get("notes", []):
            _p(f"  note: {n}")
        row = app.state.get(res["id"])
        _p("  " + (row["caption"] or "").replace("\n", "\n  "))
        if i < count - 1 and uploader.name != "dry-run":
            time.sleep(max(0, args.gap_minutes) * 60)
    _p(f"\nDone: {posted} Reel(s) posted this run.")
    return 0


def _save_env(cfg, key: str, value: str) -> None:
    env = cfg.root / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")] + [f"{key}={value}"]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_pc_run(args) -> int:
    """Run on your own PC: log in to Instagram once, then post clips from Google Drive on schedule."""
    import getpass

    cfg, app = _app(args)
    media.require_ffmpeg()
    s = cfg.secrets
    if cfg.uploader == "instagrapi" and not (s.ig_username and s.ig_password):
        _p("\n=== One-time Instagram login (saved only on this PC, in the .env file) ===")
        s.ig_username = input("Instagram username: ").strip().lstrip("@")
        s.ig_password = getpass.getpass("Instagram password (hidden while typing): ")
        _save_env(cfg, "IG_USERNAME", s.ig_username)
        _save_env(cfg, "IG_PASSWORD", s.ig_password)
    uploader = _uploader(cfg)
    _p(f"Logged in as {uploader.check()}")

    from reeler.scheduler import get_tz, next_slot
    tz = get_tz(cfg.schedule.timezone)

    def one(ignore_cap: bool) -> bool:
        try:
            res = app.cloud_post(uploader, ignore_cap=ignore_cap)
        except Exception as exc:  # noqa: BLE001
            _p(f"  post failed: {exc}")
            return True
        if "skipped" in res:
            _p(f"  nothing posted: {res['skipped']}")
            return "already posted" not in res["skipped"]
        _p(f"  POSTED {res['file']} -> {res.get('permalink') or res.get('media_id')}  ({res['remaining']} left)")
        if res.get("audio"):
            _p(f"  music: {res['audio'].get('label')}")
        for n in res.get("notes", []):
            _p(f"  note: {n}")
        return True

    for i in range(max(0, args.now)):
        _p(f"[{datetime.now(tz):%H:%M}] posting now ({i + 1}/{args.now})")
        if not one(ignore_cap=True):
            return 0
        if i < args.now - 1:
            time.sleep(max(1.0, args.gap_minutes) * 60)

    _p(f"\nAutopilot on: {len(cfg.schedule.post_times)} Reels/day at {', '.join(cfg.schedule.post_times)} "
       f"({cfg.schedule.timezone}). Keep this window open. Ctrl+C to stop.")
    while True:
        times = [datetime.fromtimestamp(r["posted_at"], tz) for r in app.state.posted_since(time.time() - 2 * 86400)]
        slot = next_slot(datetime.now(tz), cfg.schedule, times)
        _p(f"[{datetime.now(tz):%H:%M}] next Reel at {slot:%a %H:%M}")
        while (wait := (slot - datetime.now(tz)).total_seconds()) > 0:
            time.sleep(min(wait, 300))
        if not one(ignore_cap=False):
            _p("All clips are posted. Add more to the Drive folder and restart.")
            return 0
        time.sleep(60)


def cmd_refresh_token(args) -> int:
    from reeler.graph import GraphClient
    cfg = load_config(args.config)
    s = cfg.secrets
    g = GraphClient(s.ig_user_id, s.ig_access_token, host=cfg.graph.host, version=cfg.graph.version)
    data = g.exchange_long_lived(s.fb_app_id or "", s.fb_app_secret or "")
    tok = data.get("access_token")
    if not tok:
        _p(f"Unexpected response: {data}")
        return 1
    env = cfg.root / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [ln for ln in lines if not ln.startswith("IG_ACCESS_TOKEN=")] + [f"IG_ACCESS_TOKEN={tok}"]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    days = int(data.get("expires_in", 0)) // 86400
    _p(f"New long-lived token saved to .env (expires in ~{days or 60} days).")
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reeler", description="Auto-post gameplay clips as trend-aware Instagram Reels")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create config.yaml, .env and videos/").set_defaults(fn=cmd_init)
    sub.add_parser("check", help="verify ffmpeg, Claude, Instagram access").set_defaults(fn=cmd_check)
    sub.add_parser("scan", help="index new clips in the videos folder").set_defaults(fn=cmd_scan)

    t = sub.add_parser("trends", help="show (and refresh) the trending hashtag ranking")
    t.add_argument("--refresh", action="store_true")
    t.add_argument("--limit", type=int, default=40)
    t.set_defaults(fn=cmd_trends)

    pr = sub.add_parser("prepare", help="analyse + render clips ahead of time (lets you review captions)")
    pr.add_argument("ids", nargs="*", type=int)
    pr.add_argument("--limit", type=int, default=5)
    pr.add_argument("--reanalyse", action="store_true")
    pr.set_defaults(fn=cmd_prepare)

    q = sub.add_parser("queue", help="what is queued / posted / next slot")
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(fn=cmd_queue)

    pv = sub.add_parser("preview", help="show the caption, hook and files for one clip")
    pv.add_argument("id", type=int)
    pv.set_defaults(fn=cmd_preview)

    sc = sub.add_parser("set-caption", help="override a clip's caption (use \\n for new lines)")
    sc.add_argument("id", type=int)
    sc.add_argument("text")
    sc.set_defaults(fn=cmd_set_caption)

    sk = sub.add_parser("skip", help="never post this clip")
    sk.add_argument("id", type=int)
    sk.set_defaults(fn=cmd_skip)

    rt = sub.add_parser("retry", help="re-queue a failed clip")
    rt.add_argument("id", type=int)
    rt.set_defaults(fn=cmd_retry)

    po = sub.add_parser("post", help="post the next clip if a slot is due (cron mode); --now to force")
    po.add_argument("--now", action="store_true")
    po.add_argument("--dry-run", action="store_true")
    po.set_defaults(fn=cmd_post)

    rn = sub.add_parser("run", help="run forever: prepare + post on schedule")
    rn.add_argument("--dry-run", action="store_true")
    rn.add_argument("--exit-when-empty", action="store_true")
    rn.set_defaults(fn=cmd_run)

    cp = sub.add_parser("cloud-post", help="cloud mode: post the next clip from the Google Drive folder")
    cp.add_argument("--dry-run", action="store_true")
    cp.add_argument("--count", type=int, default=1, help="post this many clips in one run (bulk mode)")
    cp.add_argument("--gap-minutes", type=float, default=2, help="pause between posts in bulk mode")
    cp.set_defaults(fn=cmd_cloud_post)

    pc = sub.add_parser("pc-run", help="run on your PC: Drive folder -> Instagram, on schedule")
    pc.add_argument("--now", type=int, default=1, help="post this many right away before the schedule starts")
    pc.add_argument("--gap-minutes", type=float, default=20, help="pause between the --now posts")
    pc.add_argument("--dry-run", action="store_true")
    pc.set_defaults(fn=cmd_pc_run)

    sub.add_parser("refresh-token", help="exchange/refresh the Instagram access token (~60 days)") \
        .set_defaults(fn=cmd_refresh_token)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return args.fn(args) or 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1
