"""CosyVoice 2 (Alibaba, 0.5B) text-to-speech for Apple Silicon, forced onto the GPU via MPS.

Why this wrapper exists: CosyVoice's code picks `cuda` or `cpu` in four places (CosyVoiceModel,
CosyVoice2Model, CosyVoiceFrontEnd and the autocast calls) and never `mps`. Instead of editing the
cloned repo, `CosyVoiceTTS` builds the model on the CPU and then moves everything to MPS itself
(llm / flow / hift weights, the model's and the front end's `.device`). Operations MPS does not support
(e.g. parts of the vocoder's STFT) fall back to the CPU through PYTORCH_ENABLE_MPS_FALLBACK=1.

    # file mode: every .txt in a folder -> one .wav per file
    python tools/cosyvoice2_tts.py folder --src scripts_txt --out voice_out \
        --repo ~/CosyVoice --model ~/CosyVoice/pretrained_models/CosyVoice2-0.5B \
        --prompt-wav my_voice.wav --prompt-text "what is said in my_voice.wav"

    # streaming (low latency): audio chunks arrive while the sentence is still being made
    from cosyvoice2_tts import CosyVoiceTTS
    tts = CosyVoiceTTS(repo, model_dir)
    for chunk in tts.stream("Hello there.", prompt_wav="my_voice.wav", prompt_text="..."):
        player.write(chunk)            # float32 numpy, mono, tts.sample_rate

    python tools/cosyvoice2_tts.py check --repo ... --model ...     # prints where everything runs

Zero-shot cloning: pass `prompt_wav` (3-15 s of clean speech, 16 kHz or more) and `prompt_text` (its
transcript). Without `prompt_text` the cross-lingual mode is used (voice only, no transcript needed).
Only clone your own voice or one you have permission for.
"""
from __future__ import annotations

import argparse
import gc
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# must be set before torch is imported: unsupported MPS ops run on the CPU instead of raising, and the
# MPS allocator may not grow past 70 % of unified memory (leaves room for macOS on a 16 GB machine).
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.7")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.5")    # must not exceed the high ratio (default 1.4 is rejected)

log = logging.getLogger("cosyvoice2")


# ------------------------------------------------------------------ device
def pick_device(prefer: str = "auto"):
    """'auto' -> mps if usable, else cpu. 'mps' / 'cpu' force one (mps falls back to cpu with a warning)."""
    import torch
    mps_ok = bool(getattr(torch.backends, "mps", None)) and torch.backends.mps.is_built() \
        and torch.backends.mps.is_available()
    if prefer in ("auto", "mps"):
        if mps_ok:
            return torch.device("mps")
        log.warning("MPS is not available (torch %s, built=%s): using the CPU",
                    torch.__version__, torch.backends.mps.is_built() if hasattr(torch.backends, "mps") else False)
    return torch.device("cpu")


def free_memory() -> None:
    """Hand cached buffers back (matters on 16 GB: the allocator keeps freed GPU memory otherwise)."""
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.synchronize()
            torch.mps.empty_cache()
    except Exception:
        pass


def mem_free_percent() -> int:
    """macOS 'memory_pressure' free percentage (100 when it cannot be read)."""
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=10).stdout
        return int(re.search(r"free percentage:\s*(\d+)", out).group(1))
    except Exception:
        return 100


def wait_for_memory(low: int = 25, resume: int = 35, poll: float = 5.0) -> None:
    """Pause while free memory is under `low` % (swap wears the SSD); continue at `resume` %."""
    if mem_free_percent() >= low:
        return
    log.warning("low free memory (%d%%): pausing until %d%% is free", mem_free_percent(), resume)
    free_memory()
    while mem_free_percent() < resume:
        time.sleep(poll)


def fit_prompt_text(prompt_text: str | None, tts_text: str) -> str | None:
    """CosyVoice mumbles when the spoken line is much shorter than the prompt transcript.

    Keep the prompt text when the line is long enough (>= half the prompt). Otherwise return
    None so the caller uses cross-lingual mode (voice from the clip, no length trap).
    """
    prompt = (prompt_text or "").strip()
    text = (tts_text or "").strip()
    if not prompt:
        return None
    if not text or len(text) >= 0.5 * len(prompt):
        return prompt
    return None


def audio_ok(audio, sample_rate: int, text: str, speed: float = 1.0) -> tuple[bool, str]:
    """Reject empty, silent, or impossibly short/long clips before they get cached.

    `speed` is the playback tempo already applied to `audio` (1.0 for raw CosyVoice output)."""
    import numpy as np
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return False, "empty audio"
    dur = arr.size / max(1, int(sample_rate))
    peak = float(np.max(np.abs(arr)))
    if peak < 0.01:
        return False, f"silent audio (peak={peak:.4f})"
    words = max(1, len(re.findall(r"[A-Za-z0-9']+", text or "")))
    tempo = max(0.5, min(1.5, float(speed or 1.0)))
    # ~2.5 words/s is normal narration; allow a wide band, then retry outliers
    min_dur = max(0.35, words * 0.18) / tempo   # faster than ~330 wpm is almost certainly broken
    max_dur = (words * 1.25 + 2.5) / tempo       # very slow / padded still ok up to here
    if dur < min_dur:
        return False, f"too short ({dur:.2f}s for {words} words, need >={min_dur:.2f}s)"
    if dur > max_dur:
        return False, f"too long ({dur:.2f}s for {words} words, need <={max_dur:.2f}s)"
    return True, "ok"


def wav_file_ok(path, text: str, speed: float = 1.0) -> tuple[bool, str]:
    """Same checks as audio_ok, for an on-disk wav (skip cache of a previous bad run)."""
    import numpy as np
    p = Path(path)
    if not p.exists() or p.stat().st_size < 1000:
        return False, "missing or tiny file"
    try:
        # Prefer soundfile when present (cosy venv); else decode via ffmpeg (main venv).
        try:
            import soundfile as sf
            audio, sr = sf.read(str(p), dtype="float32", always_2d=False)
        except Exception:
            sr = 44100
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(p), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                capture_output=True, check=True)
            audio = np.frombuffer(r.stdout, dtype=np.float32)
    except Exception as e:
        return False, f"unreadable ({e})"
    return audio_ok(audio, sr, text, speed)


# ------------------------------------------------------------------ the wrapper
class CosyVoiceTTS:
    """CosyVoice2 on MPS with streaming and file output.

    repo       : the cloned FunAudioLLM/CosyVoice repository (git clone --recursive: contains Matcha-TTS)
    model_dir  : pretrained_models/CosyVoice2-0.5B (downloaded with huggingface-cli or modelscope)
    device     : 'auto' | 'mps' | 'cpu'
    """

    def __init__(self, repo: str | Path, model_dir: str | Path, device: str = "auto", fp16: bool = False):
        import torch
        repo = Path(repo).expanduser().resolve()
        for p in (repo, repo / "third_party" / "Matcha-TTS"):
            if not p.exists():
                raise FileNotFoundError(f"{p} not found (git clone --recursive https://github.com/FunAudioLLM/CosyVoice)")
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        from cosyvoice.cli.cosyvoice import CosyVoice2                      # noqa: E402  (needs the sys.path above)

        self.torch = torch
        self.device = pick_device(device)
        t0 = time.time()
        # built on the CPU first (the repo's own code only knows cuda/cpu); jit/trt/vllm are CUDA-only
        self.cv = CosyVoice2(str(Path(model_dir).expanduser()), load_jit=False, load_trt=False,
                             load_vllm=False, fp16=False)
        self.sample_rate = self.cv.sample_rate
        if self.device.type != "cpu":
            self._to_device(self.device)
        log.info("CosyVoice2 ready in %.1fs on %s (sample rate %d)", time.time() - t0, self.device, self.sample_rate)
        self.report()

    # -- moving the model ----------------------------------------------------
    def _modules(self):
        m = self.cv.model
        return {"llm": m.llm, "flow": m.flow, "hift": m.hift}

    def _to_device(self, device) -> None:
        m = self.cv.model
        try:
            for mod in self._modules().values():
                mod.to(device).eval()
            m.device = device                       # used by tts() for every input tensor
            self.cv.frontend.device = device        # text / speech-token / speaker-embedding tensors
            self.device = device
            if device.type == "mps":
                self._serialize_mps()
        except Exception as e:                      # never leave the model half on the GPU
            log.error("moving to %s failed (%s): staying on the CPU", device, e)
            self._to_device(self.torch.device("cpu")) if device.type != "cpu" else None

    def _serialize_mps(self) -> None:
        """Streaming runs the LLM token loop in a worker thread while the main thread runs flow + vocoder.
        Metal command buffers are not thread-safe ("failed assertion _status < MTLCommandBufferStatusCommitted"),
        so every GPU step takes one lock: tokens and audio still interleave, but never overlap."""
        lock = threading.RLock()
        m = self.cv.model

        def locked_fn(fn):
            def run(*a, **k):
                with lock:
                    return fn(*a, **k)
            return run

        def locked_gen(fn):
            def run(*a, **k):
                gen = fn(*a, **k)
                while True:
                    with lock:
                        try:
                            v = next(gen)
                        except StopIteration:
                            return
                    yield v
            return run
        m.llm.inference = locked_gen(m.llm.inference)
        if hasattr(m.llm, "inference_bistream"):
            m.llm.inference_bistream = locked_gen(m.llm.inference_bistream)
        m.flow.inference = locked_fn(m.flow.inference)
        m.hift.inference = locked_fn(m.hift.inference)

    def report(self) -> dict:
        """Log (and return) where the weights live and whether MPS really computes: the check asked for."""
        torch = self.torch
        info = {"torch": torch.__version__, "mps_built": torch.backends.mps.is_built(),
                "mps_available": torch.backends.mps.is_available(), "target": str(self.device),
                "fallback_env": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK")}
        for name, mod in self._modules().items():
            devs = {str(p.device) for p in mod.parameters()}
            info[name] = ",".join(sorted(devs))
        info["frontend"] = str(self.cv.frontend.device)
        ok = all(info[k].startswith(self.device.type) for k in ("llm", "flow", "hift"))
        if self.device.type == "mps":
            t = time.time()
            a = torch.randn(2048, 2048, device=self.device)
            for _ in range(10):
                a = a @ a / 2048
            torch.mps.synchronize()
            info["matmul_ms"] = round((time.time() - t) * 1000)
        for k, v in info.items():
            log.info("  %-14s %s", k, v)
        log.info("MPS check: %s", "OK, all model parts are on the GPU" if ok and self.device.type == "mps"
                 else "NOT on MPS (running on %s)" % self.device)
        info["ok"] = ok
        return info

    # -- synthesis --------------------------------------------------------------
    def _generate(self, text, prompt_wav, prompt_text, speed, stream):
        """Yield CosyVoice outputs ({'tts_speech': tensor (1, n)}) in zero-shot / cross-lingual mode."""
        if prompt_wav is None:
            raise ValueError("CosyVoice2 needs a reference voice: pass prompt_wav (and prompt_text)")
        wav = str(Path(prompt_wav).expanduser())
        # CosyVoice warns (and often mumbles) when the line is much shorter than the prompt
        # transcript. Cross-lingual keeps the voice from the clip without that length trap.
        use_text = fit_prompt_text(prompt_text, text) if prompt_text else None
        if use_text:
            gen = self.cv.inference_zero_shot(text, use_text, wav, stream=stream, speed=speed)
        else:
            gen = self.cv.inference_cross_lingual(text, wav, stream=stream, speed=speed)
        with self.torch.inference_mode():
            for out in gen:
                yield out

    def stream(self, text: str, prompt_wav, prompt_text: str | None = None, speed: float = 1.0):
        """Low-latency mode: yields float32 numpy chunks (mono, self.sample_rate) as they are made."""
        wait_for_memory()
        for out in self._generate(text, prompt_wav, prompt_text, speed, stream=True):
            yield out["tts_speech"].squeeze(0).float().cpu().numpy()
        free_memory()

    def synthesize(self, text: str, prompt_wav, prompt_text: str | None = None, speed: float = 1.0):
        """Whole text -> one float32 numpy array (all sentences joined with a short pause)."""
        import numpy as np
        wait_for_memory()
        gap = np.zeros(int(0.25 * self.sample_rate), dtype=np.float32)
        parts = []
        for out in self._generate(text, prompt_wav, prompt_text, speed, stream=False):
            parts += [out["tts_speech"].squeeze(0).float().cpu().numpy(), gap]
        free_memory()
        if not parts:
            raise RuntimeError("CosyVoice returned no audio")
        return np.concatenate(parts[:-1])

    def synthesize_to_file(self, text, out_path, prompt_wav, prompt_text=None, speed=1.0,
                           attempts: int = 3) -> Path:
        """Write a valid narration wav, retrying empty / silent / too-short results."""
        import numpy as np
        import soundfile as sf
        out = Path(out_path); out.parent.mkdir(parents=True, exist_ok=True)
        last = None
        for attempt in range(max(1, attempts)):
            try:
                audio = self.synthesize(text, prompt_wav, prompt_text, speed)
                ok, why = audio_ok(audio, self.sample_rate, text)
                if not ok:
                    raise RuntimeError(why)
                tmp = out.with_name(out.stem + ".part.wav")
                sf.write(str(tmp), audio.astype(np.float32), self.sample_rate)
                tmp.replace(out)                       # never leaves a half-written file behind
                return out
            except Exception as e:
                last = e
                log.warning("synthesis attempt %d/%d failed (%s)", attempt + 1, attempts, e)
                free_memory()
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"CosyVoice could not voice the line after {attempts} tries: {last}")

    def folder_to_audio(self, src: str | Path, out_dir: str | Path, prompt_wav, prompt_text=None,
                        pattern: str = "*.txt", speed: float = 1.0, overwrite: bool = False) -> list[Path]:
        """Every text file in `src` -> `out_dir/<name>.wav`. Existing outputs are skipped (resume-safe)."""
        src, out_dir = Path(src).expanduser(), Path(out_dir).expanduser()
        files = sorted(src.glob(pattern))
        if not files:
            log.warning("no files matching %s in %s", pattern, src)
        done = []
        for i, f in enumerate(files, 1):
            dest = out_dir / f"{f.stem}.wav"
            if dest.exists() and not overwrite:
                log.info("[%d/%d] %s: exists, skipped", i, len(files), dest.name)
                done.append(dest); continue
            text = f.read_text(errors="ignore").strip()
            if not text:
                continue
            t0 = time.time()
            self.synthesize_to_file(text, dest, prompt_wav, prompt_text, speed)
            import soundfile as sf
            secs = sf.info(str(dest)).duration
            log.info("[%d/%d] %s: %.1fs of audio in %.1fs (x%.1f real time)", i, len(files), dest.name,
                     secs, time.time() - t0, (time.time() - t0) / max(secs, 0.1))
            done.append(dest)
        return done


# ------------------------------------------------------------------ command line
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=["folder", "text", "check"])
    ap.add_argument("--repo", required=True, help="cloned FunAudioLLM/CosyVoice repository")
    ap.add_argument("--model", required=True, help="CosyVoice2-0.5B model folder")
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cpu"])
    ap.add_argument("--src", help="folder of text files (folder mode)")
    ap.add_argument("--out", help="output folder (folder mode) or .wav path (text mode)")
    ap.add_argument("--text", help="text to speak (text mode)")
    ap.add_argument("--pattern", default="*.txt")
    ap.add_argument("--prompt-wav", help="reference voice for zero-shot cloning")
    ap.add_argument("--prompt-text", help="transcript of the reference voice (optional)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--stream", action="store_true", help="text mode: use the streaming path and report first-chunk latency")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    tts = CosyVoiceTTS(a.repo, a.model, a.device)
    if a.mode == "check":
        return 0 if tts.report()["ok"] else 1
    if a.mode == "folder":
        tts.folder_to_audio(a.src, a.out, a.prompt_wav, a.prompt_text, a.pattern, a.speed)
    else:
        if a.stream:
            import numpy as np, soundfile as sf
            t0, first, chunks = time.time(), None, []
            for c in tts.stream(a.text, a.prompt_wav, a.prompt_text, a.speed):
                first = first or time.time() - t0
                chunks.append(c)
            log.info("first audio after %.2fs", first or -1)
            sf.write(a.out, np.concatenate(chunks), tts.sample_rate)
        else:
            tts.synthesize_to_file(a.text, a.out, a.prompt_wav, a.prompt_text, a.speed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
