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
from pathlib import Path
import os
# the models are cached after the first download: don't ask the Hugging Face server on every run
# (a dropped connection there made loading fail: "Server disconnected without sending a response")
_hub = Path.home() / ".cache" / "huggingface" / "hub"
if all((_hub / d).exists() for d in ("models--mlx-community--Dia-1.6B-4bit", "models--mlx-community--whisper-base-asr-fp16")):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
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
import dia_fast
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
TEMPERATURE = 0.85      # Dia's default 1.3 wanders between speakers; lower = steadier voice, fewer redos
TOKENS_PER_SEC = 86     # Dia's audio frame rate
MAX_SECS_PER_WORD = 0.5 # cap the generation length (Dia sometimes runs on with noise)
PAUSE = 0.25            # silence between chunks of one paragraph (s)
STT = "mlx-community/whisper-base-asr-fp16"
REPAIR_ROUNDS = 2        # extra passes that re-voice scenes unlike the rest
SCENE_DRIFT = 0.2       # octaves from the video's median scene pitch
SCENE_TIMBRE = 0.2      # same for brightness
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


MAX_SPIKE = 15.0        # dB: loudest frame above the median voiced frame (good takes sit at 8-13; a clap/laugh/noise burst is louder)
MAX_TIMBRE = 0.25       # allowed |log2(brightness / reference brightness)|: a different voice sounds darker/brighter


def voice_feat(path):
    """(pitch Hz, spectral centroid Hz, loudest-frame spike dB): what makes a take 'the same voice'
    and free of sound effects."""
    y, sr = librosa.load(str(path), sr=16000)
    f, _, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr)
    hz = float(np.nanmedian(f)) if np.isfinite(np.nanmedian(f)) else 0.0
    cent = float(np.median(librosa.feature.spectral_centroid(y=y, sr=sr)[0]))
    db = 20 * np.log10(librosa.feature.rms(y=y, frame_length=512, hop_length=160)[0] + 1e-6)
    spike = float(db.max() - np.median(db[db > db.max() - 35]))
    return hz, cent, spike, len(y) / sr


def say(model, text, seed, out_dir, name, ref=None, ref_text=REF_TEXT):
    mx.random.seed(int(seed))
    model._fast_extra = int((len(("[S1] " + text).split()) + len(ref_text.split())) * MAX_SECS_PER_WORD * TOKENS_PER_SEC) + 400
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
    tries = max(1, min(5, int(job.get("tries", TRIES))))   # "fast" = fewer redo attempts
    speed = max(0.5, min(2.0, float(job.get("speed", 1.0))))   # 1.0 = Dia's own pace
    ref = Path(job["ref_audio"]) if job.get("ref_audio") else ref_dir / f"dia_seed{seed}.wav"
    checked_path = Path(path).parent / "dia_checked.json"      # scenes already checked by the repair pass
    checked = set(json.loads(checked_path.read_text())) if checked_path.exists() else set()
    if not todo and ref.exists() and all(it["out"] in checked for it in items):
        return 0
    unload_llms()                            # the local LLM can hold 4+ GB
    mx.set_cache_limit(512 * 1024 * 1024)   # keep freed GPU buffers small (no swap)
    model = load_model(MODEL)
    bits = int(job.get("decode_bits", 8))             # compiled decoding with 8-bit (or 4-bit) matmuls; 0 = original path
    if bits:
        try:
            dia_fast.install(model, bits)
            print(f"FAST {bits}", flush=True)
        except Exception as e:                        # never block narration on the speed-up
            print(f"WARN fast decode unavailable ({e}); using the standard path", flush=True)
            bits = 0
    if not ref.exists():               # also the voice sample on the 台本 page (items = [])
        if bits:                       # the compiled path needs a reference clip: a first voice uses the standard one
            dia_fast.uninstall(model)
        say(model, REF_TEXT, seed, ref_dir, f"dia_seed{seed}")
        if bits:
            dia_fast.install(model, bits)
    ref_hz, ref_cent, _, _ = voice_feat(ref)
    print(f"REF {ref_hz:.0f}", flush=True)
    stt = load_stt(STT)
    done = len(items) - len(todo)
    seen_hz, seen_cent = [], []          # accepted takes so far: the voice the video really has

    def center(vals, fallback):
        return float(np.median(vals)) if vals else fallback

    def voice_item(it, tmp, seed_off=0):
        nonlocal bits
        parts = []
        for k, text in enumerate(chunks(it["text"])):
            best = None
            # target = what this video's accepted takes sound like (Dia's takes sit a little above its own
            # reference); until 3 takes exist, a wide band around the reference
            n_seen = len(seen_hz)
            c_hz, c_cent = center(seen_hz, ref_hz), center(seen_cent, ref_cent)
            tol_hz = MAX_DRIFT if n_seen >= 3 else 0.4
            for t in range(tries):
                wait_for_memory(mx.clear_cache)
                sd = seed + seed_off + 1000 * t + k
                try:
                    wav = say(model, text, sd, tmp, f"c{k}_{t}", ref, ref_text)
                except Exception as e:
                    if not bits:
                        raise
                    print(f"WARN fast decode failed ({e}); switching to the standard path", flush=True)
                    dia_fast.uninstall(model); bits = 0
                    wav = say(model, text, sd, tmp, f"c{k}_{t}", ref, ref_text)
                hz, cent, spike, dur = voice_feat(wav)
                drift = abs(math.log2(hz / c_hz)) if hz and c_hz else 9
                timbre = abs(math.log2(cent / c_cent)) if cent and c_cent else 9
                clear = recall(text, heard(stt, wav))
                clean = spike <= MAX_SPIKE and timbre <= MAX_TIMBRE
                ok = drift <= tol_hz and clear >= MIN_HEARD and clean
                # intelligible first, then no effect noise / same timbre, then the pitch
                score = (clear >= MIN_HEARD, clean, -(drift + timbre + max(0, spike - MAX_SPIKE) / 10), clear)
                if best is None or score > best[3]:
                    best = (wav, drift, dur, score, clear, spike, timbre, hz, cent)
                if ok:
                    break
            wav, drift, dur, _, clear, spike, timbre, hz, cent = best
            if drift <= 0.4 and clear >= MIN_HEARD:          # only takes of this voice shape the target
                seen_hz.append(hz); seen_cent.append(cent)
            if spike > MAX_SPIKE or timbre > MAX_TIMBRE:
                print(f"WARN odd take (spike {spike:.0f} dB, timbre {timbre:.2f}) on: {text[:60]}", flush=True)
            if drift > tol_hz:
                print(f"WARN pitch drift {drift:.2f} on: {text[:60]}", flush=True)
            if clear < MIN_HEARD:
                print(f"WARN unclear speech ({clear:.0%} heard) on: {text[:60]}", flush=True)
            # too fast -> slow down (never speed up, never below 0.8x)
            want = len(text.split()) / WPS
            tempo = max(0.8, min(1.0, dur / want)) if want else 1.0
            fixed = Path(tmp) / f"fix{k}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-af",
                            # same loudness for every chunk, soft limiting of sudden loud bits
                            f"atempo={tempo:.3f},highpass=f=60,acompressor=threshold=-22dB:ratio=3:attack=5:release=80,"
                            "loudnorm=I=-18:TP=-2", "-ar", "44100", "-ac", "1", str(fixed)], check=True)
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
        out.with_suffix(".words.json").unlink(missing_ok=True)      # timings of the old take are stale
        mx.clear_cache()

    with tempfile.TemporaryDirectory() as tmp:
        for it in todo:
            voice_item(it, tmp)
            done += 1
            print(f"TTS {done} {len(items)}", flush=True)
        # repair pass: a scene whose voice differs from the rest of the video (Dia sometimes slips into
        # another speaker) is made again with other seeds; the check covers earlier scenes too
        for rnd in range(1, REPAIR_ROUNDS + 1):
            feats = {}
            for it in items:
                if Path(it["out"]).exists():
                    f = voice_feat(it["out"]); feats[it["out"]] = f
            if len(feats) < 4:
                break
            mh = float(np.median([f[0] for f in feats.values() if f[0]]))
            mc = float(np.median([f[1] for f in feats.values()]))
            bad = [it for it in items if it["out"] in feats and feats[it["out"]][0] and (
                abs(math.log2(feats[it["out"]][0] / mh)) > SCENE_DRIFT or abs(math.log2(feats[it["out"]][1] / mc)) > SCENE_TIMBRE)]
            if not bad:
                break
            print(f"REPAIR {rnd}: {len(bad)} of {len(feats)} scenes sound unlike the rest, voicing them again", flush=True)
            for it in bad:
                Path(it["out"]).unlink(missing_ok=True); Path(it["out"]).with_suffix(".heard.json").unlink(missing_ok=True)
                voice_item(it, tmp, seed_off=5000 * rnd)
        checked_path.write_text(json.dumps([it["out"] for it in items]))
    return 0


if __name__ == "__main__":
    with gpu_lock():                     # never alongside FLUX (or another Dia run)
        raise SystemExit(main(sys.argv[1]))
