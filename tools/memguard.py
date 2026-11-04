"""Pause heavy GPU work while the Mac is short of memory, so it never has to swap
(swap = writes to the SSD). Imported by flux_batch.py and dia_batch.py (stdlib only)."""
import contextlib
import fcntl
import json
import re
import subprocess
import urllib.request
import sys
import time
from pathlib import Path

LOW = 25        # % free: stop generating here...
RESUME = 35     # ...and continue once this much is free again


def free_pct():
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=10).stdout
        return int(re.search(r"free percentage:\s*(\d+)", out).group(1))
    except Exception:
        return 100


GPU_LOCK = Path(__file__).resolve().parents[1] / "tmp" / "gpu.lock"


@contextlib.contextmanager
def gpu_lock():
    """Only one GPU-heavy process at a time (FLUX or Dia, never both): held for the whole
    batch, across processes (the 台本 page's voice sample, a render, a manual run)."""
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(GPU_LOCK, "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("GPUWAIT", flush=True)
            fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def unload_llms():
    """Drop every model the local Ollama server holds (it keeps them loaded for minutes)."""
    try:
        base = "http://127.0.0.1:11434/api"
        loaded = json.load(urllib.request.urlopen(base + "/ps", timeout=5)).get("models", [])
        for m in loaded:
            req = urllib.request.Request(base + "/generate", json.dumps({"model": m["name"], "keep_alive": 0}).encode(),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def wait_for_memory(clear=None):
    """Call between items. Blocks while free memory is below LOW (prints MEMWAIT once)."""
    if free_pct() >= LOW:
        return
    if clear:
        clear()                                  # first give back our own cached buffers
        if free_pct() >= LOW:
            return
    print(f"MEMWAIT {free_pct()}", flush=True)
    while free_pct() < RESUME:
        time.sleep(5)
    print("MEMOK", flush=True)


if __name__ == "__main__":
    print(free_pct())
    sys.exit(0)
