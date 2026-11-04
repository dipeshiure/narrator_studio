"""Bridge for motiversity_auto: burn kinetic captions onto a finished video.

motiversity_auto already knows every word's timing (Whisper on the voice, or the
TTS word boundaries of a script), so this skips kc's own transcription and person
detection: it only adds what the "director" needs (loudness / pitch per word), then
lays out and renders the captions with kc. Runs in kinetic_captions/.venv (numpy +
Pillow only).

    .venv/bin/python mv_bridge.py job.json

job.json:
  {"video": "in.mp4", "out": "out.mp4", "work": "dir",
   "words": [{"w": "Never", "s": 0.42, "e": 0.80}, ...],
   "opts": {"preset": "motiversity", "font": "montserrat", "size": 1.0,
            "text_color": "#FFFFFF", "accent_color": null, "position": "auto"}}
Progress is printed as "PROGRESS <0..1>" lines.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)                      # kc resolves assets/fonts relative to the cwd
sys.path.insert(0, str(HERE))

from kc import transcribe as T      # noqa: E402
from kc.pipeline import apply_overrides, probe_kind  # noqa: E402
from kc.render import render        # noqa: E402
from kc.style import build_layout   # noqa: E402


def say(msg: str) -> None:
    print(msg, flush=True)


def kc_words(words: list[dict], audio) -> list[dict]:
    """motiversity words ({w, s, e}) -> kc words.json entries with delivery features."""
    raw = []
    for w in words:
        text = str(w.get("w", "")).strip()
        if not text:
            continue
        s, e = float(w["s"]), float(w["e"])
        raw.append({"text": text, "start": s, "end": max(e, s + 0.05), "prob": 1.0})
    raw.sort(key=lambda w: w["start"])
    for i in range(1, len(raw)):          # monotonic, non-overlapping (kc expects it)
        if raw[i]["start"] < raw[i - 1]["end"]:
            raw[i - 1]["end"] = max(raw[i - 1]["start"] + 0.02, raw[i]["start"])
    loud_db, f0s, cents = [], [], []
    for w in raw:
        x = T._span(audio, w["start"], w["end"])
        loud_db.append(T._rms_db(x))
        f0s.append(T._f0_autocorr(T._span(audio, w["start"], max(w["end"], w["start"] + 0.1))))
        cents.append(T._spectral_centroid(x))
    loud = T._robust_norm(loud_db)
    voiced = sum(v is not None for v in f0s)
    if raw and voiced >= max(3, 0.3 * len(raw)):
        import numpy as np
        pitch = T._robust_norm([np.log2(v) if v is not None else None for v in f0s])
    else:
        pitch = T._robust_norm(cents)
    out = []
    for i, w in enumerate(raw):
        nxt = raw[i + 1]["start"] if i + 1 < len(raw) else None
        out.append({"text": w["text"], "start": round(w["start"], 3), "end": round(w["end"], 3),
                    "prob": 1.0, "loudness": round(loud[i], 3), "pitch": round(pitch[i], 3),
                    "pause_after": round(max(0.0, nxt - w["end"]), 3) if nxt is not None else 0.0})
    return out


def main(job_path: str) -> int:
    job = json.loads(Path(job_path).read_text())
    video, out = job["video"], job["out"]
    work = Path(job.get("work") or Path(out).with_suffix(".kc"))
    work.mkdir(parents=True, exist_ok=True)
    o = {"preset": "motiversity", "font": "montserrat", "size": 1.0, "text_color": "#FFFFFF",
         "accent_color": None, "position": "auto", **(job.get("opts") or {})}

    info = probe_kind(video)
    W, H = info["width"], info["height"]
    say("字幕の演出を決めています(kinetic-captions)")
    audio = T.load_audio(video)
    words = {"language": "en", "duration": round(len(audio) / T.SR, 3),
             "words": kc_words(job["words"], audio)}
    (work / "words.json").write_text(json.dumps(words, ensure_ascii=False, indent=1))

    layout = build_layout(words, W, H, preset=o["preset"])
    for ph in layout["phrases"]:              # no person matte: every word is drawn in front
        for ln in ph["lines"]:
            for x in ln["words"]:
                x["layer"] = "front"
    # fonts: "" = the preset's own. Presets with a key-word face (bhatt) keep two roles,
    # so the body font and the accent font are swapped separately, word by word.
    body, accent = str(o.get("font") or ""), str(o.get("accent_font") or "")
    if o["preset"] == "cinema":                # one face throughout: the chosen font replaces it, still clean
        if body:
            import kc.render as R
            R.CLEAN_WEIGHTS |= {body}
    elif any(w.get("weight") == "serif_it" for ph in layout["phrases"] for ln in ph["lines"] for w in ln["words"]) \
            or o["preset"] == "bhatt":
        for ph in layout["phrases"]:
            for ln in ph["lines"]:
                for x in ln["words"]:
                    if body and x.get("weight") == "tight":
                        x["weight"] = body
                    elif accent and x.get("weight") == "serif_it":
                        x["weight"] = accent
        import kc.render as R                  # bhatt look = no outline, soft shadow, whatever the face
        R.CLEAN_WEIGHTS |= {f for f in (body, accent) if f}
        body = ""                              # don't let apply_overrides force one face
    layout = apply_overrides({**layout}, {**o, "font": body or "montserrat", "behind": False})
    (work / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=1))

    last = [-1.0]

    def prog(p: float) -> None:
        if p - last[0] >= 0.02 or p >= 1:
            last[0] = p
            say(f"PROGRESS {p:.3f}")

    say(f"字幕を書き込んでいます({len(layout['phrases'])} フレーズ)")
    render(video, layout, out, progress=prog)
    say("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
