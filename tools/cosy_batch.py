"""Voice script scenes with CosyVoice 2 on the Mac GPU (MPS). Runs in tools/cosyvoice_venv.

    tools/cosyvoice_venv/bin/python tools/cosy_batch.py jobs.json

jobs.json: {"repo": "/abs/CosyVoice", "model": "/abs/CosyVoice2-0.5B",
            "ref_audio": "/abs/voice.wav", "ref_text": "what the reference says" (optional),
            "speed": 1.0, "items": [{"text": "...", "out": "/abs/scene.wav"}, ...]}

Every scene is cloned from the SAME reference clip, so the whole video keeps one voice. Output files
(44.1 kHz mono, loudness -18 LUFS, speed applied) that already exist and pass a quick quality check
are skipped; empty / silent / impossibly short clips are deleted and remade.
Progress: "TTS <done> <total>".
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memguard import gpu_lock  # noqa: E402
import cosyvoice2_tts as C  # noqa: E402


def _ready(path, text, speed):
    """True when path is a usable narration wav for this text (else delete the bad cache)."""
    ok, _ = C.wav_file_ok(path, text, speed)
    if ok:
        return True
    Path(path).unlink(missing_ok=True)
    Path(path).with_suffix(".words.json").unlink(missing_ok=True)
    Path(path).with_suffix(".heard.json").unlink(missing_ok=True)
    return False


def main(path):
    job = json.loads(Path(path).read_text())
    items = job["items"]
    speed = max(0.5, min(1.5, float(job.get("speed", 1.0))))
    todo = [it for it in items if not _ready(it["out"], it["text"], speed)]
    print(f"TTS {len(items) - len(todo)} {len(items)}", flush=True)
    if not todo:
        return 0
    tts = C.CosyVoiceTTS(job["repo"], job["model"])
    done = len(items) - len(todo)
    for it in todo:
        out = Path(it["out"]); out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_name(out.stem + ".raw.wav")
        tts.synthesize_to_file(it["text"], raw, job["ref_audio"], job.get("ref_text"), 1.0, attempts=3)
        ok, why = C.wav_file_ok(raw, it["text"], 1.0)   # raw is still at tempo 1.0
        if not ok:
            raw.unlink(missing_ok=True)
            raise RuntimeError(f"bad narration for {out.name}: {why}")
        # louder than -18 so the voice sits above BGM after the final -14 mix;
        # optional tempo; then drop CosyVoice's leading hush (keep 50 ms)
        af = "loudnorm=I=-16:TP=-1.5:LRA=11" + (f",atempo={speed:.3f}" if abs(speed - 1) > 0.01 else "")
        af += ",silenceremove=start_periods=1:start_silence=0.05:start_threshold=-35dB:detection=rms"
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
