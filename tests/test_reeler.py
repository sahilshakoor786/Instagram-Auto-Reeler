import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from reeler import hashtags as H
from reeler import media, scheduler
from reeler.analyzer import SCHEMA, analyze_offline
from reeler.config import load_config
from reeler.graph import GraphError
from reeler.pipeline import Reeler, build_caption

HAS_FFMPEG = shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------- helpers
def make_cfg(tmp_path: Path, **overrides):
    data = {"videos_dir": "videos", "work_dir": ".reeler", "posted_dir": "videos/posted",
            "uploader": "dry-run", "ai": {"enabled": False}, "trends": {"live_research": False}}
    for k, v in overrides.items():
        data[k] = v
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(data))
    return load_config(tmp_path / "config.yaml")


def make_clip(path: Path, size="1280x720", dur=3, audio=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=300:duration={dur}", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", str(path)]
    subprocess.run(cmd, check=True)
    return path


# ---------------------------------------------------------------- scheduler
SCHED = {"post_times": ["11:00", "19:00"], "jitter_minutes": 0, "max_posts_per_day": 2, "min_gap_minutes": 60}
UTC = timezone.utc


def test_next_slot_same_day():
    now = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    assert scheduler.next_slot(now, SCHED, []) == datetime(2026, 1, 5, 11, 0, tzinfo=UTC)


def test_next_slot_respects_daily_cap():
    now = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    posted = [datetime(2026, 1, 5, 8, tzinfo=UTC), datetime(2026, 1, 5, 11, tzinfo=UTC)]
    assert scheduler.next_slot(now, SCHED, posted) == datetime(2026, 1, 6, 11, 0, tzinfo=UTC)


def test_due_now_window_and_no_double_post():
    slot = datetime(2026, 1, 5, 19, 0, tzinfo=UTC)
    assert scheduler.due_now(slot + timedelta(minutes=10), SCHED, [])
    assert not scheduler.due_now(slot + timedelta(minutes=90), SCHED, [])
    assert not scheduler.due_now(slot + timedelta(minutes=10), SCHED, [slot + timedelta(minutes=2)])


def test_jitter_is_deterministic_and_bounded():
    s = dict(SCHED, jitter_minutes=15)
    day = datetime(2026, 3, 1, tzinfo=UTC)
    a = scheduler.slots_for_day(day, s["post_times"], 15, UTC)
    b = scheduler.slots_for_day(day, s["post_times"], 15, UTC)
    assert a == b
    assert abs((a[0] - datetime(2026, 3, 1, 11, tzinfo=UTC)).total_seconds()) <= 15 * 60


# ---------------------------------------------------------------- hashtags
def test_seed_pool_and_selection_mix():
    pool = H.seed_candidates(H.load_seed())
    tags = H.select_hashtags(pool, category="car_chase", ai_picks=["#ViceCityChase", "fyp"], max_n=5)
    assert len(tags) == 5 and len(set(tags)) == 5
    assert tags[0] == "gta6"                      # broad anchor
    assert any(pool[t].category == "car_chase" for t in tags if t in pool)
    assert "vicecitychase" in tags                # AI pick normalised
    assert "fyp" not in tags                      # spam tag blocked


def test_selection_rotates_recently_used():
    pool = H.seed_candidates(H.load_seed())
    first = H.select_hashtags(pool, category="stunt", max_n=5)
    second = H.select_hashtags(pool, category="stunt", max_n=5, recently_used=[first] * 4)
    assert second[0] == "gta6"
    assert set(first) != set(second)


def test_analyze_media_finds_cooccurring_tags():
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")
    media_items = [
        {"caption": "insane #gta6 #gta6stunts #vicecity", "like_count": 9000, "comments_count": 300,
         "timestamp": now, "media_product_type": "REELS"},
        {"caption": "#gta6 #gta6stunts", "like_count": 100, "comments_count": 2, "timestamp": now,
         "media_product_type": "REELS"},
    ]
    a = H.analyze_media(media_items, "gta6")
    assert "gta6" not in a["cooccurring"]
    assert a["cooccurring"].most_common(1)[0][0] == "gta6stunts"
    assert a["posts"] == 2


def test_live_refresh_uses_budget_and_discovers(tmp_path):
    cfg = make_cfg(tmp_path, preset="gta6", trends={"live_research": True, "weekly_query_budget": 3})
    app = Reeler(cfg)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")

    class FakeGraph:
        calls = 0

        def hashtag_id(self, tag):
            FakeGraph.calls += 1
            return f"id_{tag}"

        def hashtag_media(self, hid, edge="top_media", limit=50):
            return [{"caption": "#gta6 #gta6leaks #gta6trailer3 #followme", "like_count": 5000,
                     "comments_count": 50, "timestamp": now, "media_product_type": "REELS"}] * 3

    rep = H.refresh_trends(app.state, FakeGraph(), cfg, force=True)
    assert rep["live"] is True
    assert FakeGraph.calls == 3                       # stopped at the weekly budget
    tags = {r["tag"]: r for r in app.state.hashtags()}
    assert "gta6trailer3" in tags and tags["gta6trailer3"]["source"] == "discovered"
    assert "followme" not in tags                    # spam filtered


# ---------------------------------------------------------------- analysis / caption
def test_schema_is_strict():
    assert SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"])


def test_offline_analysis_and_caption(tmp_path):
    cfg = make_cfg(tmp_path, caption={"signature": "Follow for daily GTA 6"})
    a = analyze_offline({"scene_cuts_per_sec": 0.9, "mean_volume_db": -12}, 6, seed="x")
    assert a["mood"] == "hype" and a["hook_text"]
    cap = build_caption(a, ["gta6", "vicecity"], cfg)
    assert cap.endswith("#gta6 #vicecity")
    assert "Follow for daily GTA 6" in cap
    assert len(cap) <= 2200


# ---------------------------------------------------------------- graph uploader
class FakeClient:
    def __init__(self, reject_audio=False):
        self.reject_audio = reject_audio
        self.containers = []

    def create_reel_container(self, caption, **kw):
        if kw.get("audio_configuration") and self.reject_audio:
            raise GraphError("Graph API 400: invalid audio_id", {"error": {"code": 100}}, 400)
        self.containers.append(kw)
        return {"id": f"c{len(self.containers)}", "uri": "https://rupload.example/x"}

    def upload_video(self, cid, video, uri=None):
        assert Path(video).exists()
        return {"success": True}

    def wait_until_ready(self, cid, poll=5, timeout=600):
        return {"status_code": "FINISHED"}

    def publish(self, cid):
        return "1789"

    def permalink(self, mid):
        return "https://www.instagram.com/reel/abc/"


def _graph_uploader(tmp_path, client):
    from reeler.uploaders.graph_api import GraphUploader
    cfg = make_cfg(tmp_path, uploader="graph")
    up = GraphUploader.__new__(GraphUploader)
    up.cfg, up.client, up.supports_trending_audio = cfg, client, True
    return up


def test_graph_upload_with_trending_audio(tmp_path):
    video = tmp_path / "r.mp4"
    video.write_bytes(b"0" * 10)
    client = FakeClient()
    res = _graph_uploader(tmp_path, client).upload(video, "cap", thumb_offset_ms=1500,
                                                   audio={"audio_id": "42", "label": "x"})
    assert res.media_id == "1789" and res.permalink
    assert client.containers[0]["audio_configuration"]["audio_id"] == "42"
    assert client.containers[0]["thumb_offset_ms"] == 1500
    assert res.audio_used["audio_id"] == "42"


def test_graph_upload_falls_back_when_audio_rejected(tmp_path):
    video = tmp_path / "r.mp4"
    video.write_bytes(b"0" * 10)
    client = FakeClient(reject_audio=True)
    res = _graph_uploader(tmp_path, client).upload(video, "cap", audio={"audio_id": "42"})
    assert res.media_id == "1789"
    assert res.audio_used is None and res.notes
    assert client.containers[0]["audio_configuration"] is None


def test_audio_picker_skips_recent_and_survives_errors(tmp_path):
    from reeler.audio import pick_trending_audio
    cfg = make_cfg(tmp_path)

    class G:
        def search_audio(self, audio_type="music", query=None, limit=25):
            if query == "boom":
                raise GraphError("no access")
            return [{"audio_id": "1", "title": "Old"}, {"audio_id": "2", "title": "New", "artist": "DJ"}]

    choice = pick_trending_audio(G(), cfg, ["phonk"], recently_used={"1"})
    assert choice["audio_id"] == "2" and choice["label"] == "New - DJ"
    assert pick_trending_audio(G(), cfg, ["boom"], set()) is None


# ---------------------------------------------------------------- media + full pipeline
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_render_reel_is_vertical(tmp_path):
    src = make_clip(tmp_path / "in.mp4", size="640x360", dur=2, audio=False)
    out = media.render_reel(src, tmp_path / "out" / "reel.mp4", width=540, height=960, fit="blur",
                            hook_lines=["WAIT FOR IT"], font=media.find_font(), watermark="@me")
    p = media.probe(out)
    assert (p.width, p.height) == (540, 960)
    assert p.has_audio            # silent track added for clips without audio
    assert abs(p.duration - 2) < 0.3


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_full_dry_run_post(tmp_path):
    cfg = make_cfg(tmp_path, preset="gta6", video={"width": 540, "height": 960})
    make_clip(tmp_path / "videos" / "clip_b.mp4")
    make_clip(tmp_path / "videos" / "clip_a.mp4", size="720x1280")
    app = Reeler(cfg)
    assert app.scan() == (2, 2)
    assert app.scan() == (2, 0)                       # idempotent
    from reeler.uploaders import make_uploader
    up = make_uploader(cfg)
    res = app.post_next(up)
    row = app.state.get(res["id"])
    assert row["status"] == "posted"
    assert "#gta6" in row["caption"]
    assert Path(row["cover"]).exists()
    # dry-run leaves the source in place; second post takes the other clip
    res2 = app.post_next(up)
    assert res2["id"] != res["id"]
    assert app.post_next(up) is None


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_after_post_move(tmp_path):
    cfg = make_cfg(tmp_path, video={"width": 540, "height": 960})
    make_clip(tmp_path / "videos" / "c.mp4")
    app = Reeler(cfg)
    app.scan()

    class Up:
        name = "graph"
        supports_trending_audio = False

        def upload(self, video, caption, **kw):
            from reeler.uploaders.base import UploadResult
            return UploadResult("m1", "https://instagram.com/reel/x/")

    res = app.post_next(Up())
    row = app.state.get(res["id"])
    assert "posted" in row["path"] and Path(row["path"]).exists()
    assert not (tmp_path / "videos" / "c.mp4").exists()
    assert app.scan() == (0, 0)                      # posted folder is ignored


# ---------------------------------------------------------------- Claude request (mocked HTTP)
def test_claude_request_shape(tmp_path, monkeypatch):
    import anthropic
    import httpx2 as httpx

    from reeler import analyzer

    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        out = {"category": "car_chase", "mood": "intense", "energy": 8, "description": "police chase",
               "hook_text": "COPS HAD NO CHANCE", "caption": "Full send 🚔", "question_cta": "Would you stop?",
               "hashtag_picks": ["gta6cars"], "music_search_terms": ["phonk"], "best_cover_frame": 1}
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": json.dumps(out)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10}})

    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: real(
        api_key="test", base_url="https://api.example", http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    cfg = make_cfg(tmp_path, ai={"enabled": True})
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    data = analyzer.analyze_with_claude([(0.5, img), (2.0, img)], cfg, ["gta6", "vicecity"], "clip.mp4", {})
    assert data["category"] == "car_chase" and data["source"] == "claude"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default" and "server-side-fallback" in seen["beta"]
    assert sum(1 for b in body["messages"][0]["content"] if b["type"] == "image") == 2


def test_graph_client_resumable_upload_headers(tmp_path):
    from reeler.graph import GraphClient

    calls = []

    class Resp:
        def __init__(self, data, status=200):
            self._d, self.status_code, self.text = data, status, json.dumps(data)

        def json(self):
            return self._d

    class Sess:
        def post(self, url, data=None, headers=None, params=None, timeout=None):
            body = data.read() if hasattr(data, "read") else data
            calls.append(("POST", url, headers, body))
            if url.endswith("/media"):
                return Resp({"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/C1"})
            if "rupload" in url:
                return Resp({"success": True})
            if url.endswith("/media_publish"):
                return Resp({"id": "M1"})
            return Resp({"error": {"message": "unexpected"}}, 400)

        def get(self, url, params=None, timeout=None):
            calls.append(("GET", url, params, None))
            return Resp({"status_code": "FINISHED"})

    g = GraphClient("17841", "TOKEN")
    g.session = Sess()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1234)
    c = g.create_reel_container("hi", thumb_offset_ms=2000, audio_configuration={"audio_id": "9"})
    g.upload_video(c["id"], video, c["uri"])
    g.wait_until_ready("C1", poll=0)
    assert g.publish("C1") == "M1"
    create = calls[0]
    assert create[3]["media_type"] == "REELS" and create[3]["upload_type"] == "resumable"
    assert json.loads(create[3]["audio_configuration"]) == {"audio_id": "9"}
    up = calls[1]
    assert up[2]["Authorization"] == "OAuth TOKEN" and up[2]["file_size"] == "1234" and up[2]["offset"] == "0"
    assert len(up[3]) == 1234


# ---------------------------------------------------------------- Google Drive cloud mode
def test_drive_folder_id_parsing():
    from reeler.gdrive import folder_id_from
    assert folder_id_from("https://drive.google.com/drive/folders/1f1X03l5Xg80wq-fnJOz0RWbk0SiTf68k?usp=sharing") \
        == "1f1X03l5Xg80wq-fnJOz0RWbk0SiTf68k"
    assert folder_id_from("https://drive.google.com/open?id=abcdefghijkl") == "abcdefghijkl"
    assert folder_id_from("abcdefghijkl") == "abcdefghijkl"


class _R:
    def __init__(self, text="", content=b"", ctype="text/html", status=200):
        self.text, self._c, self.headers, self.status_code = text, content, {"Content-Type": ctype}, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, n):
        yield self._c

    def json(self):
        return json.loads(self.text)


def test_drive_listing_merges_manifest_and_embedded(tmp_path):
    from reeler import gdrive
    man = tmp_path / "m.json"
    man.write_text(json.dumps([{"id": "AAAAAAAAAAAA", "name": "clip_1.mp4", "size": 5}]))
    page = ('<div class="flip-entry" id="entry-BBBBBBBBBBBB" tabindex="0"><div class="flip-entry-title">clip_2.mp4</div>'
            '<div class="flip-entry" id="entry-AAAAAAAAAAAA"><div class="flip-entry-title">clip_1.mp4</div>'
            '<div class="flip-entry" id="entry-CCCCCCCCCCCC"><div class="flip-entry-title">notes.txt</div>')

    class S:
        def get(self, url, **kw):
            assert "embeddedfolderview" in url
            return _R(page)

    files = gdrive.list_folder("FOLDERID12345", manifest=man, session=S())
    assert [f["id"] for f in files] == ["AAAAAAAAAAAA", "BBBBBBBBBBBB"]


def test_drive_download_handles_confirm_page(tmp_path):
    from reeler import gdrive
    calls = []
    confirm = ('<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
               '<input type="hidden" name="id" value="FILE"><input type="hidden" name="confirm" value="t">'
               '<input type="hidden" name="uuid" value="u-1"></form>')

    class S:
        def get(self, url, params=None, **kw):
            calls.append(params)
            if len(calls) == 1:
                return _R(confirm)
            return _R(content=b"v" * 4096, ctype="video/mp4")

    out = gdrive.download("FILE", tmp_path / "a.mp4", session=S())
    assert out.stat().st_size == 4096 and calls[1]["uuid"] == "u-1"


def test_drive_download_private_file_errors(tmp_path):
    from reeler import gdrive

    class S:
        def get(self, url, **kw):
            return _R("<a href='https://accounts.google.com/ServiceLogin'>sign in</a>")

    with pytest.raises(RuntimeError, match="not public"):
        gdrive.download("FILE", tmp_path / "a.mp4", session=S())


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_cloud_post_cycles_through_drive(tmp_path, monkeypatch):
    from reeler import gdrive
    from reeler.uploaders.base import UploadResult

    src = {"ID_A": make_clip(tmp_path / "src" / "a.mp4"), "ID_B": make_clip(tmp_path / "src" / "b.mp4", size="720x1280")}
    monkeypatch.setattr(gdrive, "list_folder", lambda *a, **k: [{"id": "ID_A", "name": "clip a.mp4"},
                                                                  {"id": "ID_B", "name": "clip_b.mp4"},
                                                                  {"id": "ID_BAD", "name": "broken.mp4"}])

    def fake_download(fid, dest, session=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if fid == "ID_BAD":
            dest.write_bytes(b"not a video" * 200)
        else:
            shutil.copyfile(src[fid], dest)
        return dest

    monkeypatch.setattr(gdrive, "download", fake_download)
    cfg = make_cfg(tmp_path, drive={"folder": "x"}, order="name", after_post="delete", state_file="state/s.db",
                   video={"width": 540, "height": 960},
                   schedule={"max_posts_per_day": 2, "min_gap_minutes": 0, "timezone": "UTC",
                             "post_times": ["10:00"]})

    class Up:
        name = "graph"
        supports_trending_audio = False
        n = 0

        def upload(self, video, caption, **kw):
            Up.n += 1
            return UploadResult(f"m{Up.n}", f"https://instagram.com/reel/{Up.n}/")

    app = Reeler(cfg)
    r1 = app.cloud_post(Up())
    assert r1["file"] == "broken.mp4" or r1["file"] == "clip a.mp4"
    names = {r1["file"]}
    r2 = app.cloud_post(Up())
    names.add(r2.get("file"))
    assert names == {"clip a.mp4", "clip_b.mp4"}
    assert "daily cap" in app.cloud_post(Up())["skipped"]     # max_posts_per_day = 2
    assert (tmp_path / "state" / "s.db").exists()
    done = app.state.kv_get("drive_done")
    assert done["ID_A"] == "posted" and done["ID_B"] == "posted"
    assert app.state.kv_get("drive_fail").get("ID_BAD", 0) >= 1


def test_graph_host_autodetect_from_token():
    from reeler.graph import GraphClient
    assert GraphClient("1", "IGAAxyz").host == "graph.instagram.com"
    assert GraphClient("1", "EAAxyz", host="graph.instagram.com").host == "graph.facebook.com"
    assert GraphClient("1", " IGAAxyz \n").token == "IGAAxyz"


# ---------------------------------------------------------------- app-login (instagrapi) uploader
def _fake_ig(tmp_path, music_fails=False):
    pytest.importorskip("instagrapi")
    from instagrapi.types import Track
    from reeler.uploaders.instagrapi_up import InstagrapiUploader

    cfg = make_cfg(tmp_path, uploader="instagrapi")
    up = InstagrapiUploader(cfg)
    up._logged_in = True

    class Media:
        pk, code = 123, "ABC"

    def tr(i, title):
        return Track(id=str(i), title=title, subtitle="", display_artist="DJ", audio_cluster_id=str(i),
                     uri=f"https://x/{i}.m4a", highlight_start_times_in_ms=[1000], is_explicit=False,
                     dash_manifest="", has_lyrics=False, audio_asset_id=str(i), duration_in_ms=30000,
                     allows_saving=True, territory_validity_periods={})

    class FakeCl:
        calls = []

        def music_trending(self):
            # real responses are loosely shaped; the second item is missing most fields on purpose
            return {"items": [{"track": tr(1, "Hot").model_dump()},
                              {"track": {"audio_asset_id": "2", "title": "Hotter", "uri": "https://x/2.m4a"}}]}

        def search_music(self, q):
            return [tr(3, f"{q} beat")]

        def clip_upload_as_reel_with_music(self, path, caption, track):
            FakeCl.calls.append(("music", track.id))
            if music_fails:
                raise RuntimeError("boom")
            return Media()

        def clip_upload(self, path, caption, thumbnail=None):
            FakeCl.calls.append(("plain", None))
            return Media()

    up.cl = FakeCl()
    return up, FakeCl


def test_instagrapi_trending_music_and_upload(tmp_path):
    up, Cl = _fake_ig(tmp_path)
    choice, track = up.pick_audio(["phonk"], recently_used={"1"})
    assert choice["audio_id"] in {"2", "3"} and track is not None
    res = up.upload(tmp_path / "v.mp4", "cap", audio=dict(choice, _track=track))
    assert Cl.calls == [("music", choice["audio_id"])]
    assert res.permalink == "https://www.instagram.com/reel/ABC/"
    assert res.audio_used["audio_id"] == choice["audio_id"] and "_track" not in res.audio_used


def test_instagrapi_music_failure_falls_back(tmp_path):
    up, Cl = _fake_ig(tmp_path, music_fails=True)
    choice, track = up.pick_audio([], set())
    res = up.upload(tmp_path / "v.mp4", "cap", audio=dict(choice, _track=track))
    assert [c[0] for c in Cl.calls] == ["music", "plain"]
    assert res.audio_used is None and res.notes


def test_instagrapi_sessionid_login(tmp_path, monkeypatch):
    pytest.importorskip("instagrapi")
    from reeler.uploaders import instagrapi_up as mod

    cfg = make_cfg(tmp_path, uploader="instagrapi")
    up = mod.InstagrapiUploader(cfg)
    seen = {}

    class FakeCl:
        device_settings = {}
        delay_range = None

        def set_device(self, d):
            seen["device"] = d

        def set_user_agent(self):
            pass

        def login_by_sessionid(self, sid):
            if not sid.startswith("123"):
                raise AssertionError("Invalid sessionid")
            seen["sid"] = sid

        def dump_settings(self, path):
            seen["dumped"] = True

        def login(self, *a, **k):
            raise AssertionError("password login must not be attempted by default")

    up.cl = FakeCl()
    cfg.secrets.ig_username, cfg.secrets.ig_password = "me", "pw"
    answers = iter(["bad", "123%3Aabc%3A9"])
    monkeypatch.setattr(mod.getpass, "getpass", lambda prompt="": next(answers))
    saved = []
    up.on_new_sessionid = saved.append
    up._login()
    assert seen["sid"] == "123:abc:9"                      # URL-decoded browser cookie
    assert seen["device"]["app_version"] == mod.InstagrapiUploader.APP_VERSION["app_version"]
    assert saved == ["123%3Aabc%3A9"] and up._logged_in


def test_browser_login_reads_cookie_after_login(tmp_path):
    pytest.importorskip("playwright")
    import http.server
    import threading

    from reeler.browser_login import grab_session_cookie

    chrome = "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"
    if not Path(chrome).exists():
        pytest.skip("no local chromium")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if self.path.startswith("/login-done"):
                self.send_header("Set-Cookie", "sessionid=4242%3Aabc%3A1; Path=/")
                body = b"logged in"
            else:  # login page: 'user' submits after a moment
                body = b"<script>setTimeout(()=>location='/login-done',800)</script>login form"
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sid = grab_session_cookie(tmp_path / "prof", url=f"http://127.0.0.1:{srv.server_port}/",
                                  domain_hint="127.0.0.1", timeout_s=30, executable_path=chrome, headless=True)
    finally:
        srv.shutdown()
    assert sid == "4242%3Aabc%3A1"


def test_after_post_never_raises_on_locked_files(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, after_post="delete")
    app = Reeler(cfg)
    reel = cfg.work_path / "clips" / "00007" / "reel.mp4"
    reel.parent.mkdir(parents=True)
    reel.write_bytes(b"x")
    src = tmp_path / "src.mp4"
    src.write_bytes(b"y")
    real_unlink = Path.unlink
    locked = {str(reel), str(src)}

    def flaky_unlink(self, missing_ok=False):
        if str(self) in locked:
            raise PermissionError(32, "The process cannot access the file because it is being used")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    app._after_post(src, 7)                       # must not raise (Windows WinError 32)
    assert set(app.state.kv_get("cleanup_later")) == locked
    locked.clear()                                # later the lock is gone
    app.sweep_cleanup()
    assert app.state.kv_get("cleanup_later") == [] and not reel.exists() and not src.exists()


# ================================================================ universal niche
def test_presets_and_simple_settings(tmp_path):
    cfg = make_cfg(tmp_path, preset="cooking", posts_per_day=7, language="Spanish",
                   signature="Follow for recipes", platforms="instagram+youtube")
    assert "cooking" in cfg.ai.niche and cfg.niche == cfg.ai.niche
    assert cfg.youtube.category_id == "26"
    assert len(cfg.schedule.post_times) == 7 and cfg.schedule.max_posts_per_day == 7
    assert cfg.caption.language == "Spanish" and cfg.caption.signature == "Follow for recipes"
    assert cfg.platforms == ["instagram", "youtube"]
    for name in ("gta6", "cooking", "football", "pets", "motivation"):
        c = make_cfg(tmp_path, preset=name)
        assert H.resolve_seed(c)["broad"], name


def test_old_style_config_with_ai_niche_still_works(tmp_path):
    cfg = make_cfg(tmp_path, ai={"enabled": False, "niche": "GTA 6 (Grand Theft Auto VI) gameplay clips"})
    assert cfg.niche == "GTA 6 (Grand Theft Auto VI) gameplay clips"


def test_any_niche_gets_hashtags_from_its_own_words(tmp_path):
    cfg = make_cfg(tmp_path, niche="football skills")
    app = Reeler(cfg)
    app.refresh_trends()
    tags = [r["tag"] for r in app.state.hashtags()]
    assert "footballskills" in tags and "football" in tags
    assert not any("gta" in t for t in tags)
    pool = H.pool_from_state(app.state)
    assert H.select_hashtags(pool, max_n=5)[0] == "footballskills"


def test_niche_kit_is_generated_once_and_cached(tmp_path, monkeypatch):
    from reeler import niche

    calls = []

    def fake_generate(cfg):
        calls.append(cfg.ai.niche)
        return {"niche": cfg.ai.niche, "topic": "makeup", "categories": ["tutorial", "glam", "other"],
                "hook_examples": ["TRY THIS LOOK"], "music_vibes": ["rnb"],
                "broad_tags": [{"tag": "makeup", "weight": 0.95}], "niche_tags": [{"tag": "glowymakeup", "weight": 0.5}],
                "category_tags": [{"category": "glam", "tags": ["glammakeup"]}]}

    monkeypatch.setattr(niche, "generate_kit", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = make_cfg(tmp_path, niche="makeup tutorials", ai={"enabled": True})
    seed = H.resolve_seed(cfg)
    assert seed["broad"] == {"makeup": 0.95} and seed["categories"]["glam"] == {"glammakeup": 0.5}
    H.resolve_seed(cfg)
    assert calls == ["makeup tutorials"]                      # cached after the first time
    prof = niche.load_profile(cfg)
    assert prof.categories == ["tutorial", "glam", "other"] and prof.hook_examples == ["TRY THIS LOOK"]


def test_prompt_and_schema_follow_the_niche(tmp_path):
    from reeler.analyzer import build_schema, system_prompt
    from reeler.niche import load_profile

    cfg = make_cfg(tmp_path, preset="pets", brand="Paw Patrolz")
    prof = load_profile(cfg)
    text = system_prompt(cfg, prof)
    assert "pets" in text and "Paw Patrolz" in text
    assert "GTA" not in text and "VICE" not in text.upper()
    assert build_schema(prof.categories)["properties"]["category"]["enum"] == prof.categories
    gta = load_profile(make_cfg(tmp_path, preset="gta6"))
    assert "car_chase" in gta.categories


def test_offline_templates_are_generic(tmp_path):
    from reeler.niche import load_profile

    prof = load_profile(make_cfg(tmp_path, niche="knitting"))
    for i in range(40):
        for stats in ({"scene_cuts_per_sec": 0.9, "mean_volume_db": -10}, {"scene_cuts_per_sec": 0.01},
                      {"scene_cuts_per_sec": 0.3}, {"scene_cuts_per_sec": 0.15}):
            a = analyze_offline(stats, 6, seed=str(i), profile=prof)
            blob = json.dumps(a).lower()
            assert "gta" not in blob and "vice" not in blob and "{topic}" not in blob
            assert a["hook_text"] and a["caption"] and a["title"]
    gta = analyze_offline({"scene_cuts_per_sec": 0.9, "mean_volume_db": -10}, 6, seed="1",
                          profile=load_profile(make_cfg(tmp_path, preset="gta6")))
    assert gta["music_search_terms"] == ["phonk", "drift phonk"]


def test_niche_change_starts_fresh_hashtag_pool(tmp_path):
    cfg = make_cfg(tmp_path, preset="gta6")
    app = Reeler(cfg)
    app.refresh_trends()
    assert any(r["tag"] == "gta6" for r in app.state.hashtags())
    cfg2 = make_cfg(tmp_path, niche="funny cats")
    app2 = Reeler(cfg2)
    assert app2.state.hashtags() == []
    app2.refresh_trends()
    assert not any("gta" in r["tag"] for r in app2.state.hashtags())


def test_discovery_uses_the_niche_not_gaming_words(tmp_path):
    cfg = make_cfg(tmp_path, preset="cooking", trends={"live_research": True, "weekly_query_budget": 2})
    app = Reeler(cfg)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")

    class FakeGraph:
        def hashtag_id(self, tag):
            return f"id_{tag}"

        def hashtag_media(self, hid, edge="top_media", limit=50):
            return [{"caption": "#cooking #airfryerrecipes #recipeoftheday #gta6leaks", "like_count": 900,
                     "comments_count": 9, "timestamp": now, "media_product_type": "REELS"}] * 3

    H.refresh_trends(app.state, FakeGraph(), cfg, force=True)
    tags = {r["tag"] for r in app.state.hashtags()}
    assert "recipeoftheday" in tags and "gta6leaks" not in tags


# ================================================================ scheduling / pacing
def test_spread_times_and_pacing_warnings():
    times, gap = scheduler.spread_times(5)
    assert times[0] == "09:00" and times[-1] == "23:00" and len(times) == 5 and gap >= 60
    assert len(scheduler.spread_times(100)[0]) == scheduler.MAX_PER_DAY
    assert scheduler.pacing_warning(5) is None
    assert "safe" in scheduler.pacing_warning(15)
    assert "spam" in scheduler.pacing_warning(100) or "allows" in scheduler.pacing_warning(100)


# ================================================================ multi-platform posting
class _Up:
    def __init__(self, platform, fail_times=0):
        self.platform, self.name, self.fail_times = platform, platform, fail_times
        self.supports_trending_audio = False
        self.calls = []

    def upload(self, video, caption, **kw):
        from reeler.uploaders.base import UploadResult
        self.calls.append(kw)
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError(f"{self.platform} is down")
        return UploadResult(f"{self.platform}-{len(self.calls)}", f"https://{self.platform}/x{len(self.calls)}")


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_each_platform_gets_each_clip_once(tmp_path):
    cfg = make_cfg(tmp_path, niche="football skills", platforms=["instagram", "youtube"], after_post="keep",
                   video={"width": 540, "height": 960})
    make_clip(tmp_path / "videos" / "a.mp4")
    app = Reeler(cfg)
    ig, yt = _Up("instagram"), _Up("youtube", fail_times=1)
    ups = {"instagram": ig, "youtube": yt}
    res = app.post_next(ups)
    assert not res["complete"] and "instagram" in res["platforms"]
    assert app.state.get(res["id"])["status"] == "failed"         # YouTube still to do
    res2 = app.post_next(ups)                                     # retry: only YouTube this time
    assert res2["complete"] and list(res2["platforms"]) == ["youtube"]
    assert len(ig.calls) == 1 and len(yt.calls) == 2
    meta = yt.calls[-1]["meta"]
    assert meta["title"].endswith("#Shorts") and "#Shorts" in meta["description"]
    assert app.state.platform_counts() == {"instagram": 1, "youtube": 1}
    assert app.post_next(ups) is None                              # never posted twice


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_dry_run_uploaders_for_both_platforms(tmp_path):
    from reeler.uploaders import make_uploaders
    cfg = make_cfg(tmp_path, niche="dance videos", platforms=["instagram", "youtube"],
                   video={"width": 540, "height": 960})
    make_clip(tmp_path / "videos" / "a.mp4")
    ups = make_uploaders(cfg)
    assert set(ups) == {"instagram", "youtube"} and all(u.name == "dry-run" for u in ups.values())
    res = Reeler(cfg).post_next(ups)
    assert res["complete"] and set(res["platforms"]) == {"instagram", "youtube"}


def test_old_database_history_is_migrated(tmp_path):
    import sqlite3
    from reeler.state import State

    db = tmp_path / "pc_state.db"
    s = State(db)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x" * 100)
    vid, _ = s.upsert_video(clip)
    s.update(vid, status="posted", media_id="m1", permalink="https://instagram.com/reel/1/", posted_at=1.0)
    # make it look like a database from before per-platform history existed
    s.conn.execute("DROP TABLE posts")
    s.conn.execute("DELETE FROM kv WHERE key='posts_migrated'")
    s.conn.commit()
    s.conn.close()
    s2 = State(db)
    assert s2.platform_posted(vid, "instagram") and not s2.platform_posted(vid, "youtube")
    assert s2.get(vid)["status"] == "posted"
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1


# ================================================================ YouTube uploader (mocked API)
class _FakeYT:
    def __init__(self, privacy_back="public"):
        self.privacy_back = privacy_back
        self.inserted = None

    def channels(self):
        outer = self

        class C:
            def list(self, **kw):
                class R:
                    def execute(self_inner):
                        return {"items": [{"snippet": {"title": "My Channel"}}]}
                return R()
        return C()

    def videos(self):
        outer = self

        class V:
            def insert(self, **kw):
                outer.inserted = kw

                class Req:
                    n = 0

                    def next_chunk(self_inner):
                        Req.n += 1
                        if Req.n == 1:
                            return None, None
                        return None, {"id": "yt123", "status": {"privacyStatus": outer.privacy_back}}
                return Req()
        return V()


def test_youtube_upload_sends_shorts_metadata(tmp_path, monkeypatch):
    from reeler.uploaders import youtube as Y

    cfg = make_cfg(tmp_path, preset="gta6", platforms=["youtube"])
    svc = _FakeYT(privacy_back="private")
    up = Y.YouTubeUploader(cfg, service=svc)
    assert up.check() == "YouTube channel 'My Channel'"
    video = tmp_path / "r.mp4"
    video.write_bytes(b"0" * 1000)
    opened = []

    class FakeMedia:
        def __init__(self, path):
            self._fd = open(path, "rb")
            opened.append(self._fd)

    monkeypatch.setattr(up, "_media", lambda v: FakeMedia(v))
    meta = Y.youtube_metadata({"title": "Cops had no chance <wow> #gta6 " + "x" * 120}, "Full send 🚔\n\n#gta6 #vicecity",
                              ["gta6", "vicecity"], cfg)
    assert len(meta["title"]) <= 100 and meta["title"].endswith(" #Shorts") and "<" not in meta["title"]
    assert "#gta6" not in meta["title"]
    res = up.upload(video, "cap", meta=meta)
    body = svc.inserted["body"]
    assert body["snippet"]["categoryId"] == "20" and body["status"]["privacyStatus"] == "public"
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert "shorts" in body["snippet"]["tags"] and "#Shorts" in body["snippet"]["description"]
    assert res.permalink == "https://youtube.com/shorts/yt123"
    assert res.notes and "private" in res.notes[0]               # unaudited Google project
    assert opened[0].closed                                      # file released (Windows can delete it)


def test_youtube_without_setup_gives_plain_instructions(tmp_path):
    pytest.importorskip("google_auth_oauthlib")
    from reeler.uploaders.youtube import YouTubeUploader
    cfg = make_cfg(tmp_path, platforms=["youtube"])
    with pytest.raises(RuntimeError, match="Turn on YouTube Shorts"):
        YouTubeUploader(cfg)._credentials()


# ================================================================ projects (profiles) + migration
def _legacy_root(tmp_path):
    from reeler.state import State
    root = tmp_path / "app"
    (root / ".reeler").mkdir(parents=True)
    st = State(root / ".reeler" / "pc_state.db")
    clip = root / "old.mp4"
    clip.write_bytes(b"z" * 100)
    vid, _ = st.upsert_video(clip)
    st.update(vid, status="posted", media_id="m", posted_at=1.0)
    st.kv_set("drive_done", {"DRIVEID": "posted"})
    st.conn.execute("DROP TABLE posts")                          # as written by the previous version
    st.conn.execute("DELETE FROM kv WHERE key='posts_migrated'")
    st.conn.commit()
    st.conn.close()
    (root / ".reeler" / "instagram.session.json").write_text("{}")
    (root / ".env").write_text("ANTHROPIC_API_KEY=\nIG_SESSIONID=legacy%3Asid\n")
    shutil.copyfile(Path(__file__).resolve().parent.parent / "config.pc.yaml", root / "config.pc.yaml")
    return root


def test_existing_gta_setup_becomes_a_project(tmp_path, monkeypatch):
    from reeler import config as C
    from reeler import profiles as P

    monkeypatch.setattr(C, "load_dotenv", None)
    root = _legacy_root(tmp_path)
    assert P.migrate_legacy(root) == "gta6"
    assert P.migrate_legacy(root) is None                         # only once
    cfg = load_config(P.profile_path(root, "gta6"), root=root)
    assert cfg.resolve(cfg.state_file) == (root / ".reeler" / "pc_state.db").resolve()
    assert cfg.work_path == (root / ".reeler").resolve()          # same login/session files as before
    assert cfg.secrets.ig_sessionid == "legacy%3Asid"
    assert "GTA 6" in cfg.ai.niche and cfg.platforms == ["instagram"]
    assert "drive.google.com" in cfg.drive.folder
    app = Reeler(cfg)
    assert app.state.kv_get("drive_done") == {"DRIVEID": "posted"}  # history kept: no re-uploads
    assert app.state.platform_counts() == {"instagram": 1}


def test_projects_do_not_share_accounts(tmp_path, monkeypatch):
    from reeler import config as C
    from reeler import profiles as P

    monkeypatch.setattr(C, "load_dotenv", None)
    monkeypatch.setenv("IG_SESSIONID", "someone-elses-session")
    root = tmp_path / "app"
    root.mkdir()
    P.write_profile(root, "cooking", P.new_profile_data("cooking", root, {"niche": "cooking"}))
    cfg = load_config(P.profile_path(root, "cooking"), root=root)
    assert cfg.secrets.ig_sessionid is None
    C.save_secret(cfg, "IG_SESSIONID", "cook%3Asid")
    cfg = load_config(P.profile_path(root, "cooking"), root=root)
    assert cfg.secrets.ig_sessionid == "cook%3Asid"
    assert (root / ".reeler" / "cooking" / "account.env").exists()
    assert cfg.resolve(cfg.state_file) == (root / ".reeler" / "cooking" / "state.db").resolve()


def test_setup_questions_create_and_reuse_a_project(tmp_path, monkeypatch, capsys):
    from reeler import config as C
    from reeler import profiles as P

    monkeypatch.setattr(C, "load_dotenv", None)
    root = tmp_path / "app"
    root.mkdir()
    clips = tmp_path / "My Clips"
    clips.mkdir()

    def script(answers):
        it = iter(answers)
        return lambda prompt="": next(it)

    # first run: name, folder, topic (preset 2 = cooking), 40/day -> warned -> no -> 6, Instagram
    name = P.choose_profile(script(["My Cooking!", "not/a/folder", f'"{clips}"', "2", "40", "n", "6", "1"]), root)
    assert name == "my-cooking"
    out = capsys.readouterr().out
    assert "spam" in out or "limited" in out
    data = P.read_profile(root, name)
    assert data["videos_dir"] == str(clips) and data["after_post"] == "keep" and data["posts_per_day"] == 6
    cfg = load_config(P.profile_path(root, name), root=root)
    assert cfg.videos_path == clips.resolve() and "cooking" in cfg.ai.niche
    assert len(cfg.schedule.post_times) == 6

    # later run: Enter = same project, then "change" -> Drive link, own topic, Enter keeps 6/day, both
    (root / "client_secret.json").write_text("{}")
    link = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123"
    name2 = P.choose_profile(script(["", "c", link, "vegan baking", "", "3"]), root)
    assert name2 == name
    data = P.read_profile(root, name)
    assert data["drive"]["folder"] == link and data["niche"] == "vegan baking" and "preset" not in data
    assert data["platforms"] == ["instagram", "youtube"] and data["posts_per_day"] == 6
    assert data["videos_dir"].endswith("downloads")
    # a second project sits next to it
    name3 = P.choose_profile(script(["n", "cars", link, "car repairs", "3", "1"]), root)
    assert P.list_profiles(root) == ["cars", "my-cooking"] and name3 == "cars"


def test_youtube_finds_key_file_with_googles_own_name(tmp_path):
    from reeler.uploaders.youtube import YouTubeUploader
    cfg = make_cfg(tmp_path, platforms=["youtube"])
    f = tmp_path / "client_secret_1042-abc.apps.googleusercontent.com.json"
    f.write_text("{}")
    assert YouTubeUploader(cfg, service=object()).secrets_file == f


# ================================================================ cloud link queue
def test_link_kinds_and_ids():
    from reeler import links as L
    assert L.classify("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing") == "drive_file"
    assert L.drive_file_id("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp") == "1AbCdEfGhIjKlMnOp"
    assert L.classify("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp") == "drive_folder"
    assert L.classify("https://www.dropbox.com/s/abc/clip.mp4?dl=0") == "dropbox"
    assert "dl=1" in L._dropbox_direct("https://www.dropbox.com/s/abc/clip.mp4?dl=0")
    assert L.classify("https://example.com/v/clip.MP4") == "direct"
    assert L.classify("https://www.youtube.com/shorts/abc123") == "site"
    assert L.link_id("https://x/y") == L.link_id(" https://x/y ") and L.link_id("https://x/y").startswith("L")


def test_read_links_skips_comments_and_duplicates(tmp_path):
    from reeler import links as L
    f = tmp_path / "links.txt"
    f.write_text("# header\n\nhttps://a/1.mp4\n  https://a/2.mp4  \nhttps://a/1.mp4\n")
    assert L.read_links(f) == ["https://a/1.mp4", "https://a/2.mp4"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_cloud_posts_pasted_links_in_order_once_each(tmp_path, monkeypatch):
    from reeler import links as L

    clip = make_clip(tmp_path / "src" / "a.mp4")
    clip2 = make_clip(tmp_path / "src" / "b.mp4", size="720x1280")
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue" / "links.txt").write_text(
        "# my clips\nhttps://example.com/first.mp4\nhttps://drive.google.com/file/d/1DriveFileIdXYZ/view\n")
    fetched = []

    def fake_fetch(item, dest):
        fetched.append(item["kind"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(clip if item["kind"] == "direct" else clip2, dest)
        return dest

    monkeypatch.setattr(L, "fetch", fake_fetch)
    cfg = make_cfg(tmp_path, preset="gta6", platforms=["youtube"], links={"file": "queue/links.txt"},
                   order="queue", after_post="delete", state_file="state/yt.db", video={"width": 540, "height": 960},
                   schedule={"max_posts_per_day": 5, "min_gap_minutes": 0, "timezone": "UTC", "post_times": ["10:00"]})
    yt = _Up("youtube")
    app = Reeler(cfg)
    r1 = app.cloud_post({"youtube": yt})
    r2 = app.cloud_post({"youtube": yt})
    assert fetched == ["direct", "drive_file"]                    # pasted order
    assert r1["complete"] and r2["complete"]
    assert "no clips" in app.cloud_post({"youtube": yt}).get("skipped", "") or \
        "already posted" in app.cloud_post({"youtube": yt})["skipped"]
    assert len(yt.calls) == 2 and yt.calls[0]["meta"]["title"].endswith("#Shorts")
    done = app.state.kv_get("drive_done")
    assert done["1DriveFileIdXYZ"] == "posted" and done[L.link_id("https://example.com/first.mp4")] == "posted"


def test_youtube_in_the_cloud_never_waits_for_a_browser(tmp_path, monkeypatch):
    pytest.importorskip("google_auth_oauthlib")
    from reeler.uploaders.youtube import YouTubeUploader
    monkeypatch.setenv("CI", "true")
    cfg = make_cfg(tmp_path, platforms=["youtube"])
    with pytest.raises(RuntimeError, match="YOUTUBE_TOKEN_JSON"):
        YouTubeUploader(cfg)._credentials()


def test_cloud_youtube_config_loads():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.youtube-cloud.yaml")
    assert cfg.platforms == ["youtube"] and cfg.links.file == "queue/links.txt" and cfg.order == "queue"
    assert len(cfg.schedule.post_times) == 5


def test_youtube_retries_on_a_fresh_connection_after_a_drop(tmp_path, monkeypatch):
    from reeler.uploaders import youtube as Y

    cfg = make_cfg(tmp_path, platforms=["youtube"])
    built = []

    class Svc(_FakeYT):
        pass

    def make_service():
        built.append(1)
        svc = Svc()
        if len(built) == 1:  # the first (old) connection was cut off while idle
            def broken_videos():
                class V:
                    def insert(self, **kw):
                        class R:
                            def next_chunk(self):
                                raise ConnectionAbortedError(10053, "An established connection was aborted")
                        return R()
                return V()
            svc.videos = broken_videos
        return svc

    up = Y.YouTubeUploader(cfg)
    monkeypatch.setattr(Y.YouTubeUploader, "service", property(lambda self: self._service or
                                                                setattr(self, "_service", make_service()) or self._service))
    monkeypatch.setattr(Y.time, "sleep", lambda s: None)
    video = tmp_path / "r.mp4"
    video.write_bytes(b"0" * 100)

    class FakeMedia:
        def __init__(self, path):
            self._fd = open(path, "rb")

    monkeypatch.setattr(up, "_media", lambda v: FakeMedia(v))
    res = up.upload(video, "cap", meta=Y.youtube_metadata({"title": "t"}, "cap", [], cfg))
    assert res.media_id == "yt123" and len(built) == 2


def test_other_youtube_errors_are_not_retried():
    from reeler.uploaders.youtube import _connection_problem
    assert _connection_problem(ConnectionAbortedError(10053, "aborted"))
    assert not _connection_problem(ValueError("quotaExceeded"))
