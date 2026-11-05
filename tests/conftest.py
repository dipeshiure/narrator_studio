"""Shared fixtures. Every test runs in a throw-away sandbox: library, settings,
locks and outputs point to tmp_path, so the real library / settings.json and
any running auto-generation workers are never touched."""
import shutil, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import make_video as mv  # noqa: E402

SAMPLE_SRC = Path.home() / "Desktop" / "soundcloud_motivational" / \
    "63_Opportunity Motivational Speech - LIFE Motivation!.mp3"
ASSETS = Path(__file__).parent / ".assets"   # cached generated media (git-ignorable)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def assets():
    """Real speech+music samples cut from one of the user's mp3s, plus a dummy clip."""
    ASSETS.mkdir(exist_ok=True)
    a = {
        "speech": ASSETS / "speech_12s.mp3",
        "short": ASSETS / "short_3s.mp3",
        "wav": ASSETS / "speech_8s.wav",
        "m4a": ASSETS / "speech_8s.m4a",
        "clip": ASSETS / "clip.mp4",
    }
    if not a["speech"].exists():
        ffmpeg("-ss", "30", "-t", "12", "-i", str(SAMPLE_SRC), str(a["speech"]))
    if not a["short"].exists():
        ffmpeg("-ss", "30", "-t", "3", "-i", str(SAMPLE_SRC), str(a["short"]))
    if not a["wav"].exists():
        ffmpeg("-ss", "30", "-t", "8", "-i", str(SAMPLE_SRC), str(a["wav"]))
    if not a["m4a"].exists():
        ffmpeg("-ss", "30", "-t", "8", "-i", str(SAMPLE_SRC), "-c:a", "aac", str(a["m4a"]))
    if not a["clip"].exists():   # 6 s 1280x720 test pattern, > 200 KB so it counts as a clip
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
               "-c:v", "libx264", "-b:v", "4M", str(a["clip"]))
    return a


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every path make_video uses into tmp_path."""
    paths = {
        "LIB": tmp_path / "library", "USED": tmp_path / "library_used", "TMP": tmp_path / "tmp",
        "LOCKS": tmp_path / "tmp" / "locks", "SETTINGS": tmp_path / "settings.json",
        "PAGES": tmp_path / "pexels_pages.json",
        "REUSE": tmp_path / "reuse_counts.json",
        "OUTPUT": tmp_path / "videos_voice",          # never the real finished-video folder
        "POSTING": tmp_path / "posting",              # never the real motiversity_auto/投稿用動画
        "FINISH": tmp_path / "finish.json",           # never the user's saved finishing look
    }
    for name, p in paths.items():
        monkeypatch.setattr(mv, name, p)
    # BGM library sandboxed too, and never downloaded during tests
    import music
    monkeypatch.setattr(music, "DIR", tmp_path / "library_music")
    monkeypatch.setattr(music, "fetch", lambda n=12, log=print: 0)
    for d in ("LIB", "USED", "TMP"):
        paths[d].mkdir(parents=True)
    out = tmp_path / "videos"; out.mkdir()
    paths["OUT"] = out
    paths["root"] = tmp_path
    return paths


def add_clips(folder, clip, n, prefix="1000"):
    """Put n distinct fake Pexels clips into folder/<query>/<id>.mp4."""
    q = folder / "test_query"
    q.mkdir(parents=True, exist_ok=True)
    made = []
    for i in range(n):
        dest = q / f"{prefix}{i:03d}.mp4"
        shutil.copy(clip, dest)
        made.append(dest)
    return made


def probe(path):
    """(duration, width, height, has_audio) of a media file."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    import json
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in d["streams"])
    return float(d["format"]["duration"]), v["width"], v["height"], has_audio
