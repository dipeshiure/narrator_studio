"""Add free BGM to already-finished videos (audio swap only, picture untouched).

  venv/bin/python remix_bgm.py <videos dir> <voice audio dir> [--settings FILE] [--redo]

For every <videos dir>/<stem>.mp4 whose voice-only audio <voice audio dir>/<stem>.mp3
exists: mix voice + a free incompetech track (ducked), replace the video's audio,
add the CC BY credit to the description, refresh the posting folders.
Videos that already have BGM (render.json "music") are skipped unless --redo.
"""
import argparse
import sys as _sys
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))  # app modules live in the tool's root
from pathlib import Path

import make_video as mv

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("videos"); ap.add_argument("audio")
    ap.add_argument("--settings", default=str(mv.SETTINGS))
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()
    s = {**mv.load_settings(a.settings), "bgm": True}
    vdir, adir = Path(a.videos).expanduser(), Path(a.audio).expanduser()
    videos = sorted(vdir.glob("*.mp4"))
    done = skipped = failed = 0
    for i, v in enumerate(videos, 1):
        voice = adir / (v.stem + ".mp3")
        if not voice.exists():
            print(f"[{i}/{len(videos)}] skip (no voice audio): {v.name}", flush=True); skipped += 1; continue
        if not a.redo and (mv.read_json(mv.work_dir(voice) / "render.json", {}) or {}).get("music"):
            print(f"[{i}/{len(videos)}] skip (already has BGM): {v.name}", flush=True); skipped += 1; continue
        print(f"[{i}/{len(videos)}] {v.name}", flush=True)
        try:
            mv.remix_bgm(v, voice, s, log=lambda m: print(m, flush=True))
            done += 1
        except Exception as e:
            print(f"  FAILED: {e}", flush=True); failed += 1
    mv.refresh_posting(vdir, log=print)
    print(f"finished: {done} remixed, {skipped} skipped, {failed} failed", flush=True)
