"""Sort downloaded clips into mood folders by what they show (local CLIP).

    venv/bin/python tools/sort_anime.py "<source folder>" [--dest media/anime] [--dry-run]

Every clip is looked at (3 frames, the same cached CLIP index as media_match.py) and
compared with a few descriptions per mood; it goes to the closest mood folder. Talking
scenes (two people in a room, a classroom, a face speaking) go to talk_会話: they rarely
suit narration. Files are hard-linked (no extra disk space; the source stays as it is),
named "<work>__<file>" so the same clip from two works never collides. A report with
every decision is written to <dest>/_sort_report.json.
"""
import argparse, json, os, shutil, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import media_match as mm  # noqa: E402

MOODS = {
    "fight": ["anime fight scene, punching", "anime battle with swords", "explosion and action in anime",
                 "two characters fighting", "attack with energy blast"],
    "training": ["anime character training hard", "running and sweating", "doing push ups, exercise",
                    "practicing martial arts alone", "lifting weights"],
    "alone": ["a lone figure walking alone", "a person standing alone, seen from behind",
                 "empty street with one person", "a lonely character looking at the sky", "silhouette at sunset"],
    "rain_storm": ["rain falling", "storm clouds and lightning", "dark gloomy sky", "heavy rain on a street",
                      "wind and thunder"],
    "sad": ["a crying anime character", "sad face with tears", "person with head down in grief",
                 "a character sitting alone feeling sad", "despair"],
    "victory": ["anime character shouting with power", "triumphant pose, victory", "glowing aura of power",
                   "a determined face, clenched fist", "standing tall after winning"],
    "nature": ["landscape with sky and clouds", "city skyline", "ocean and horizon", "mountains", "trees and sunlight"],
    "talk": ["two people talking in a room", "students talking in a classroom", "close-up of a face speaking",
                "people sitting at a table having a conversation", "a teacher in front of a class"],
}
TALK = "talk"
MIXED = "mixed"
# CLIP scores some moods higher than others on everything (fight, sad); each mood's
# scores are standardised across the folder before comparing, and a clip that is not
# clearly one mood goes to MIXED instead of being forced somewhere.
MIN_Z, MIN_GAP = 0.8, 0.3


def mood_vectors():
    import torch
    keys, vecs = [], []
    for mood, prompts in MOODS.items():
        e = mm.embed_text(prompts)                      # (n, 512), normalised
        keys.append(mood)
        vecs.append(e)
    return keys, vecs, torch


def classify(item, keys, vecs, torch):
    frames = torch.tensor([f["vec"] for f in item["frames"]])           # (k, 512)
    scores = []
    for v in vecs:
        sim = frames @ v.T                                               # (k, prompts)
        scores.append(float(sim.max(dim=1).values.mean()))              # best prompt per frame, averaged
    order = sorted(range(len(keys)), key=lambda i: -scores[i])
    return keys[order[0]], scores[order[0]], scores[order[0]] - scores[order[1]], dict(zip(keys, scores))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--dest", default=str(HERE / "media" / "anime"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src, dest = Path(a.source).expanduser().resolve(), Path(a.dest).expanduser().resolve()
    index = mm.index_folder(src, log=lambda m: print(m, flush=True))
    keys, vecs, torch = mood_vectors()
    report, counts = [], Counter()
    items = [it for it in index["items"] if it["kind"] == "video"]
    raw = torch.tensor([[classify(it, keys, vecs, torch)[3][k] for k in keys] for it in items])
    z = (raw - raw.mean(0)) / raw.std(0).clamp_min(1e-6) if len(items) > 1 else raw
    for it, row, zrow in zip(items, raw, z):
        p = Path(it["path"])
        top = zrow.topk(2)
        score, margin = float(top.values[0]), float(top.values[0] - top.values[1])
        mood = keys[int(top.indices[0])] if score >= MIN_Z and margin >= MIN_GAP else MIXED
        all_scores = dict(zip(keys, row.tolist()))
        rel = [x.strip() for x in p.relative_to(src).parts]
        # "<work>__<episode folder>__<file>": the same file name can sit in two episode folders
        target = dest / mood / "__".join(rel if len(rel) > 1 else ["misc"] + rel)
        counts[mood] += 1
        report.append({"file": str(p), "mood": mood, "score": round(score, 4), "margin": round(margin, 4),
                       "target": str(target), "scores": {k: round(v, 4) for k, v in all_scores.items()}})
        if a.dry_run or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(p, target)                  # same disk: no copy, no extra space
        except OSError:
            shutil.copy2(p, target)
    if not a.dry_run:
        # drop links this script made last time that are no longer wanted (renamed or moved
        # to another mood) -- only ones recorded in the old report and still the same file
        old = dest / "_sort_report.json"
        keep = {r["target"] for r in report}
        for r in (json.loads(old.read_text()) if old.exists() else []):
            t_, s_ = Path(r["target"]), Path(r["file"])
            if r["target"] not in keep and t_.exists() and s_.exists() and os.path.samefile(t_, s_):
                t_.unlink()
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "_sort_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for mood in keys + [MIXED]:
        print(f"{mood:18s} {counts[mood]:4d}")
    print(f"total {sum(counts.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
