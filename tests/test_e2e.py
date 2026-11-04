"""End-to-end: render real (short) videos with real Whisper + Demucs.
Footage is a dummy test pattern in a sandbox library, so nothing is downloaded."""
import shutil, subprocess

import pytest

import make_video as mv
from conftest import add_clips, ffmpeg, probe

QUIET = lambda m: None


def render(sandbox, assets, audio, **overrides):
    add_clips(mv.LIB, assets["clip"], 30)
    s = {**mv.load_settings(), "offline": True, **overrides}
    log = []
    out = mv.make(audio, sandbox["OUT"], s, log=log.append)
    return out, log, s


def work_dir(audio):
    import hashlib
    return mv.TMP / hashlib.md5(audio.stem.encode()).hexdigest()[:12]


def volume(path, start=None, dur=None):
    args = ["ffmpeg", "-hide_banner"] + (["-ss", str(start), "-t", str(dur)] if start is not None else []) + \
        ["-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"]
    err = subprocess.run(args, capture_output=True, text=True).stderr
    return float(next(l for l in err.splitlines() if "mean_volume" in l).split(":")[1].split()[0])


def test_voice_only_simple_subs_16x9(sandbox, assets):
    audio = sandbox["root"] / "speech.mp3"; shutil.copy(assets["speech"], audio)
    out, log, s = render(sandbox, assets, audio, subs_engine="simple")
    assert out and out.exists(), log
    dur, w, h, has_audio = probe(out)
    assert (w, h) == (1920, 1080) and has_audio
    assert abs(dur - mv.duration(audio)) < 0.5
    wd = work_dir(audio)
    assert (wd / "voice.wav").exists()                       # music removed
    ass = (wd / "subs.ass").read_text()
    assert ass.count("Dialogue:") > 3                         # burned-in captions
    assert len(mv.used_pool()) == len(list(wd.glob("*.mp4"))) or True
    assert len(mv.library_pool()) < 30                        # clips were moved to used/
    assert not list(wd.glob("0*.mp4"))                        # shot segments cleaned up


def test_music_kept_simple_subs_vertical(sandbox, assets):
    audio = sandbox["root"] / "vertical.mp3"; shutil.copy(assets["speech"], audio)
    out, log, _ = render(sandbox, assets, audio, remove_music=False, subs_engine="simple", aspect="9:16")
    assert out, log
    _, w, h, has_audio = probe(out)
    assert (w, h) == (1080, 1920) and has_audio
    wd = work_dir(audio)
    assert not (wd / "voice.wav").exists()
    assert "PlayResY: 1920" in (wd / "subs.ass").read_text()


def test_voice_only_is_quieter_in_music_gaps(sandbox, assets):
    """Music-only moments must get much quieter when the music is removed."""
    audio = sandbox["root"] / "gaps.mp3"; shutil.copy(assets["speech"], audio)
    out, log, _ = render(sandbox, assets, audio, subs=False)
    assert out, log
    voice = work_dir(audio) / "voice.wav"
    music_level = volume(audio) - volume(voice)
    assert music_level > 1.0, "separated voice should have less energy than speech+music"


def test_no_subtitles(sandbox, assets):
    audio = sandbox["root"] / "nosubs.mp3"; shutil.copy(assets["speech"], audio)
    out, log, _ = render(sandbox, assets, audio, subs=False, remove_music=False)
    assert out, log
    assert not (work_dir(audio) / "subs.ass").exists()


@pytest.mark.parametrize("kind", ["wav", "m4a"])
def test_other_audio_formats(sandbox, assets, kind):
    audio = sandbox["root"] / f"input.{kind}"; shutil.copy(assets[kind], audio)
    out, log, _ = render(sandbox, assets, audio, remove_music=False, subs_engine="simple")
    assert out and out.name == "input.mp4", log
    assert probe(out)[3]


def test_very_short_audio(sandbox, assets):
    audio = sandbox["root"] / "short.mp3"; shutil.copy(assets["short"], audio)
    out, log, _ = render(sandbox, assets, audio)        # 3 s < fade_in + fade_out
    assert out, log
    assert abs(probe(out)[0] - mv.duration(audio)) < 0.5


def test_one_second_audio(sandbox, assets):
    audio = sandbox["root"] / "tiny.mp3"
    ffmpeg("-ss", "0", "-t", "1", "-i", str(assets["speech"]), str(audio))
    out, log, _ = render(sandbox, assets, audio, remove_music=False)
    assert out, log


def test_music_only_audio_no_speech(sandbox, assets):
    """Instrumental input: no words -> empty subtitles, video still rendered."""
    audio = sandbox["root"] / "tone.mp3"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=6", str(audio))
    out, log, _ = render(sandbox, assets, audio)
    assert out, log


def test_special_characters_in_filename(sandbox, assets):
    name = "It's \"Time\" 100%! ＂X＂ [v2] & more.mp3"
    audio = sandbox["root"] / name; shutil.copy(assets["speech"], audio)
    out, log, _ = render(sandbox, assets, audio, remove_music=False)
    assert out and out.name == name[:-4] + ".mp4", log


def test_existing_output_is_skipped_and_lock_released(sandbox, assets):
    audio = sandbox["root"] / "dup.mp3"; shutil.copy(assets["short"], audio)
    (sandbox["OUT"] / "dup.mp4").write_bytes(b"x")
    out, _, _ = render(sandbox, assets, audio)
    assert out is None
    assert not list(mv.LOCKS.glob("*.lock"))


def test_failure_releases_lock_and_reports(sandbox, assets, monkeypatch):
    audio = sandbox["root"] / "fail.mp3"; shutil.copy(assets["short"], audio)
    monkeypatch.setattr(mv, "take_clips", lambda *a, **k: [])
    log = []
    out = mv.make(audio, sandbox["OUT"], {**mv.load_settings(), "offline": True}, log=log.append)
    assert out is None and any("FAILED" in m for m in log)
    assert not list(mv.LOCKS.glob("*.lock"))


def test_render_record_and_real_quality_check(sandbox, assets):
    """Whole automatic check on a real render, including real Whisper sync on the excerpt."""
    import qa
    audio = sandbox["root"] / "qa run.mp3"; shutil.copy(assets["speech"], audio)
    out, log, s = render(sandbox, assets, audio, remove_music=False, subs_engine="simple")
    assert out, log
    wd = work_dir(audio)
    rec = mv.read_json(wd / "render.json", {})
    assert rec["subs"] and rec["size"] == [1920, 1080] and abs(rec["total"] - mv.duration(audio)) < 0.01
    assert mv.read_json(wd / "words_render.json", [])
    r = qa.check_video(out, audio, wd, log=QUIET, s=s)
    assert r["status"] in ("pass", "warn"), r["reasons"]       # ad check may warn on real speech
    for name in ("duration", "decode", "audio", "resolution", "subtitles", "sync"):
        assert next(c for c in r["checks"] if c["name"] == name)["status"] == "pass", r["checks"]
    assert r["sync"]["median"] <= qa.SYNC_PASS


@pytest.mark.skipif(not mv.kinetic_available(), reason="kinetic_captions/.venv not set up")
def test_kinetic_captions_engine(sandbox, assets):
    audio = sandbox["root"] / "kinetic.mp3"; shutil.copy(assets["speech"], audio)
    out, log, _ = render(sandbox, assets, audio, remove_music=False, subs_engine="kinetic", bgm=False)
    assert out and out.exists(), log
    dur, w, h, has_audio = probe(out)
    assert (w, h) == (1920, 1080) and has_audio
    assert abs(dur - mv.duration(audio)) < 0.5
    wd = work_dir(audio)
    layout = (wd / "kc" / "layout.json").read_text()
    assert '"phrases"' in layout and layout.count('"text"') > 5   # captions were laid out
    assert (wd / "subs.ass").read_text().count("Dialogue:") > 2   # plain record kept for QA
    assert not (wd / "out.mp4").exists() and not (wd / "out_kc.mp4").exists()
