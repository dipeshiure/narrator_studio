"""Voice script paragraphs with Dia-1.6B (Nari Labs, Apache-2.0) on Apple Silicon.
Runs in tools/dia_venv (mlx-audio, model mlx-community/Dia-1.6B-4bit).

    tools/dia_venv/bin/python tools/dia_batch.py jobs.json

jobs.json: {"seed": 33, "ref_dir": "/abs/dir",
            "items": [{"text": "...", "out": "/abs/scene.wav"}, ...]}
Optional "ref_audio" + "ref_text": a recorded voice (and what is said in it) to use as the
speaker instead of the seed's invented one ("my voices" on the 台本 page).

Dia has no fixed voices: every run invents a speaker. To keep ONE voice for a whole
video, a reference clip is made once per seed (cached in ref_dir) and passed as the
audio prompt for every chunk; each chunk's pitch is then checked against the reference
and the chunk is redone with another seed when it drifts (e.g. turns into a female
voice). Paragraphs are voiced 1-2 sentences at a time (Dia rushes long inputs, and is
unnatural on very short ones); chunks that come out too fast are slowed with atempo.
Every chunk is also heard back with Whisper on the GPU: a chunk whose words can't be
recognised (Dia sometimes mumbles or makes noise) is redone. The finished paragraph's
word timings are written next to it as <out>.heard.json for the subtitles.
Files that already exist are skipped. Progress: "TTS <done> <total>".
"""
import glob
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import mlx.core as mx
import numpy as np
from mlx_audio.tts.generate import generate_audio
sys.path.insert(0, str(Path(__file__).resolve().parent))
from memguard import gpu_lock, unload_llms, wait_for_memory  # noqa: E402
from mlx_audio.stt.utils import load_model as load_stt
from mlx_audio.tts.utils import load_model

MODEL = "mlx-community/Dia-1.6B-4bit"
REF_TEXT = ("Every man reaches a point where nobody claps for him anymore. "
            "Nobody checks on him. And that is exactly where his real life begins.")
MAX_WORDS = 24          # per chunk (~6-9 s of speech)
WPS = 2.5               # target words per second (calm narration)
MAX_DRIFT = 0.18        # allowed |log2(chunk pitch / reference pitch)| (~13 %): same speaker stays well inside, a male->female flip is ~0.8
TRIES = 5
TEMPERATURE = 1.0       # Dia's default 1.3 wanders between speakers; lower = steadier voice, fewer redos
TOKENS_PER_SEC = 86     # Dia's audio frame rate
MAX_SECS_PER_WORD = 0.5 # cap the generation length (Dia sometimes runs on with noise)
PAUSE = 0.25            # silence between chunks of one paragraph (s)
STT = "mlx-community/whisper-base-asr-fp16"
MIN_HEARD = 0.6         # share of the script's words Whisper must recognise in a chunk


def chunks(text):
    sents = [s.strip() for s in re.findall(r"[^.!?]+[.!?]+[\"')\]]*|[^.!?]+$", text) if s.strip()]
    out, cur = [], ""
    for s in sents or [text]:
        if cur and len((cur + " " + s).split()) > MAX_WORDS:
            out.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return out


def _norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def heard(stt, path, words=False):
    r = stt.generate(str(path), language="en", word_timestamps=words)
    if words:
        return [{"w": w["word"].strip(), "s": round(float(w["start"]), 3), "e": round(float(w["end"]), 3)}
                for seg in (r.segments or []) for w in seg.get("words", []) if w["word"].strip()]
    return r.text or ""


def recall(text, got):
    """Share of the script's words that were heard (order-free; Whisper's spelling may differ)."""
    want = [_norm(w) for w in text.split() if _norm(w)]
    have = {_norm(w) for w in got.split()}
    return sum(w in have for w in want) / max(1, len(want))


def pitch(path):
    y, sr = librosa.load(str(path), sr=16000)
    f, _, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr)
    return float(np.nanmedian(f)) if np.isfinite(np.nanmedian(f)) else 0.0, len(y) / sr


def say(model, text, seed, out_dir, name, ref=None, ref_text=REF_TEXT):
    mx.random.seed(int(seed))
    words = len(text.split()) + (len(ref_text.split()) if ref else 0)   # the prompt is part of the output
    generate_audio(text="[S1] " + text, model=model, ref_audio=str(ref) if ref else None,
                   ref_text=("[S1] " + ref_text) if ref else None, temperature=TEMPERATURE,
                   max_tokens=int((words * MAX_SECS_PER_WORD + 3) * TOKENS_PER_SEC),
                   output_path=str(out_dir), file_prefix=name, verbose=False)
    files = sorted(glob.glob(f"{out_dir}/{name}_*.wav"))
    if not files:
        raise RuntimeError("Dia produced no audio")
    dest = Path(out_dir) / f"{name}.wav"
    shutil.move(files[-1], dest)
    return dest


def main(path):
    job = json.loads(Path(path).read_text())
    items, seed = job["items"], int(job.get("seed", 33))
    todo = [it for it in items if not Path(it["out"]).exists()]
    print(f"TTS {len(items) - len(todo)} {len(items)}", flush=True)
    ref_dir = Path(job["ref_dir"]); ref_dir.mkdir(parents=True, exist_ok=True)
    ref_text = job.get("ref_text") or REF_TEXT
    speed = max(0.5, min(2.0, float(job.get("speed", 1.0))))   # 1.0 = Dia's own pace
    ref = Path(job["ref_audio"]) if job.get("ref_audio") else ref_dir / f"dia_seed{seed}.wav"
    if not todo and ref.exists():
        return 0
    unload_llms()                            # the local LLM can hold 4+ GB
    mx.set_cache_limit(512 * 1024 * 1024)   # keep freed GPU buffers small (no swap)
    model = load_model(MODEL)
    if not ref.exists():               # also the voice sample on the 台本 page (items = [])
        say(model, REF_TEXT, seed, ref_dir, f"dia_seed{seed}")
    ref_hz, _ = pitch(ref)
    print(f"REF {ref_hz:.0f}", flush=True)
    stt = load_stt(STT)
    done = len(items) - len(todo)
    with tempfile.TemporaryDirectory() as tmp:
        for it in todo:
            parts = []
            for k, text in enumerate(chunks(it["text"])):
                best = None
                for t in range(TRIES):
                    wait_for_memory(mx.clear_cache)
                    wav = say(model, text, seed + 1000 * t + k, tmp, f"c{k}_{t}", ref, ref_text)
                    hz, dur = pitch(wav)
                    drift = abs(math.log2(hz / ref_hz)) if hz and ref_hz else 9
                    clear = recall(text, heard(stt, wav))
                    ok = drift <= MAX_DRIFT and clear >= MIN_HEARD
                    score = (clear >= MIN_HEARD, -drift, clear)       # intelligible first, then the voice
                    if best is None or score > best[3]:
                        best = (wav, drift, dur, score, clear)
                    if ok:
                        break
                wav, drift, dur, _, clear = best
                if drift > MAX_DRIFT:
                    print(f"WARN pitch drift {drift:.2f} on: {text[:60]}", flush=True)
                if clear < MIN_HEARD:
                    print(f"WARN unclear speech ({clear:.0%} heard) on: {text[:60]}", flush=True)
                # too fast -> slow down (never speed up, never below 0.8x)
                want = len(text.split()) / WPS
                tempo = max(0.8, min(1.0, dur / want)) if want else 1.0
                fixed = Path(tmp) / f"fix{k}.wav"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-af",
                                f"atempo={tempo:.3f}", "-ar", "44100", "-ac", "1", str(fixed)], check=True)
                parts.append(fixed)
            out = Path(it["out"]); out.parent.mkdir(parents=True, exist_ok=True)
            lst = Path(tmp) / "list.txt"
            gap = Path(tmp) / "gap.wav"
            if not gap.exists():
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                                "-t", str(PAUSE), str(gap)], check=True)
            seq = [p for x in parts for p in (x, gap)][:-1]
            lst.write_text("".join(f"file '{p}'\n" for p in seq))
            part = out.with_name(out.stem + ".part.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                            "-af", "loudnorm=I=-18" + (f",atempo={speed:.3f}" if abs(speed - 1) > 0.01 else ""),
                            "-ar", "44100", "-ac", "1", str(part)], check=True)
            part.replace(out)
            out.with_suffix(".heard.json").write_text(json.dumps(heard(stt, out, words=True)))
            for p in parts:
                p.unlink(missing_ok=True)
            mx.clear_cache()
            done += 1
            print(f"TTS {done} {len(items)}", flush=True)
    return 0


if __name__ == "__main__":
    with gpu_lock():                     # never alongside FLUX (or another Dia run)
        raise SystemExit(main(sys.argv[1]))
