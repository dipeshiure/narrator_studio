"""Resumable download of a model repo into tools/models/ (curl -C -).
Files already complete in the Hugging Face cache are linked instead of downloaded.

    venv/bin/python tools/fetch_flux.py                                   # FLUX.1-schnell 4-bit
    venv/bin/python tools/fetch_flux.py <owner/repo> <folder name>        # any other repo
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

REPO = sys.argv[1] if len(sys.argv) > 2 else "dhairyashil/FLUX.1-schnell-mflux-4bit"
HERE = Path(__file__).resolve().parent
DEST = HERE / "models" / (sys.argv[2] if len(sys.argv) > 2 else "flux1-schnell-mflux-4bit")
CACHE = Path.home() / ".cache/huggingface/hub" / ("models--" + REPO.replace("/", "--")) / "snapshots"


def files():
    url = f"https://huggingface.co/api/models/{REPO}/tree/main?recursive=true"
    for i in range(10):
        try:
            return [(x["path"], x["size"]) for x in json.load(urllib.request.urlopen(url, timeout=30))
                    if x["type"] == "file"]
        except Exception as e:
            print("file list failed:", e, flush=True); time.sleep(10)
    sys.exit("could not list the model files")


def main():
    todo = files()
    for path, size in todo:
        out = DEST / path
        out.parent.mkdir(parents=True, exist_ok=True)
        cached = next((p / path for p in CACHE.glob("*") if (p / path).exists()), None) if CACHE.exists() else None
        if not out.exists() and cached and cached.resolve().stat().st_size == size:
            os.link(cached.resolve(), out)
        part = out.with_name(out.name + ".part")
        while not (out.exists() and out.stat().st_size == size):
            if part.exists() and part.stat().st_size > size:     # junk appended by a broken resume
                print(f"{path}: partial file is larger than the real file, starting it again", flush=True)
                part.unlink()
            r = subprocess.run(["curl", "-sSL", "--fail", "-C", "-", "--retry", "20", "--retry-all-errors",
                                "--speed-limit", "20000", "--speed-time", "60", "-o", str(part),
                                f"https://huggingface.co/{REPO}/resolve/main/{path}"])
            if part.exists() and part.stat().st_size == size:
                part.replace(out)
            elif r.returncode:
                print(f"{path}: curl {r.returncode}, {part.stat().st_size if part.exists() else 0}/{size}, retrying",
                      flush=True)
                time.sleep(5)
        print(f"ok {path} ({size / 1e6:.0f} MB)", flush=True)
    (DEST / ".complete").write_text("ok\n")
    print("model ready:", DEST, flush=True)


if __name__ == "__main__":
    main()
