#!/usr/bin/env python3
"""FORGED Shorts: story.py (preset forged_shorts) + post-fix.

Post-fix only: trim leading silence, apply a mild color contrast grade
(no desaturation), and encode. Captions come solely from narrator_studio's
own kinetic_captions pass inside story.py. This tool does not add a hook
overlay or any other caption layer.

Usage:
  venv/bin/python tools/forged_short.py scripts/forged_shorts/x.txt [...]
  venv/bin/python tools/forged_short.py --postfix-only scripts/forged_shorts/x.txt
  --color is accepted for compatibility and is the default (no B&W grade).
"""
import re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "output" / "videos" / "forged"

def slug(t): return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", t)).strip("_")

def lead_silence(f):
    r = subprocess.run(["ffmpeg", "-i", str(f), "-af", "silencedetect=n=-40dB:d=0.1", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    m = re.search(r"silence_start: (-?[\d.]+)\n.*?silence_end: ([\d.]+)", r, re.S)
    return float(m.group(2)) if m and float(m.group(1)) < 0.05 else 0.0

def main(script, postfix_only=False):
    lines = [l.strip() for l in Path(script).read_text().splitlines() if l.strip()]
    title = lines[0]
    src = HERE / "output" / "videos" / f"{slug(title)}.mp4"
    if not postfix_only:
        subprocess.run([str(HERE / "venv/bin/python"), str(HERE / "story.py"), script, "--preset", "forged_shorts"],
                       cwd=HERE, check=True)
    if not src.exists():
        raise SystemExit(f"raw render missing: {src}")
    cut = max(0.0, lead_silence(src) - 0.03)
    OUT.mkdir(parents=True, exist_ok=True)
    # Mild color grade only — no hue=s=0, no drawtext overlay.
    vf = "eq=contrast=1.08:brightness=-0.02"
    dst = OUT / f"{slug(title)}.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{cut:.2f}", "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p"]
    if cut <= 0.001:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)
    print(f"-> {dst} (cut {cut:.2f}s, color, no hook overlay)")

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = set(a for a in sys.argv[1:] if a.startswith("-"))
    unknown = flags - {"--color", "--postfix-only"}
    if unknown:
        raise SystemExit(f"unknown flags: {unknown}")
    if not args:
        raise SystemExit("usage: forged_short.py [--postfix-only] [--color] script.txt [...]")
    postfix_only = "--postfix-only" in flags
    for a in args:
        main(a, postfix_only=postfix_only)
