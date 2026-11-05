"""Voice script scenes with CosyVoice 2 on the Mac GPU (MPS). Runs in tools/cosyvoice_venv.

    tools/cosyvoice_venv/bin/python tools/cosy_batch.py jobs.json

jobs.json: {"repo": "/abs/CosyVoice", "model": "/abs/CosyVoice2-0.5B",
            "ref_audio": "/abs/voice.wav", "ref_text": "what the reference says" (optional),
            "speed": 1.0, "items": [{"text": "...", "out": "/abs/scene.wav"}, ...]}

Every scene is cloned from the SAME reference clip, so the whole video keeps one voice. Output files
(44.1 kHz mono, loudness -18 LUFS, speed applied) that already exist are skipped.
Progress: "TTS <done> <total>".
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memguard import gpu_lock  # noqa: E402
import cosyvoice2_tts as C  # noqa: E402


def main(path):
    job = json.loads(Path(path).read_text())
    items = job["items"]
    todo = [it for it in items if not Path(it["out"]).exists()]
    print(f"TTS {len(items) - len(todo)} {len(items)}", flush=True)
    if not todo:
        return 0
    tts = C.CosyVoiceTTS(job["repo"], job["model"])
    speed = max(0.5, min(1.5, float(job.get("speed", 1.0))))
    done = len(items) - len(todo)
    for it in todo:
        out = Path(it["out"]); out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_name(out.stem + ".raw.wav")
        tts.synthesize_to_file(it["text"], raw, job["ref_audio"], job.get("ref_text"), 1.0)
        af = "loudnorm=I=-18" + (f",atempo={speed:.3f}" if abs(speed - 1) > 0.01 else "")
        part = out.with_name(out.stem + ".part.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", af, "-ar", "44100", "-ac", "1",
                        str(part)], check=True)
        part.replace(out); raw.unlink(missing_ok=True)
        done += 1
        print(f"TTS {done} {len(items)}", flush=True)
    return 0


if __name__ == "__main__":
    with gpu_lock():                       # never alongside FLUX or Dia
        raise SystemExit(main(sys.argv[1]))
