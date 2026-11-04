"""自分の声: upload a recorded voice, list / play / delete it, and hand it to Dia's job."""
import io
from pathlib import Path

import pytest

import make_video as mv
import server
import story
from test_api import client  # noqa: F401

ASSETS = Path(__file__).parent / ".assets"


@pytest.fixture
def voices_dir(monkeypatch, tmp_path):
    d = tmp_path / "voices"
    monkeypatch.setattr(story, "MY_VOICES", d)
    return d


def upload(client, path, **form):
    data = {"file": (io.BytesIO(Path(path).read_bytes()), Path(path).name), **form}
    return client.post("/api/story/voices", data=data, content_type="multipart/form-data")


def test_add_list_play_delete(client, voices_dir):
    r = upload(client, ASSETS / "speech_8s.wav", name="Me", text="This is my own voice for the narration test.")
    assert r.status_code == 400 and "許可" in r.json["error"]              # consent box is required
    r = upload(client, ASSETS / "speech_8s.wav", name="Me", consent="1",
               text="This is my own voice for the narration test.")
    assert r.status_code == 200, r.json
    vid = r.json["id"]
    assert [v["name"] for v in client.get("/api/story/voices").json] == ["Me"]
    assert 3 <= client.get("/api/story/voices").json[0]["seconds"] <= 15
    assert client.get(f"/api/story/voices/{vid}/audio").status_code == 200
    assert client.get("/api/story/options").json["my_voices"][0]["id"] == vid
    assert client.get("/api/story/voices/..%2F..%2Fserver/audio").status_code == 404
    assert client.post(f"/api/story/voices/{vid}/delete").json["voices"] == []
    assert not list(voices_dir.iterdir())


def test_rejects_short_and_non_audio(client, voices_dir, tmp_path):
    short = tmp_path / "short.wav"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=220:d=1", str(short)])
    r = upload(client, short, consent="1", text="one two three four")
    assert r.status_code == 400 and "短すぎ" in r.json["error"]
    bad = tmp_path / "notes.txt"; bad.write_text("hello")
    assert upload(client, bad, consent="1", text="one two three").status_code == 400


def test_dia_job_uses_the_recorded_voice(voices_dir, tmp_path, monkeypatch):
    voices_dir.mkdir()
    (voices_dir / "abc123def0.wav").write_bytes(b"RIFF")
    mv.write_json(voices_dir / "abc123def0.json", {"name": "Me", "text": "hello there my friend"})
    seen = {}

    class Stop(Exception):
        pass

    def fake_popen(cmd, **kw):
        seen.update(mv.read_json(Path(cmd[2]), {}))
        raise Stop
    monkeypatch.setattr(story.subprocess, "Popen", fake_popen)
    sc = [{"text": "Walk alone."}]
    with pytest.raises(Stop):
        story.dia_voice(sc, [tmp_path / "v.wav"], {"dia_seed": 33, "dia_voice": "abc123def0"}, log=lambda *a: None)
    assert seen["ref_audio"].endswith("abc123def0.wav") and seen["ref_text"] == "hello there my friend"
    assert story.voice_label({"dia_voice": "abc123def0"}) == "Dia Me"
    assert story.my_voice("../etc") is None
