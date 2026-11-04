"""Mock of the core: script -> beats -> timed shots with context-matched assets.

Stdlib only. The script uses inline tags so matching never has to guess:
  [[entity:keyence]]  -> must-show subject (logo / photo / founder)
  [[visual:chart:operating_margin]] -> generated data card instead of stock footage
Untagged sentences fall back to keyword scoring against the asset index.
"""
import math
import re
from collections import Counter

WPS = 2.6          # English TTS speed (~155 wpm); replaced by real word timings later
MIN_SHOT, MAX_SHOT = 2.0, 6.0   # competitor-level pacing: cut every 2-6 s

ASSETS = [  # in the real tool: built from a folder + sidecar JSON tags
    {"id": "keyence_logo.png",        "tags": ["keyence", "logo", "company"]},
    {"id": "keyence_hq_osaka.jpg",    "tags": ["keyence", "headquarters", "osaka", "building"]},
    {"id": "takizaki_portrait.jpg",   "tags": ["takizaki", "founder", "keyence"]},
    {"id": "factory_sensor_line.mp4", "tags": ["factory", "sensor", "automation", "line"]},
    {"id": "salesman_meeting.mp4",    "tags": ["sales", "salesman", "meeting", "client"]},
    {"id": "tokyo_skyline_night.mp4", "tags": ["japan", "tokyo", "city", "economy"]},
    {"id": "yen_coins.mp4",           "tags": ["yen", "money", "salary", "economy"]},
    {"id": "office_workers.mp4",      "tags": ["office", "employees", "salary", "work"]},
]
GENERIC = ["japan_map_card", "title_card"]  # always-available fallback (generated)

STOP = set("the a an of to and in is it that for on with as by was are this at from be its their they".split())


def tokens(text):
    return [w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP]


def idf_table(assets):
    df = Counter(t for a in assets for t in set(a["tags"]))
    n = len(assets)
    return {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}


IDF = idf_table(ASSETS)


def parse_beats(script):
    # Tags belong to the LINE they are written on (fix: splitting on "." first
    # pushed a trailing tag onto the next sentence and shifted the visuals).
    beats = []
    for line in script.strip().splitlines():
        entities = re.findall(r"\[\[entity:(\w+)\]\]", line)
        visuals = re.findall(r"\[\[visual:([\w:]+)\]\]", line)
        clean = re.sub(r"\[\[.*?\]\]", "", line).strip()
        for j, sent in enumerate(s for s in re.split(r"(?<=[.!?])\s+", clean) if s):
            beats.append({"text": sent, "entities": entities if j == 0 else [],
                          "visuals": visuals if j == 0 else []})
    return beats


def proper_nouns(text):
    # capitalised words that are not sentence-initial = names/places -> boosted
    return {w.lower() for w in re.findall(r"(?<!^)(?<![.!?] )\b[A-Z][a-z]+", text)}


def score(asset, words, entities, text=""):
    pn, n = proper_nouns(text), max(len(words), 1)
    s = 0.0
    for pos, w in enumerate(words):
        if w in asset["tags"]:
            w_score = IDF.get(w, 0) * (2.0 if w in pn else 1.0)
            s += w_score * (1 + 0.2 * (n - pos) / n)  # earlier = more likely the subject
    s += sum(5.0 for e in entities if e in asset["tags"])  # entity is a hard priority
    return s


def plan(script):
    t, timeline, recent = 0.0, [], []
    for b in parse_beats(script):
        dur = max(MIN_SHOT, len(b["text"].split()) / WPS)
        n_shots = max(1, math.ceil(dur / MAX_SHOT))
        for i in range(n_shots):
            seg = dur / n_shots
            if b["visuals"] and i == 0:
                src, why = "GEN:" + b["visuals"][0], "explicit visual tag"
            else:
                ranked = sorted(ASSETS, key=lambda a: -score(a, tokens(b["text"]), b["entities"], b["text"]))
                # anti-repetition: skip anything used in the last 3 shots
                pick = next((a for a in ranked if a["id"] not in recent[-3:]
                             and score(a, tokens(b["text"]), b["entities"], b["text"]) > 0), None)
                if pick:
                    src, why = pick["id"], f"score={score(pick, tokens(b['text']), b['entities'], b['text']):.2f}"
                else:
                    src, why = "GEN:" + GENERIC[len(timeline) % 2], "fallback"
            recent.append(src)
            timeline.append((round(t, 2), round(t + seg, 2), src, why, b["text"][:48]))
            t += seg
    return timeline


SCRIPT = """
In 1974, a 29-year-old named Takizaki founded a tiny sensor company in Osaka. [[entity:keyence]]
Today [[entity:keyence]] runs an operating margin above fifty percent. [[visual:chart:operating_margin]]
Most Japanese factories would kill for a margin of ten.
The secret is not the sensor. It is the salesman, and how every client meeting is scripted.
Its employees earn salaries that rival Wall Street, in a country where wages have been flat for thirty years.
Nobody outside Japan talks about this.
"""

if __name__ == "__main__":
    tl = plan(SCRIPT)
    for row in tl:
        print(f"{row[0]:6.2f}-{row[1]:6.2f}  {row[2]:28s} {row[3]:20s} | {row[4]}")
    durs = [b - a for a, b, *_ in tl]
    assert all(d >= MIN_SHOT - 1e-6 and d <= MAX_SHOT + 1e-6 for d in durs), "pacing violated"
    assert all(tl[i][2] != tl[i + 1][2] for i in range(len(tl) - 1)), "same asset back-to-back"
    print(f"\nOK: {len(tl)} shots, {tl[-1][1]:.1f}s, avg shot {sum(durs)/len(durs):.2f}s, "
          f"fallback {sum('fallback' in r[3] for r in tl)}/{len(tl)}")
