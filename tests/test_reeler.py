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
    cfg = make_cfg(tmp_path, trends={"live_research": True, "weekly_query_budget": 3})
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
    cfg = make_cfg(tmp_path, video={"width": 540, "height": 960})
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
