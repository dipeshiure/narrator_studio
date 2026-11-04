"""qa.check_video on small generated videos (Whisper is mocked for the sync check)."""
import shutil

import pytest

import make_video as mv
import qa
from conftest import ffmpeg

WORDS = [{"w": w, "s": round(i * 0.5, 3), "e": round(i * 0.5 + 0.4, 3)} for i, w in enumerate(
    "never give up on yourself because the pain you feel today will be the strength you feel tomorrow".split())]


@pytest.fixture
def made(sandbox, assets, tmp_path):
    """An 8 s 1920x1080 video with audio + the work-dir records a render leaves."""
    audio = tmp_path / "speech.wav"; shutil.copy(assets["wav"], audio)
    vdir = tmp_path / "out"; vdir.mkdir()
    video = vdir / "speech.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=8", "-i", str(audio),
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video))
    work = mv.work_dir(audio); work.mkdir(parents=True)
    mv.write_json(work / "render.json", {"total": mv.duration(audio), "subs": True, "aspect": "16:9",
                                         "size": [1920, 1080]})
    mv.write_json(work / "words_render.json", WORDS)
    mv.write_simple_ass(WORDS, work / "subs.ass", mv.load_settings())
    return video, audio, work


def heard(shift):
    return lambda clip, model, offset=0.0: [{**w, "s": w["s"] + shift, "e": w["e"] + shift} for w in WORDS]


def status(r, name):
    return next(c for c in r["checks"] if c["name"] == name)["status"]


def test_good_video_passes_and_is_saved(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", heard(0.12))
    r = qa.check_video(video, audio, work, log=lambda m: None)
    assert r["status"] == "pass", r["reasons"]
    assert {c["name"]: c["status"] for c in r["checks"]} == {
        "duration": "pass", "decode": "pass", "audio": "pass", "resolution": "pass",
        "subtitles": "pass", "ads": "pass", "sync": "pass"}
    assert r["sync"]["median"] == pytest.approx(0.12) and r["sync"]["matched"] >= 10
    assert mv.read_json(video.with_name("speech.qa.json"), {})["status"] == "pass"


def test_sync_offsets_warn_and_fail(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", heard(0.7))
    assert status(qa.check_video(video, audio, work, log=lambda m: None), "sync") == "warn"
    monkeypatch.setattr(mv, "whisper_words", heard(1.6))
    r = qa.check_video(video, audio, work, log=lambda m: None)
    assert status(r, "sync") == "fail" and r["status"] == "fail"
    assert any("ずれ" in x for x in r["reasons"])


def test_sync_too_few_matches_is_warning(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", lambda *a, **k: [{"w": "hello", "s": 1.0, "e": 1.2}])
    assert status(qa.check_video(video, audio, work, log=lambda m: None), "sync") == "warn"


def test_sync_can_be_skipped(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", lambda *a, **k: pytest.fail("whisper must not run"))
    r = qa.check_video(video, audio, work, log=lambda m: None, s={**mv.load_settings(), "qa_sync": False})
    assert status(r, "sync") == "skip" and r["status"] == "pass"


def test_duration_mismatch_fails(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", heard(0))
    mv.write_json(work / "render.json", {**mv.read_json(work / "render.json", {}), "total": 20.0})
    r = qa.check_video(video, audio, work, log=lambda m: None)
    assert status(r, "duration") == "fail" and r["status"] == "fail"


def test_wrong_resolution_fails(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", heard(0))
    mv.write_json(work / "render.json", {**mv.read_json(work / "render.json", {}), "aspect": "9:16",
                                         "size": [1080, 1920]})
    assert status(qa.check_video(video, audio, work, log=lambda m: None), "resolution") == "fail"


def test_ad_phrase_left_in_captions_warns(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", heard(0))
    words = WORDS + [{"w": w, "s": 7.0 + i * 0.1, "e": 7.05 + i * 0.1} for i, w in
                     enumerate("thanks for watching".split())]
    mv.write_json(work / "words_render.json", words)
    mv.write_simple_ass(words, work / "subs.ass", mv.load_settings())
    r = qa.check_video(video, audio, work, log=lambda m: None)
    assert status(r, "ads") == "warn" and r["status"] == "warn"
    assert "thanks for watching" in next(c for c in r["checks"] if c["name"] == "ads")["detail"]


def test_missing_subtitles_fail_and_off_is_skipped(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", heard(0))
    (work / "subs.ass").unlink()
    assert status(qa.check_video(video, audio, work, log=lambda m: None), "subtitles") == "fail"
    mv.write_json(work / "render.json", {**mv.read_json(work / "render.json", {}), "subs": False})
    r = qa.check_video(video, audio, work, log=lambda m: None)
    assert status(r, "subtitles") == "skip" and status(r, "sync") == "skip" and r["status"] == "pass"


def test_no_audio_stream_fails(made, tmp_path, monkeypatch):
    video, audio, work = made
    silent = video.with_name("silent.mp4")
    ffmpeg("-i", str(video), "-an", "-c", "copy", str(silent))
    r = qa.check_video(silent, audio, work, log=lambda m: None)
    assert status(r, "audio") == "fail" and status(r, "sync") == "skip"


def test_corrupt_file_fails_decode(made, monkeypatch):
    video, audio, work = made
    monkeypatch.setattr(mv, "whisper_words", heard(0))
    data = bytearray(video.read_bytes())
    for i in range(len(data) // 3, len(data) * 2 // 3, 97):   # damage the middle of the stream
        data[i] ^= 0xFF
    video.write_bytes(bytes(data))
    r = qa.check_video(video, audio, work, log=lambda m: None)
    assert status(r, "decode") == "fail" and r["status"] == "fail"


def test_expected_length_without_render_record_uses_kept_ranges(made):
    video, audio, work = made
    (work / "render.json").unlink()
    mv.write_json(work / "analysis.json", {"total": 10.0, "sentences": [
        {"s": 0, "e": 2, "cut": True}, {"s": 2.5, "e": 10, "cut": False}]})
    assert qa.expected_audio(audio, work, mv.load_settings()) == pytest.approx(10 - 2.35)


def test_match_offsets_uses_nearest_identical_sequence():
    ref = [{"w": w, "s": float(i)} for i, w in enumerate("a b c x a b c".split())]
    got = [{"w": w.upper() + ",", "s": 4.3 + i} for i, w in enumerate("a b c".split())]
    assert qa.match_offsets(got, ref) == [0.3]


def test_caption_words_fallback_for_older_renders(made):
    video, audio, work = made
    (work / "words_render.json").unlink()
    mv.write_json(work / "words_voice.json", WORDS)
    mv.write_json(work / "analysis.json", {"source": "voice", "total": 10.0, "sentences": [
        {"s": 0.0, "e": 0.9, "cut": True}, {"s": 1.0, "e": 10.0, "cut": False}]})
    words = qa.caption_words(work, mv.load_settings())
    assert words[0]["w"] == "up" and words[0]["s"] == pytest.approx(0.1)   # cut words dropped, timeline shifted
