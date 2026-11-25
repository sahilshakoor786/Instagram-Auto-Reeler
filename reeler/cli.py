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


def _load(args):
    name = getattr(args, "profile", None)
    if name:
        from reeler.profiles import list_profiles, profile_path
        if name not in list_profiles(ROOT):
            raise SystemExit(f"No project named '{name}'. Double-click START_HERE.bat to create it.")
        return load_config(profile_path(ROOT, name), root=ROOT)
    return load_config(args.config)


def _app(args):
    from reeler.pipeline import Reeler
    cfg = _load(args)
    if getattr(args, "dry_run", False):
        cfg["uploader"] = "dry-run"
    return cfg, Reeler(cfg)


def _uploader(cfg):
    """All uploaders for the configured platforms ({platform: uploader})."""
    from reeler.uploaders import make_uploaders
    return make_uploaders(cfg)


def _check_uploaders(cfg, uploaders: dict, interactive: bool = False) -> dict:
    """Log in everywhere. A platform that can't log in is dropped (after asking, when interactive)."""
    from reeler.config import save_secret

    ok = {}
    for platform, up in uploaders.items():
        if hasattr(up, "on_new_sessionid"):
            up.on_new_sessionid = lambda sid: save_secret(cfg, "IG_SESSIONID", sid)
        try:
            _p(f"{platform:9}: {up.check()}")
            ok[platform] = up
        except Exception as exc:  # noqa: BLE001
            _p(f"{platform:9}: NOT CONNECTED - {str(exc).strip()}")
    if not ok:
        raise SystemExit("Could not log in to any platform.")
    missing = [p for p in uploaders if p not in ok]
    if missing:
        _p(f"\nClips posted now will NOT be sent to {', '.join(missing)} later.")
        if interactive and input("Continue without it for now? (y/n): ").strip().lower() not in ("y", "yes"):
            raise SystemExit("Stopped. Fix the problem above and start again.")
    return ok


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

    _p(f"[ok]   niche: {cfg.ai.niche}")
    try:
        ups = _uploader(cfg)
    except Exception as exc:  # noqa: BLE001
        ok = False
        ups = {}
        _p(f"[FAIL] uploaders: {exc}")
    for platform, up in ups.items():
        try:
            _p(f"[ok]   {platform} ({up.name}): {up.check()}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            _p(f"[FAIL] {platform} ({up.name}): {str(exc).strip()}")

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
            from reeler.hashtags import resolve_seed
            probe = next(iter((resolve_seed(cfg).get("broad") or {"instagram": 1})), "instagram")
            hid = g.hashtag_id(probe)
            _p(f"[ok]   Hashtag search works (#{probe} -> {hid})")
            app.state.record_lookup(probe, hid)
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
    per = app.state.platform_counts()
    if per:
        _p("Posted per platform: " + ", ".join(f"{k}={v}" for k, v in sorted(per.items())))
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
    _report(res)
    return True


def _report(res: dict, prefix: str = "") -> None:
    plats = res.get("platforms") or {}
    if not plats:
        _p(f"{prefix}Posted clip {res['id']}: {res.get('permalink') or res.get('media_id')}")
    for platform, r in plats.items():
        _p(f"{prefix}POSTED clip {res['id']} on {platform}: {r.get('permalink') or r.get('media_id')}")
    if res.get("audio"):
        _p(f"{prefix}  music: {res['audio'].get('label')}")
    for n in res.get("notes", []):
        _p(f"{prefix}  note: {n}")


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
    uploader = _check_uploaders(cfg, _uploader(cfg))
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
    if not cfg.drive.folder and not (cfg.get("links") or {}).get("file"):
        _p("Set drive.folder or links.file in the config first.")
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
        _p(f"[{posted}/{count}] {res['file']}  ({res['remaining']} clips left)")
        _report(res, "  ")
        row = app.state.get(res["id"])
        _p("  " + (row["caption"] or "").replace("\n", "\n  "))
        if i < count - 1 and not all(u.name == "dry-run" for u in uploader.values()):
            time.sleep(max(0, args.gap_minutes) * 60)
    _p(f"\nDone: {posted} Reel(s) posted this run.")
    return 0


def cmd_pc_run(args) -> int:
    """Run on your own PC: log in once, then post clips (Drive folder or local folder) on schedule."""
    from reeler.scheduler import MAX_PER_DAY, get_tz, next_slot, pacing_warning

    cfg, app = _app(args)
    media.require_ffmpeg()
    uploaders = _check_uploaders(cfg, _uploader(cfg), interactive=sys.stdin.isatty())
    use_drive = bool(cfg.drive.folder)
    tz = get_tz(cfg.schedule.timezone)
    per_day = len(cfg.schedule.post_times)
    if pacing_warning(per_day):
        _p("\n" + pacing_warning(per_day))

    def one(ignore_cap: bool) -> bool:
        """Post one clip everywhere. False = nothing left to post."""
        try:
            if use_drive:
                res = app.cloud_post(uploaders, ignore_cap=ignore_cap)
            else:
                app.sweep_cleanup()
                res = app.post_next(uploaders)
                if res is None:
                    _p("  nothing posted: every clip in the folder is already posted")
                    return False
        except Exception as exc:  # noqa: BLE001
            _p(f"  post failed: {exc}")
            return True
        if "skipped" in res:
            _p(f"  nothing posted: {res['skipped']}")
            return "already posted" not in res["skipped"]
        if res.get("file"):
            _p(f"  {res['file']}  ({res.get('remaining', '?')} left)")
        _report(res, "  ")
        return True

    now = max(0, min(args.now, MAX_PER_DAY))
    if now > 5:
        _p(f"\nPosting {now} right now, one every {max(1.0, args.gap_minutes):.0f} minutes "
           f"(that takes about {(now - 1) * max(1.0, args.gap_minutes) / 60:.1f} hours).")
        if now > 10:
            _p("Heads up: many posts in one go can get an account temporarily limited.")
    for i in range(now):
        _p(f"[{datetime.now(tz):%H:%M}] posting now ({i + 1}/{now})")
        if not one(ignore_cap=True):
            return 0
        if i < now - 1:
            gap = max(1.0, args.gap_minutes)
            _p(f"  waiting {gap:.0f} min before the next one (spacing posts out keeps the account safe)...")
            time.sleep(gap * 60)

    where = " + ".join(uploaders)
    _p(f"\nAutopilot on: {per_day} posts/day to {where} at {', '.join(cfg.schedule.post_times)} "
       f"({cfg.schedule.timezone}). Keep this window open. Ctrl+C to stop - it continues where it left off.")
    while True:
        times = [datetime.fromtimestamp(r["posted_at"], tz) for r in app.state.posted_since(time.time() - 2 * 86400)]
        slot = next_slot(datetime.now(tz), cfg.schedule, times)
        _p(f"[{datetime.now(tz):%H:%M}] next post at {slot:%a %H:%M}")
        while (wait := (slot - datetime.now(tz)).total_seconds()) > 0:
            time.sleep(min(wait, 300))
        if not one(ignore_cap=False):
            _p("All clips are posted. Add more videos and start again.")
            return 0
        time.sleep(60)


def cmd_projects(args) -> int:
    from reeler.profiles import list_profiles, migrate_legacy, read_profile, summary
    migrate_legacy(ROOT)
    names = list_profiles(ROOT)
    if not names:
        _p("No projects yet. Double-click START_HERE.bat to make one.")
    for n in names:
        _p(f"{n:12} {summary(read_profile(ROOT, n))}")
    return 0


def cmd_start(args) -> int:
    """What START_HERE.bat runs: pick/create a project (asks simple questions), then pc-run."""
    from reeler.profiles import choose_profile
    from reeler.scheduler import pacing_warning

    _p("\n==== Auto Poster: Instagram Reels + YouTube Shorts ====")
    if not (ROOT / ".env").exists() and (ROOT / ".env.example").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")  # place for the optional Claude key
    name = choose_profile(input, ROOT)
    args.profile = name
    while True:
        ans = input("\nHow many should I post RIGHT NOW, before the daily schedule starts? [Enter = 1]: ").strip() or "1"
        if ans.isdigit():
            n = int(ans)
            if n > 10:
                _p(pacing_warning(max(n, 11)) or "")
                _p(f"They will go out one every {args.gap_minutes:.0f} minutes.")
                if input("Post that many now? (y/n): ").strip().lower() not in ("y", "yes"):
                    continue
            break
        _p("Please type a number, e.g. 1")
    args.now = n
    _p(f"\nStarting project '{name}'...\n")
    return cmd_pc_run(args)


def cmd_refresh_token(args) -> int:
    from reeler.graph import GraphClient
    cfg = _load(args)
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
    p = argparse.ArgumentParser(prog="reeler",
                                description="Auto-post short videos (any topic) as Instagram Reels / YouTube Shorts")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-p", "--profile", help="use a project made by START_HERE (see .reeler/profiles/)")
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

    st = sub.add_parser("start", help="ask the setup questions, then run (what START_HERE.bat does)")
    st.add_argument("--gap-minutes", type=float, default=20, help="pause between the posts made right away")
    st.add_argument("--dry-run", action="store_true")
    st.set_defaults(fn=cmd_start)

    pf = sub.add_parser("projects", help="list projects made by START_HERE")
    pf.set_defaults(fn=cmd_projects)

    pc = sub.add_parser("pc-run", help="run on your PC: Drive/local folder -> Instagram/YouTube, on schedule")
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
