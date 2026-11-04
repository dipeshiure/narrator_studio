"""Remove long silent stretches from already-finished videos (no re-render of the footage).

  venv/bin/python tighten_existing.py <videos dir> <voice audio dir> [--settings FILE] [--part k/n]

For each video: find pauses longer than max_pause from the word timestamps, cut the
same ranges out of the picture (subtitles are burned in, so they stay in sync) and
out of the voice-only audio (tiny fades at the joins), then mix the same BGM track
again so the music runs continuously. The previous voice file is kept in
<voice dir>/_before_tighten/. Updates words/render.json so QA can check sync.
"""
import argparse, os, shutil
import sys as _sys
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))  # app modules live in the tool's root
from pathlib import Path

import make_video as mv
import prep


def cut_video(video, ranges, dest, bitrate):
    parts = [f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{k}]" for k, (a, b) in enumerate(ranges)]
    graph = ";".join(parts) + ";" + "".join(f"[v{k}]" for k in range(len(ranges))) + \
        f"concat=n={len(ranges)}:v=1:a=0[out]"
    script = dest.with_suffix(".filter.txt"); script.write_text(graph)
    mv.run(["ffmpeg", "-y", "-i", str(video), "-/filter_complex", str(script), "-map", "[out]",
            "-c:v", "h264_videotoolbox", "-b:v", bitrate, "-an", str(dest)])
    script.unlink(missing_ok=True)


def tighten(video, voice, s, log=print):
    work = mv.work_dir(voice)
    words = mv.read_json(work / "words_render.json", None) or mv.read_json(work / "words_original.json", [])
    total = mv.duration(voice)
    mp = float(s.get("max_pause", 1.2))
    ranges = prep.tighten_ranges([(0.0, total)], words, total, mp, quiet=prep.silences(voice, mp))
    if sum(b - a for a, b in ranges) < 1.0:            # nothing but silence: don't touch it
        log("  無音のみのため、そのままにします"); return 0.0
    removed = total - sum(b - a for a, b in ranges)
    if ranges == [(0.0, total)] or removed < 0.5:
        log("  詰める無音なし"); return 0.0
    rj = mv.read_json(work / "render.json", {}) or {}
    music = None
    if rj.get("music"):
        import music as mus
        t = next((x for x in mus.load_tracks() if x["file"] == rj["music"]["file"]), None)
        if t:
            music = {**t, "path": str(mus.DIR / t["file"]), "credit": mus.credit_block([t["title"]])}
    tmp_v = video.with_name(video.stem + ".tight.mp4")
    cut_video(video, ranges, tmp_v, s.get("bitrate", "6M"))
    backup = voice.parent / "_before_tighten"; backup.mkdir(exist_ok=True)
    if not (backup / voice.name).exists():
        shutil.copy2(voice, backup / voice.name)
    tmp_a = voice.with_name(voice.stem + ".tight.wav")
    prep.trim_audio(voice, ranges, tmp_a)
    mv.run(["ffmpeg", "-y", "-i", str(tmp_a), "-c:a", "libmp3lame", "-b:a", "192k", str(voice.with_suffix(".tmp.mp3"))])
    os.replace(voice.with_suffix(".tmp.mp3"), voice); tmp_a.unlink(missing_ok=True)
    # the cut picture has no audio yet: put voice (+ the same BGM) on it
    s2 = {**s, "bgm": bool(music)}
    if music:
        mv.remix_bgm(tmp_v, voice, s2, log=lambda m: None, music=music)
    else:
        mv.run(["ffmpeg", "-y", "-i", str(tmp_v), "-i", str(voice), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-shortest", str(tmp_v.with_suffix(".a.mp4"))])
        os.replace(tmp_v.with_suffix(".a.mp4"), tmp_v)
    if abs(mv.duration(tmp_v) - mv.duration(voice)) > 0.5:
        raise RuntimeError("長さが合いません")
    os.replace(tmp_v, video)
    new_words = prep.remap_words(words, ranges) if words else \
        mv.whisper_words(voice, s.get("whisper_model", "base"))      # no transcript cached: make one (for QA)
    mv.write_json(work / "words_render.json", new_words)
    mv.write_json(work / "words_original.json", new_words)
    rj = mv.read_json(work / "render.json", {}) or {}
    rj.update(video=str(video), audio=str(voice), total=mv.duration(voice))
    mv.write_json(work / "render.json", rj)
    if music:
        mv.apply_music_credit(video, voice, log=lambda m: None)
    log(f"  無音を {removed:.1f} 秒詰めました({total:.0f}s → {total - removed:.0f}s)")
    return removed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("videos"); ap.add_argument("audio")
    ap.add_argument("--settings", default=str(mv.SETTINGS))
    ap.add_argument("--part", default="1/1")
    a = ap.parse_args()
    s = mv.load_settings(a.settings)
    k, n = map(int, a.part.split("/"))
    vdir, adir = Path(a.videos).expanduser(), Path(a.audio).expanduser()
    videos = sorted(vdir.glob("*.mp4"))[k - 1::n]
    total = 0.0
    for i, v in enumerate(videos, 1):
        voice = adir / (v.stem + ".mp3")
        if not voice.exists():
            continue
        print(f"[{i}/{len(videos)}] {v.name}", flush=True)
        try:
            total += tighten(v, voice, s, log=lambda m: print(m, flush=True))
        except Exception as e:
            print(f"  FAILED: {e}", flush=True)
    print(f"finished part {k}/{n}: {total / 60:.1f} 分の無音を詰めました", flush=True)
