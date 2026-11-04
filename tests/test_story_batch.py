"""まとめて作る: /api/story/batch, cancel and script upload (story.make_story is faked)."""
import io
import threading

import pytest

import make_video as mv
import server
import story
from test_api import client, wait_job  # noqa: F401  (fixture + helper)


def write_script(dirpath, title, body="One paragraph here.\n\nAnother one."):
    p = dirpath / f"{story.slug(title)}.txt"
    p.write_text(f"{title}\n\n{body}\n")
    return p.name


@pytest.fixture
def renders(monkeypatch):
    """Fake renderer: writes an empty mp4; titles in `fail` raise. Records the order."""
    made, fail, gate = [], set(), {}

    def fake_make_story(script, outdir, s, log=print):
        data = story.load_script(script)
        if data["title"] in gate:
            gate[data["title"]].wait(10)
        if data["title"] in fail:
            raise RuntimeError("boom")
        out = outdir / f"{story.slug(data['title'])}.mp4"
        out.write_bytes(b"")
        made.append(data["title"])
        return out
    monkeypatch.setattr(story, "make_story", fake_make_story)
    monkeypatch.setattr(mv, "refresh_posting", lambda *a, **k: None)
    return made, fail, gate


def test_batch_runs_in_order_and_survives_a_failure(client, renders):
    made, fail, _ = renders
    names = [write_script(server.SCRIPTS, t) for t in ("Alpha Story", "Broken Story", "Gamma Story")]
    fail.add("Broken Story")
    r = client.post("/api/story/batch", json={"names": names, "options": {"tts_engine": "edge"}})
    assert r.status_code == 200, r.json
    j = wait_job(client, r.json["job"])
    assert made == ["Alpha Story", "Gamma Story"]                 # in order, failure skipped over
    assert [x["status"] for x in j["items"]] == ["done", "error", "done"]
    assert "boom" in j["items"][1]["error"]
    assert j["result"] == ["Alpha_Story.mp4", "Gamma_Story.mp4"]
    assert j["log"][-1].startswith("一括作成: 2 本完成 / 1 本失敗")
    assert j["items"][2]["log_from"] > j["items"][1]["log_from"]  # per-video log slices


def test_batch_skips_existing_videos_unless_overwrite(client, renders):
    made, _, _ = renders
    name = write_script(server.SCRIPTS, "Old Story")
    (server.VIDEOS / "Old_Story.mp4").write_bytes(b"x")
    j = wait_job(client, client.post("/api/story/batch", json={"names": [name]}).json["job"])
    assert j["items"][0]["status"] == "skipped" and made == []
    j = wait_job(client, client.post("/api/story/batch", json={"names": [name], "overwrite": True}).json["job"])
    assert j["items"][0]["status"] == "done" and made == ["Old Story"]
    assert list((server.VIDEOS / "_replaced").glob("Old_Story.*.mp4"))   # the old one is kept, not deleted


def test_batch_cancel_stops_the_rest(client, renders):
    made, _, gate = renders
    names = [write_script(server.SCRIPTS, t) for t in ("First One", "Second One", "Third One")]
    gate["First One"] = threading.Event()
    jid = client.post("/api/story/batch", json={"names": names}).json["job"]
    for _ in range(100):                                  # cancel while the first one is rendering
        if server.jobs[jid]["items"][0]["status"] == "running":
            break
        threading.Event().wait(0.05)
    assert client.post(f"/api/story/batch/{jid}/cancel").status_code == 200
    gate["First One"].set()
    j = wait_job(client, jid)
    assert made == ["First One"]
    assert [x["status"] for x in j["items"]] == ["done", "skipped", "skipped"]


def test_batch_rejects_bad_names_and_bad_scripts(client, renders):
    assert client.post("/api/story/batch", json={"names": []}).status_code == 400
    assert client.post("/api/story/batch", json={"names": ["../server.py"]}).status_code == 400
    bad = write_script(server.SCRIPTS, "Bad Tags", "Hello [[nope:x]]")
    r = client.post("/api/story/batch", json={"names": [bad]})
    assert r.status_code == 400 and "nope" in r.json["error"]
    assert client.post("/api/story/batch/nojob/cancel").status_code == 404


def test_upload_scripts(client):
    files = [(io.BytesIO(b"Uploaded Title\n\nA paragraph.\n"), "a.txt"),
             (io.BytesIO(b"Bad One\n\nHi [[nope:x]]\n"), "b.txt"),
             (io.BytesIO(b"not a script"), "c.mp3")]
    r = client.post("/api/story/scripts/upload", data={"files": files}, content_type="multipart/form-data")
    assert r.json["added"] == ["Uploaded_Title.txt"]
    assert {x["name"] for x in r.json["rejected"]} == {"b.txt", "c.mp3"}
    assert (server.SCRIPTS / "Uploaded_Title.txt").exists() and not (server.SCRIPTS / "Bad_One.txt").exists()
    o = client.get("/api/story/options").json["scripts"]
    assert {"name": "Uploaded_Title.txt", "paras": 1, "has_video": False}.items() <= o[0].items()
