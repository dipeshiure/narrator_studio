"""Narration quality gates: short-prompt handling and reject-bad-cache checks."""
import sys
from pathlib import Path

import numpy as np
import pytest

import make_video as mv
import story

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import cosyvoice2_tts as C  # noqa: E402


def test_fit_prompt_text_drops_when_line_is_much_shorter():
    prompt = ("Every man reaches a point where nobody claps for him anymore. "
              "Nobody checks on him. And that is exactly where his real life begins.")
    assert C.fit_prompt_text(prompt, prompt) == prompt
    assert C.fit_prompt_text(prompt, "Walk alone.") is None
    assert C.fit_prompt_text("", "Walk alone.") is None
    assert C.fit_prompt_text(None, "Walk alone.") is None


def test_audio_ok_rejects_empty_silent_and_impossible_rates():
    sr = 24000
    ok, _ = C.audio_ok(np.zeros(0, dtype=np.float32), sr, "hello there friend")
    assert not ok
    ok, _ = C.audio_ok(np.zeros(sr, dtype=np.float32) + 0.001, sr, "hello there friend")
    assert not ok                                          # peak too low
    words = " ".join(["word"] * 20)
    too_short = np.linspace(-0.2, 0.2, int(sr * 2.5), dtype=np.float32)  # ~8 wps, mumbled
    assert C.audio_ok(too_short, sr, words)[0] is False
    good = np.linspace(-0.2, 0.2, int(sr * 8.0), dtype=np.float32)
    assert C.audio_ok(good, sr, words)[0] is True
    # after 1.5x tempo the same clip is shorter but still accepted
    sped = np.linspace(-0.2, 0.2, int(sr * 8.0 / 1.5), dtype=np.float32)
    assert C.audio_ok(sped, sr, words, speed=1.5)[0] is True


def test_narration_usable_deletes_bad_cache(tmp_path):
    text = " ".join(["word"] * 20)
    good = tmp_path / "good.wav"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=220:d=8", "-ar", "44100", "-ac", "1", str(good)])
    assert story.narration_usable(good, text) is True
    bad = tmp_path / "bad.wav"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=220:d=1", "-ar", "44100", "-ac", "1", str(bad)])
    (tmp_path / "bad.words.json").write_text("[]")
    assert story.narration_usable(bad, text) is False
    assert not bad.exists() and not (tmp_path / "bad.words.json").exists()


def test_wav_file_ok_reads_disk(tmp_path):
    text = " ".join(["word"] * 12)
    path = tmp_path / "clip.wav"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=5", "-ar", "44100", "-ac", "1", str(path)])
    assert C.wav_file_ok(path, text)[0] is True
    assert C.wav_file_ok(tmp_path / "missing.wav", text)[0] is False


def test_trim_lead_silence_cuts_opening_hush(tmp_path):
    path = tmp_path / "pad.wav"
    # 0.7 s silence then 2 s tone — CosyVoice-like lead-in
    mv.run(["ffmpeg", "-y",
            "-f", "lavfi", "-t", "0.7", "-i", "anullsrc=r=44100:cl=mono",
            "-f", "lavfi", "-t", "2", "-i", "sine=f=440:r=44100",
            "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(path)])
    before = mv.duration(path)
    (tmp_path / "pad.words.json").write_text("[]")
    removed = story.trim_lead_silence(path)
    assert removed >= 0.5
    assert abs(mv.duration(path) - (before - removed)) < 0.08
    assert not (tmp_path / "pad.words.json").exists()
    assert story.trim_lead_silence(path) < 0.04   # already tight


def test_story_fade_in_is_short_so_first_frame_shows_picture():
    s = story.load_story_settings("bhatt")
    assert s["fade_in"] <= 0.35
    assert mv.load_settings()["fade_in"] >= 1.0     # inbox can stay longer; stories override
