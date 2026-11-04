"""Word-timed transcription on the Apple GPU (Whisper via mlx-audio; runs in tools/dia_venv).

    tools/dia_venv/bin/python tools/stt.py <model id> <audio> [<audio> ...]

Prints one JSON line per audio: [{"w": "Every", "s": 0.0, "e": 0.26}, ...] (English).
About 10x faster than openai-whisper on the CPU of an M-series Mac, same Whisper weights.
"""
import json
import sys

from mlx_audio.stt.utils import load_model


def main(model_id, paths):
    model = load_model(model_id)
    for p in paths:
        r = model.generate(p, language="en", word_timestamps=True)
        words = [{"w": w["word"].strip(), "s": round(float(w["start"]), 3), "e": round(float(w["end"]), 3)}
                 for seg in (r.segments or []) for w in seg.get("words", []) if w["word"].strip()]
        print(json.dumps(words), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
