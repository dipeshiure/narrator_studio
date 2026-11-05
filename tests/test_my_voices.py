"""自分の声: upload a recorded voice, list / play / delete it, and hand it to CosyVoice's job."""
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
    voices = client.get("/api/story/voices").json
    assert [v["name"] for v in voices] == ["Atlas", "Orion", "Titan", "Me"]       # built-in first, then yours
    assert 3 <= voices[-1]["seconds"] <= 15
    assert client.get(f"/api/story/voices/{vid}/audio").status_code == 200
    assert client.get("/api/story/options").json["voices"][-1]["id"] == vid
    assert client.get("/api/story/voices/..%2F..%2Fserver/audio").status_code == 404
    assert [v["id"] for v in client.post(f"/api/story/voices/{vid}/delete").json["voices"]] == ["atlas", "orion", "titan"]
    assert not list(voices_dir.iterdir())


def test_rejects_short_and_non_audio(client, voices_dir, tmp_path):
    short = tmp_path / "short.wav"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=220:d=1", str(short)])
    r = upload(client, short, consent="1", text="one two three four")
    assert r.status_code == 400 and "短すぎ" in r.json["error"]
    bad = tmp_path / "notes.txt"; bad.write_text("hello")
    assert upload(client, bad, consent="1", text="one two three").status_code == 400


def test_cosy_job_uses_the_chosen_voice(voices_dir, tmp_path, monkeypatch):
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
    for vid, ref in (("abc123def0", "abc123def0.wav"), ("atlas", "atlas.wav"), ("nope", "atlas.wav")):   # unknown -> Atlas
        with pytest.raises(Stop):
            story.cosy_voice([{"text": "Walk alone."}], [tmp_path / f"{vid}.wav"], {"voice_id": vid}, log=lambda *a: None)
        assert seen["ref_audio"].endswith(ref)
    assert seen["ref_text"].startswith("Every man reaches") and seen["items"][0]["text"] == "Walk alone."
    assert story.voice_label({"voice_id": "abc123def0"}) == "CosyVoice 2 Me"
    assert story.my_voice("../etc") is None


def test_builtin_voices_are_listed_first_and_cannot_be_deleted(client, voices_dir):
    names = [v["id"] for v in client.get("/api/story/voices").json]
    assert names[:3] == ["atlas", "orion", "titan"]
    assert client.post("/api/story/voices/atlas/delete").status_code == 400
    assert server.story_settings({"options": {"voice_id": "bogus"}})[1]["voice_id"] == "atlas"
    assert server.story_settings({"options": {"tts_engine": "dia"}})[1]["tts_engine"] == "cosy"   # old setting
