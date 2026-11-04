"""HTTP API tests with Flask's test client (sandboxed paths, no real workers touched).
/api/auto/start and /stop are deliberately not called: they pkill real workers."""
import io, json, shutil, time
from pathlib import Path

import pytest

import make_video as mv
import server
from conftest import add_clips


@pytest.fixture
def client(sandbox, monkeypatch, tmp_path):
    for name, sub in (("VIDEOS", "videos"), ("UPLOADS", "uploads"), ("LOGS", "logs"), ("SCRIPTS", "scripts")):
        p = tmp_path / sub; p.mkdir(exist_ok=True)
        monkeypatch.setattr(server, name, p)
    monkeypatch.setattr(server, "UI_STATE", tmp_path / "ui_state.json")
    monkeypatch.setattr(server, "DEFAULT_SRC", tmp_path / "src")
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(server, "ensure_workers", lambda: None)   # never start real background threads
    for q in (server.analysis_q, server.render_q):
        while not q.empty():
            q.get_nowait()
    # never call the local LLM / Pexels: the publishing kit is faked (writes a minimal kit)
    monkeypatch.setattr(mv, "run_publish", fake_publish)
    server.app.testing = True
    return server.app.test_client()


PUBLISHED = []


def fake_publish(video, audio, log=print, force=False):
    video = Path(video)
    PUBLISHED.append((video.name, Path(audio).name, force))
    mv.write_json(video.parent / f"{video.stem}.publish.json", {
        "video": video.name, "title_en": "Title " + video.stem, "title_ja": "タイトル",
        "description_en_full": "d", "description_ja_full": "説明", "tags": ["t"], "hashtags": ["#h"],
        "thumbnail": video.stem + ".thumb.jpg"})
    return True


def wait_job(client, jid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json
        if j["status"] != "running":
            return j
        time.sleep(0.5)
    raise TimeoutError


def test_index_and_static(client):
    assert client.get("/").status_code == 200
    for f in ("app.js", "styles.css"):
        assert client.get(f"/static/{f}").status_code == 200


def test_settings_get_save_reset(client):
    s = client.get("/api/settings").json
    assert s == mv.DEFAULTS
    r = client.post("/api/settings", json={"cut": 6.0, "evil": 1}).json
    assert r["cut"] == 6.0 and "evil" not in r
    assert json.loads(mv.SETTINGS.read_text())["cut"] == 6.0
    assert client.post("/api/settings/reset").json == mv.DEFAULTS
    assert not mv.SETTINGS.exists()


def test_ui_state_defaults_merge_and_beacon(client):
    st = client.get("/api/ui-state").json
    assert st["page"] == "story" and st["workers"] == 3      # 作成(台本) is the main page
    client.post("/api/ui-state", json={"page": "videos", "junk": 1})
    # sendBeacon body arrives as text/plain-ish: must still be parsed
    client.post("/api/ui-state", data=json.dumps({"workers": 2}), content_type="text/plain")
    st = client.get("/api/ui-state").json
    assert st["page"] == "videos" and st["workers"] == 2 and "junk" not in st


def test_files_listing_marks_done_and_uploads(client, tmp_path, assets):
    src = tmp_path / "src"
    shutil.copy(assets["short"], src / "a.mp3"); shutil.copy(assets["short"], src / "b.mp3")
    (server.VIDEOS / "a.mp4").write_bytes(b"x")
    r = client.get("/api/files").json
    names = {f["name"]: f for f in r["files"]}
    assert names["a.mp3"]["done"] and not names["b.mp3"]["done"]
    assert client.get("/api/files?folder=/nope").json["error"]


def test_upload_then_generate(client, assets, monkeypatch):
    data = {"file": (io.BytesIO(assets["short"].read_bytes()), "../evil name.mp3")}
    up = client.post("/api/upload", data=data, content_type="multipart/form-data").json
    assert up["name"] == "evil name.mp3"                       # path traversal stripped
    assert (server.UPLOADS / "evil name.mp3").exists()
    add_clips(mv.LIB, assets["clip"], 10)
    client.post("/api/settings", json={"offline": True, "remove_music": False, "subs": False})
    jid = client.post("/api/generate", json={"path": up["path"], "overwrite": True}).json["job"]
    j = wait_job(client, jid)
    assert j["status"] == "done", j["log"]
    assert (server.VIDEOS / "evil name.mp4").exists()
    # same file again without overwrite -> refused, not regenerated
    jid = client.post("/api/generate", json={"path": up["path"], "overwrite": False}).json["job"]
    assert wait_job(client, jid)["status"] == "error"


def test_upload_rejects_empty(client):
    assert client.post("/api/upload", data={}, content_type="multipart/form-data").status_code == 400


def test_generate_missing_file(client):
    assert client.post("/api/generate", json={"path": "/nope.mp3"}).status_code == 400


def test_library_counts_and_recycle(client, assets):
    add_clips(mv.LIB, assets["clip"], 3)
    add_clips(mv.USED, assets["clip"], 2, prefix="9")
    r = client.get("/api/library").json
    assert (r["fresh"], r["used"]) == (3, 2) and r["size"] > 0
    assert client.post("/api/library/recycle").json["moved"] == 2
    assert client.get("/api/library").json["fresh"] == 5


def test_library_download_uses_fetch_new(client, monkeypatch):
    calls = []
    monkeypatch.setattr(mv, "pexels_key", lambda: "KEY")
    monkeypatch.setattr(mv, "fetch_new", lambda q, k, n, log=print: calls.append((q, n)) or n)
    jid = client.post("/api/library/download", json={"queries": ["a", " ", "b"], "per": 4}).json["job"]
    assert wait_job(client, jid)["status"] == "done"
    assert calls == [("a", 4), ("b", 4)]
    assert mv.load_settings()["queries"] == ["a", "b"]


def test_videos_list_media_range_and_traversal(client):
    (server.VIDEOS / "v.mp4").write_bytes(b"0123456789")
    assert client.get("/api/videos").json[0]["name"] == "v.mp4"
    r = client.get("/media/v.mp4", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206 and r.data == b"2345"            # seeking works
    assert client.get("/media/..%2Fsettings.json").status_code == 404
    assert client.get("/media/nope.mp4").status_code == 404


def test_reveal_ignores_paths_outside_videos(client, monkeypatch):
    opened = []
    monkeypatch.setattr(server.subprocess, "run", lambda cmd, **k: opened.append(cmd))
    client.post("/api/reveal", json={"name": "../settings.json"})
    assert opened == []


def test_auto_status(client, tmp_path, assets):
    shutil.copy(assets["short"], tmp_path / "src" / "a.mp3")
    (server.LOGS / "worker_1.log").write_text("== a.mp3\n  shots 1/3\n")
    r = client.get("/api/auto").json
    assert r["total"] == 1 and r["done"] == 0
    assert r["logs"][0]["lines"][-1].strip() == "shots 1/3"


# ---------------------------------------------------------------- inbox (upload page)
@pytest.fixture
def inbox(client, monkeypatch):
    """Queues are drained by hand (no background threads in tests)."""
    monkeypatch.setattr(server, "ensure_workers", lambda: None)
    for q in (server.analysis_q, server.render_q):
        while not q.empty():
            q.get_nowait()
    return client


def drain():
    while not server.analysis_q.empty():
        server.process_analysis(server.analysis_q.get_nowait())
    while not server.render_q.empty():
        server.process_render(server.render_q.get_nowait())


def fake_analysis(monkeypatch):
    def analyze(path, work, s, log=print, force=False):
        work.mkdir(parents=True, exist_ok=True)
        a = {"source": "voice", "total": 20.0, "music_db": -20.0, "sentences": [
            {"s": 0, "e": 4, "text": "download our app", "cut": True, "auto": True, "reason": "広告"},
            {"s": 5, "e": 15, "text": "never give up", "cut": False, "auto": False, "reason": ""},
            {"s": 16, "e": 20, "text": "subscribe", "cut": True, "auto": True, "reason": "登録"}]}
        mv.write_json(work / "analysis.json", a)
        return a
    monkeypatch.setattr(server.prep, "analyze", analyze)


def upload_files(c, *names, data=b"ID3fake"):
    files = [(io.BytesIO(data), n) for n in names]
    return c.post("/api/inbox/upload", data={"files": files}, content_type="multipart/form-data").json


def test_inbox_bulk_upload_filters_and_sanitizes(inbox, monkeypatch):
    fake_analysis(monkeypatch)
    r = upload_files(inbox, "a.mp3", "dir/b.WAV", "notes.txt", ".hidden.mp3", "../../c.m4a")
    assert sorted(r["added"]) == ["a.mp3", "b.WAV", "c.m4a"]
    assert sorted(r["skipped"]) == [".hidden.mp3", "notes.txt"]
    assert sorted(p.name for p in server.UPLOADS.iterdir() if p.is_file()) == ["a.mp3", "b.WAV", "c.m4a"]
    items = inbox.get("/api/inbox").json
    assert {i["status"] for i in items} == {"queued"}


def test_inbox_analysis_review_generate_delete(inbox, monkeypatch, tmp_path):
    fake_analysis(monkeypatch)
    upload_files(inbox, "speech.mp3")
    drain()
    item = inbox.get("/api/inbox").json[0]
    assert item["status"] == "ready" and item["cuts"] == 2
    assert item["final"] == pytest.approx(10.45)                  # keeps 4.85..15.3 (ads + their pauses cut)
    iid = item["id"]
    d = inbox.get(f"/api/inbox/{iid}").json
    assert len(d["sentences"]) == 3
    # user keeps the outro after all
    r = inbox.post(f"/api/inbox/{iid}/sentences", json={"cut": {"2": False, "99": True}}).json
    assert r["cuts"] == 1
    assert mv.read_json(mv.work_dir(server.UPLOADS / "speech.mp3") / "analysis.json", {})["sentences"][2]["cut"] is False

    made = []

    def fake_make(path, outdir, s, log=print):
        made.append(path.name)
        out = outdir / (path.stem + ".mp4"); out.write_bytes(b"v")
        return out
    monkeypatch.setattr(server.mv, "make", fake_make)
    assert inbox.post("/api/inbox/generate", json={"ids": [iid, "nope"]}).json["queued"] == [iid]
    assert inbox.get("/api/inbox").json[0]["status"] == "waiting"
    drain()
    item = inbox.get("/api/inbox").json[0]
    assert item["status"] == "done" and item["video"] == "speech.mp4" and item["video_exists"]
    assert made == ["speech.mp3"]

    # delete: audio + cache + record go, the finished video stays
    r = inbox.post("/api/inbox/delete", json={"ids": [iid]}).json
    assert r["deleted"] == ["speech.mp3"]
    assert inbox.get("/api/inbox").json == []
    assert not (server.UPLOADS / "speech.mp3").exists()
    assert not mv.work_dir(server.UPLOADS / "speech.mp3").exists()
    assert (server.VIDEOS / "speech.mp4").exists()


def test_inbox_busy_items_cannot_be_deleted_or_reanalyzed(inbox, monkeypatch):
    fake_analysis(monkeypatch)
    upload_files(inbox, "busy.mp3")
    iid = inbox.get("/api/inbox").json[0]["id"]
    server.update_meta(iid, status="generating")
    assert inbox.post("/api/inbox/delete", json={"ids": [iid]}).json["busy"] == ["busy.mp3"]
    assert inbox.post(f"/api/inbox/{iid}/reanalyze").status_code == 409
    assert (server.UPLOADS / "busy.mp3").exists()


def test_inbox_analysis_error_is_reported(inbox, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("whisper exploded")
    monkeypatch.setattr(server.prep, "analyze", broken)
    upload_files(inbox, "bad.mp3")
    drain()
    item = inbox.get("/api/inbox").json[0]
    assert item["status"] == "error" and "whisper exploded" in item["error"]
    # generate refused until analysis exists
    assert inbox.post("/api/inbox/generate", json={"ids": [item["id"]]}).json["queued"] == []


def test_inbox_render_failure_shows_reason(inbox, monkeypatch):
    fake_analysis(monkeypatch)
    upload_files(inbox, "f.mp3"); drain()
    iid = inbox.get("/api/inbox").json[0]["id"]

    def failing_make(path, outdir, s, log=print):
        log(f"  FAILED {path.name}: 素材がありません")
        return None
    monkeypatch.setattr(server.mv, "make", failing_make)
    inbox.post("/api/inbox/generate", json={"ids": [iid]}); drain()
    item = inbox.get("/api/inbox").json[0]
    assert item["status"] == "error" and "素材がありません" in item["error"]


def test_inbox_reupload_same_name_resets(inbox, monkeypatch):
    fake_analysis(monkeypatch)
    upload_files(inbox, "same.mp3"); drain()
    iid = inbox.get("/api/inbox").json[0]["id"]
    inbox.post(f"/api/inbox/{iid}/sentences", json={"cut": {"0": False}})
    upload_files(inbox, "same.mp3")
    items = inbox.get("/api/inbox").json
    assert len(items) == 1 and items[0]["status"] == "queued"
    assert not (mv.work_dir(server.UPLOADS / "same.mp3") / "analysis.json").exists()


def test_inbox_audio_prefers_voice_track(inbox, monkeypatch):
    fake_analysis(monkeypatch)
    upload_files(inbox, "v.mp3", data=b"ORIGINAL"); drain()
    iid = inbox.get("/api/inbox").json[0]["id"]
    assert inbox.get(f"/api/inbox/{iid}/audio").data == b"ORIGINAL"
    (mv.work_dir(server.UPLOADS / "v.mp3") / "voice.wav").write_bytes(b"VOICE")
    assert inbox.get(f"/api/inbox/{iid}/audio").data == b"VOICE"
    assert inbox.get("/api/inbox/doesnotexist/audio").status_code == 404


def test_recover_registers_dropped_files_and_requeues(inbox, monkeypatch):
    fake_analysis(monkeypatch)
    (server.UPLOADS / "dropped.mp3").write_bytes(b"x")
    upload_files(inbox, "mid.mp3")
    iid = next(i["id"] for i in inbox.get("/api/inbox").json if i["name"] == "mid.mp3")
    server.update_meta(iid, status="analyzing")             # server died mid-analysis
    while not server.analysis_q.empty():
        server.analysis_q.get_nowait()
    monkeypatch.setattr(server, "HERE", server.UPLOADS.parent)
    server.recover_inbox()
    drain()
    st = {i["name"]: i["status"] for i in inbox.get("/api/inbox").json}
    assert st == {"dropped.mp3": "ready", "mid.mp3": "ready"}


def test_pexels_key_saved_to_env_never_returned(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "HERE", tmp_path); monkeypatch.setattr(mv, "HERE", tmp_path)
    monkeypatch.setattr(mv, "CONFIG", tmp_path / "nope.toml"); monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert client.get("/api/pexels-key").json == {"has_key": False}
    assert client.post("/api/pexels-key", json={"key": "x y"}).status_code == 400
    r = client.post("/api/pexels-key", json={"key": "A" * 56})
    assert r.json == {"has_key": True} and "AAAA" not in r.get_data(as_text=True)
    env = tmp_path / ".env"
    assert env.read_text().strip() == "PEXELS_API_KEY=" + "A" * 56
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    assert mv.pexels_key() == "A" * 56


# ------------------------------------------------------------------ posts (投稿)
@pytest.fixture
def posts(client, monkeypatch, tmp_path):
    d = tmp_path / "videos_voice"; d.mkdir(exist_ok=True)
    monkeypatch.setattr(server, "VIDEOS", d)     # 投稿 and 完成動画 share one folder
    monkeypatch.setattr(mv, "duration", lambda p: 61.23)
    for n, stem in ((10, "10_Ten"), (2, "02_Two"), (3, "03_NoVideo")):
        meta = {"video": f"{stem}.mp4", "title_en": f"T{n}", "title_ja": f"タ{n}",
                "description_en_full": "desc", "description_ja_full": "説明",
                "tags": ["a", "b c"], "hashtags": ["#x"], "thumbnail": f"{stem}.thumb.jpg"}
        mv.write_json(d / f"{stem}.publish.json", meta)
        if stem != "03_NoVideo":
            (d / f"{stem}.mp4").write_bytes(b"0123456789")
    (d / "02_Two.thumb.jpg").write_bytes(b"jpg")
    (tmp_path / "secret.mp4").write_bytes(b"secret")
    return client, d


def test_posts_list_sorted_with_urls(posts):
    client, d = posts
    r = client.get("/api/posts").json
    assert [p["stem"] for p in r] == ["02_Two", "10_Ten"]          # numeric order, no-video skipped
    two = r[0]
    assert two["name"] == "Two" and two["number"] == 2 and two["duration"] == 61.2
    assert two["video_url"].startswith("/posts-media/02_Two.mp4?v=")
    assert two["thumbnail_url"].startswith("/posts-media/02_Two.thumb.jpg?v=")
    assert two["tags"] == ["a", "b c"] and two["posted"] is False and two["size"] == 10
    assert r[1]["thumbnail_url"] is None                            # missing thumb tolerated


def test_posts_media_range_and_traversal(posts):
    client, d = posts
    r = client.get("/posts-media/02_Two.mp4", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206 and r.data == b"2345"
    assert client.get("/posts-media/02_Two.thumb.jpg").data == b"jpg"
    assert client.get("/posts-media/..%2Fsecret.mp4").status_code == 404
    assert client.get("/posts-media/02_Two.publish.json").status_code == 404   # only media types
    assert client.get("/posts-media/nope.mp4").status_code == 404


def test_posts_posted_toggle_persists(posts):
    client, d = posts
    r = client.post("/api/posts/02_Two/posted", json={"posted": True}).json
    assert r["posted"] and r["posted_at"]
    assert json.loads((d / "_posted.json").read_text())["02_Two"]["posted"] is True
    p = {x["stem"]: x for x in client.get("/api/posts").json}
    assert p["02_Two"]["posted"] and not p["10_Ten"]["posted"]
    client.post("/api/posts/02_Two/posted", json={"posted": False})
    assert "02_Two" not in json.loads((d / "_posted.json").read_text())
    assert client.post("/api/posts/nope/posted", json={"posted": True}).status_code == 404
    assert client.post("/api/posts/..%2F..%2Fx/posted", json={"posted": True}).status_code == 404


def test_posts_download_and_reveal(posts, monkeypatch):
    client, d = posts
    r = client.get("/api/posts/02_Two/download/thumbnail")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert client.get("/api/posts/10_Ten/download/thumbnail").status_code == 404
    assert client.get("/api/posts/02_Two/download/evil").status_code == 404
    opened = []
    monkeypatch.setattr(server.subprocess, "run", lambda cmd, **k: opened.append(cmd))
    assert client.post("/api/posts/reveal", json={"name": "../secret.mp4"}).json["ok"] is False
    client.post("/api/posts/reveal", json={"name": "02_Two.mp4"})
    assert opened == [["open", "-R", str((d / "02_Two.mp4").resolve())]]


def test_ui_state_keeps_posts_filter(client):
    client.post("/api/ui-state", json={"page": "posts", "posts_filter": "todo", "posts_search": "abc"})
    st = client.get("/api/ui-state").json
    assert (st["page"], st["posts_filter"], st["posts_search"]) == ("posts", "todo", "abc")


# ------------------------------------------------------------------ automatic flow
def test_single_output_folder():
    """Inbox renders, auto workers, 完成動画 and 投稿 all use videos_voice/."""
    # no fixture here: these are the real module-level values
    assert server.VIDEOS == server.POSTS == mv.HERE / "output" / "videos" == mv.OUTPUT
    import inspect
    assert "default=str(OUTPUT)" in inspect.getsource(mv)


def test_rendered_video_shows_on_videos_and_posts_pages(posts):
    client, d = posts
    (d / "new.mp4").write_bytes(b"v")                       # finished, kit not made yet
    assert "new.mp4" in [v["name"] for v in client.get("/api/videos").json]
    p = {x["stem"]: x for x in client.get("/api/posts").json}
    assert p["new"]["has_kit"] is False and p["new"]["number"] >= 10**9 and p["new"]["title_en"] == ""
    assert p["02_Two"]["has_kit"] is True and p["02_Two"]["qa"] is None
    assert client.post("/api/posts/new/posted", json={"posted": True}).json["posted"]


def test_posts_include_qa_summary(posts):
    client, d = posts
    mv.write_json(d / "02_Two.qa.json", {"status": "warn", "reasons": ["字幕と声のずれ: 0.6 秒"],
                                         "checks": [{"name": "sync", "label": "字幕と声のずれ", "status": "warn",
                                                     "detail": "0.6"}],
                                         "sync": {"median": 0.6, "max": 1.1}, "checked_at": 1})
    q = {x["stem"]: x for x in client.get("/api/posts").json}["02_Two"]["qa"]
    assert q["status"] == "warn" and q["sync_median"] == 0.6 and q["sync_max"] == 1.1
    assert q["checks"][0]["label"] == "字幕と声のずれ" and q["reasons"]


def test_republish_runs_in_background_with_force(posts, monkeypatch):
    client, d = posts
    PUBLISHED.clear()
    refreshed = []
    monkeypatch.setattr(mv, "refresh_posting", lambda vdir, log=print: refreshed.append(vdir))
    r = client.post("/api/posts/02_Two/republish").json
    j = wait_job(client, r["job"])
    assert j["status"] == "done", j["log"]
    assert PUBLISHED == [("02_Two.mp4", "02_Two.mp3", True)] and refreshed == [d]
    assert client.post("/api/posts/nope/republish").status_code == 404
    assert client.post("/api/posts/..%2Fsecret/republish").status_code == 404


def test_republish_failure_reported(posts, monkeypatch):
    client, d = posts
    monkeypatch.setattr(mv, "run_publish", lambda *a, **k: False)
    j = wait_job(client, client.post("/api/posts/10_Ten/republish").json["job"])
    assert j["status"] == "error"


def test_find_audio_prefers_qa_record_then_uploads(posts, tmp_path):
    client, d = posts
    a = tmp_path / "elsewhere.m4a"; a.write_bytes(b"x")
    mv.write_json(d / "02_Two.qa.json", {"audio": str(a)})
    assert server.find_audio("02_Two") == a
    (server.UPLOADS / "10_Ten.wav").write_bytes(b"x")
    assert server.find_audio("10_Ten") == server.UPLOADS / "10_Ten.wav"
    assert server.find_audio("[odd]*name").name == "[odd]*name.mp3"   # glob characters are literal


def render_fixture(monkeypatch):
    def fake_make(path, outdir, s, log=print):
        out = outdir / (path.stem + ".mp4"); out.write_bytes(b"v")
        return out
    monkeypatch.setattr(server.mv, "make", fake_make)


def test_inbox_render_runs_publish_qa_posting_with_statuses(inbox, monkeypatch):
    import qa
    fake_analysis(monkeypatch); render_fixture(monkeypatch)
    upload_files(inbox, "auto.mp3"); drain()
    iid = inbox.get("/api/inbox").json[0]["id"]
    seen = {}

    def publish(video, audio, log=print, force=False):
        seen["publish"] = server.load_meta(iid)["status"]
        return fake_publish(video, audio, log, force)

    def check(video, audio, work, log=print, s=None):
        seen["qa"] = server.load_meta(iid)["status"]
        r = {"status": "pass", "reasons": [], "audio": str(audio)}
        mv.write_json(qa.qa_path(video), r)
        return r
    monkeypatch.setattr(mv, "run_publish", publish)
    monkeypatch.setattr(qa, "check_video", check)
    inbox.post("/api/inbox/generate", json={"ids": [iid]}); drain()
    assert seen == {"publish": "publishing", "qa": "checking"}
    item = inbox.get("/api/inbox").json[0]
    assert item["status"] == "done" and item["publish"] == "ok" and item["qa"] == "pass" and not item["warning"]
    assert (server.VIDEOS / "auto.publish.json").exists()
    folder = mv.POSTING / "Title auto"                        # posting folder refreshed (sandboxed)
    assert (folder / "video.mp4").exists() and (folder / "post.txt").exists()
    # appears on the 投稿 page with its kit
    monkeypatch.setattr(mv, "duration", lambda p: 1.0)
    p = {x["stem"]: x for x in inbox.get("/api/posts").json}["auto"]
    assert p["has_kit"] and p["title_en"] == "Title auto" and p["qa"]["status"] == "pass"


def test_inbox_publish_and_qa_failures_keep_the_video(inbox, monkeypatch):
    import qa
    fake_analysis(monkeypatch); render_fixture(monkeypatch)
    upload_files(inbox, "warn.mp3"); drain()
    iid = inbox.get("/api/inbox").json[0]["id"]

    def boom(*a, **k):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(mv, "run_publish", boom)
    monkeypatch.setattr(qa, "check_video", lambda *a, **k: {"status": "fail", "reasons": ["長さ: 差 3 秒"]})
    inbox.post("/api/inbox/generate", json={"ids": [iid]}); drain()
    item = inbox.get("/api/inbox").json[0]
    assert item["status"] == "done" and item["video_exists"]
    assert item["publish"] == "error" and item["qa"] == "fail"
    assert "ollama down" in item["warning"] and "長さ" in item["warning"]


def test_inbox_auto_steps_can_be_turned_off(inbox, monkeypatch):
    import qa
    fake_analysis(monkeypatch); render_fixture(monkeypatch)
    inbox.post("/api/settings", json={"auto_publish": False, "qa": False})
    calls = []
    monkeypatch.setattr(mv, "run_publish", lambda *a, **k: calls.append("p"))
    monkeypatch.setattr(qa, "check_video", lambda *a, **k: calls.append("q"))
    upload_files(inbox, "off.mp3"); drain()
    iid = inbox.get("/api/inbox").json[0]["id"]
    inbox.post("/api/inbox/generate", json={"ids": [iid]}); drain()
    item = inbox.get("/api/inbox").json[0]
    assert calls == [] and item["status"] == "done" and item["publish"] == "skipped"


def test_render_never_deletes_someone_elses_video(inbox, monkeypatch):
    fake_analysis(monkeypatch); render_fixture(monkeypatch)
    (server.VIDEOS / "mine.mp4").write_bytes(b"USER VIDEO")   # e.g. one of the 68 finished videos
    upload_files(inbox, "mine.mp3"); drain()
    iid = inbox.get("/api/inbox").json[0]["id"]
    inbox.post("/api/inbox/generate", json={"ids": [iid]}); drain()
    kept = list((server.VIDEOS / "_replaced").glob("mine.*.mp4"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"USER VIDEO"
    # regenerating our own render replaces it without another backup
    inbox.post("/api/inbox/generate", json={"ids": [iid]}); drain()
    assert len(list((server.VIDEOS / "_replaced").glob("mine.*.mp4"))) == 1


def test_busy_statuses_block_delete_and_recover_marks_done(inbox, monkeypatch):
    fake_analysis(monkeypatch)
    upload_files(inbox, "pub.mp3")
    iid = inbox.get("/api/inbox").json[0]["id"]
    for st in ("publishing", "checking"):
        server.update_meta(iid, status=st)
        assert inbox.post("/api/inbox/delete", json={"ids": [iid]}).json["busy"] == ["pub.mp3"]
        assert inbox.post(f"/api/inbox/{iid}/reanalyze").status_code == 409
    monkeypatch.setattr(server, "HERE", server.UPLOADS.parent)
    server.recover_inbox()
    m = server.load_meta(iid)
    assert m["status"] == "done" and "中断" in m["warning"]


# ---------------------------------------------------------------- rights (権利確認)
def test_rights_list_check_youtube_and_source(client, monkeypatch):
    import rights
    v = server.VIDEOS / "07_Lion.mp4"; v.write_bytes(b"v")
    mv.write_json(server.VIDEOS / "07_Lion.publish.json", {"video": "07_Lion.mp4", "title_en": "Be a Lion",
                                                          "speech_source": "要確認", "tags": [], "hashtags": [],
                                                          "description_en_full": "", "description_ja_full": ""})
    items = client.get("/api/rights").json["items"]
    assert items[0]["risk"] == "unknown" and items[0]["shazam"]["status"] == "unchecked"

    # Shazam mocked: a match -> high risk + detected source
    def fake_recognize(video, log=print):
        rec = {"status": "match", "matches": [{"at": 10, "title": "The Lion Attitude", "artist": "Fearless Motivation",
                                               "label": "", "url": "https://x"}], "checked_at": 1, "error": None}
        r = rights.load(video); r["shazam"] = rec; rights.save(video, r)
        return rec
    monkeypatch.setattr(rights, "recognize", fake_recognize)
    jid = client.post("/api/rights/07_Lion/check").json["job"]
    assert wait_job(client, jid)["status"] == "done"
    it = client.get("/api/rights").json["items"][0]
    assert it["risk"] == "high" and it["detected_source"] == "Fearless Motivation"

    # YouTube result recorded; claim beats the Shazam signal
    assert client.post("/api/rights/07_Lion/youtube", json={"status": "bogus"}).status_code == 400
    it = client.post("/api/rights/07_Lion/youtube", json={"status": "claim", "note": "Content ID"}).json
    assert it["risk"] == "claimed" and it["youtube"]["note"] == "Content ID"
    assert mv.read_json(server.VIDEOS / "07_Lion.rights.json", {})["youtube"]["status"] == "claim"

    # source edit goes into publish.json
    monkeypatch.setattr(mv, "refresh_posting", lambda *a, **k: None)
    it = client.post("/api/rights/07_Lion/source", json={"source": "Fearless Motivation"}).json
    assert it["speech_source"] == "Fearless Motivation"
    assert mv.read_json(server.VIDEOS / "07_Lion.publish.json", {})["speech_source"] == "Fearless Motivation"
    assert client.post("/api/rights/..%2Fx/check").status_code == 404


def test_rights_check_all_only_unchecked(client, monkeypatch):
    import rights
    for n in ("01_a", "02_b"):
        (server.VIDEOS / f"{n}.mp4").write_bytes(b"v")
    rights.save(server.VIDEOS / "01_a.mp4", {"shazam": {"status": "none", "matches": []}})
    seen = []
    monkeypatch.setattr(rights, "recognize", lambda v, log=print: seen.append(v.stem) or {"status": "none"})
    r = client.post("/api/rights/check-all", json={}).json
    assert r["count"] == 1 and wait_job(client, r["job"])["status"] == "done" and seen == ["02_b"]


def test_music_api(client, monkeypatch, tmp_path):
    import music
    music.DIR.mkdir(exist_ok=True)
    (music.DIR / "a.mp3").write_bytes(b"ID3")
    music.save_tracks([{"title": "A", "file": "a.mp3", "feel": ["epic"], "length": 120, "enabled": True, "uses": 0}])
    lst = client.get("/api/music").json
    assert lst[0]["title"] == "A" and lst[0]["url"].startswith("/music-media/")
    assert client.get(lst[0]["url"]).status_code == 200
    assert client.get("/music-media/..%2Ftracks.json").status_code == 404
    assert client.post("/api/music/a.mp3/enabled", json={"enabled": False}).json["enabled"] is False
    assert music.load_tracks()[0]["enabled"] is False
    assert client.post("/api/music/nope.mp3/enabled", json={"enabled": True}).status_code == 404


def test_open_posting_folder_uses_tool_folder(client, monkeypatch):
    opened = []
    monkeypatch.setattr(server.subprocess, "run", lambda cmd, **k: opened.append(cmd))
    r = client.post("/api/posts/open-folder").json
    assert r["path"] == str(mv.POSTING) and opened == [["open", str(mv.POSTING)]]


def test_story_options_and_validation(client):
    o = client.get("/api/story/options").json
    assert o["voices"] and "bhatt" in o["presets"] and "kinetic_ready" in o
    assert client.post("/api/story/generate", json={"title": "", "text": ""}).status_code == 400
    bad = client.post("/api/story/generate", json={"title": "Bad Tag", "text": "Hi there [[nope:x]]"})
    assert bad.status_code == 400 and "nope" in bad.json["error"]
    assert client.post("/api/story/draft", json={"theme": ""}).status_code == 400
    assert client.get("/api/story/scripts/..%2Fserver.py").status_code == 404


def test_story_settings_reject_bad_rate():
    import server
    _, s = server.story_settings({"preset": "bhatt", "options": {"tts_rate": "fast; rm", "voice": "x",
                                                                   "unknown": 1}})
    assert s["tts_rate"] == "+0%" and "unknown" not in s


def test_caption_fonts_api(client):
    fonts = {f["id"]: f for f in client.get("/api/fonts").json}
    assert {"montserrat", "tight", "serif_it"} <= set(fonts)
    assert client.get(fonts["serif_it"]["url"]).status_code == 200
    assert client.get("/api/fonts/file/..%2F..%2Fserver.py").status_code == 404
    assert client.post("/api/fonts/remove", json={"id": "tight"}).status_code == 400   # built-ins stay
    assert client.post("/api/fonts", json={"query": ""}).status_code == 400
