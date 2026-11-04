"""Compare ad/intro detectors: current pipeline vs old raw LLM vs Flan-T5.

Run:  venv/bin/python bench/compare_t5.py      (writes bench/results.md)

Datasets (label True = ad / promo / channel greeting, i.e. should be cut):
  struggle  : real transcript of 04_Struggle Makes You STRONGER (48 sentences)
  adtest    : real speech with a synthetic app-ad intro and subscribe outro (12)
  tricky    : hand-written sentences, incl. speech that *sounds* like an ad (40)
"""
import json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import make_video as mv  # noqa: E402
import prep  # noqa: E402

MODEL = "qwen2.5:7b-instruct"


# ---------------------------------------------------------------- datasets
def load_sets():
    sets = {}
    w = json.loads((mv.TMP / "c8cbbb6a7870" / "words_voice.json").read_text())
    ss = prep.sentences_from_words(w)
    for x in ss:
        t = x["text"].strip().lower()
        x["label"] = t.startswith("thank you for watching")
        if t == "thank you.":          # short "Thank you." mid-speech after a music break: ambiguous
            x["label"] = None
    sets["struggle"] = (ss, w)

    w = json.loads((ROOT / "bench/work/adtest/words.json").read_text())
    ss = prep.sentences_from_words(w)
    for i, x in enumerate(ss):
        x["label"] = i in (0, 1, 2) or "watching" in x["text"].lower() or "subscribe" in x["text"].lower()
    sets["adtest"] = (ss, w)

    ads = [
        "Hey guys, welcome back to the channel.", "Before we get started, a quick word from today's sponsor.",
        "This video is brought to you by BetterHelp.", "Head over to the link in the description.",
        "Use code RISE at checkout to get 15 percent off.", "Download the app for free on the App Store.",
        "If you enjoyed this video, hit the like button.", "Make sure you subscribe and turn on notifications.",
        "Follow us on Instagram for daily motivation.", "Check out our new merch store.",
        "Let me know in the comments what you thought.", "Thanks so much for watching, see you next time.",
        "Our podcast is available on Spotify every Monday.", "Sign up for the free newsletter on our website.",
        "Audible is offering a free thirty day trial.", "Today's episode is sponsored by Squarespace.",
        "Don't forget to share this video with a friend.", "Support the channel on Patreon.",
        "Grab your copy of the book, link below.", "That's it for today's video, peace out.",
    ]
    speech = [
        "Subscribe to your own goals, not to other people's opinions.", "Invest in yourself before anything else.",
        "Download a new mindset every single morning.", "Follow your dreams even when nobody believes in you.",
        "Like a lion, you have to hunt for what you want.", "The price of greatness is responsibility.",
        "Nobody is coming to save you.", "Your struggle is your gift.",
        "Wake up at five and go to work on yourself.", "Money will not make you happy, purpose will.",
        "Thank you, pain, for making me stronger.", "Check your excuses at the door.",
        "Share your story with the world, it might save someone.", "You were born to win.",
        "Comment less on other people's lives and build your own.", "The bell rings and the fight begins.",
        "Watch what happens when you refuse to quit.", "Every video of your life is still being recorded.",
        "Stay focused, stay hungry, stay humble.", "Today is the day you stop making excuses.",
    ]
    tricky = [{"s": i, "e": i + 1, "text": t, "label": True} for i, t in enumerate(ads)] + \
             [{"s": 100 + i, "e": 101 + i, "text": t, "label": False} for i, t in enumerate(speech)]
    sets["tricky"] = (tricky, None)
    return sets


# ---------------------------------------------------------------- detectors
def current(ss, words):
    """What the app does now: keywords + gated LLM + comma split + islands."""
    import copy
    items = copy.deepcopy(ss)
    for x in items:
        x.setdefault("wi", None)
    out = prep.classify([dict(x) for x in items], MODEL, log=lambda m: None, words=words)
    # comma splitting can change the sentence list: map back by time overlap
    return [any(o["cut"] and o["s"] < x["e"] and o["e"] > x["s"] for o in out) for x in ss]


def old_raw_llm(ss, words):
    """Previous behaviour: trust every index the LLM lists (50 per request), plus keywords."""
    import urllib.request
    flagged = set()
    old_prompt = prep.PROMPT.split("Be conservative")[0] + \
        'Return JSON: {{"cut": [{{"i": <number>, "reason": "<category>"}}]}}\n\n{sentences}'
    for start in range(0, len(ss), 50):
        part = ss[start:start + 50]
        listing = "\n".join(f"{start + j} [{x['s']:.0f}s]: {x['text']}" for j, x in enumerate(part))
        body = json.dumps({"model": MODEL, "stream": False, "format": "json", "options": {"temperature": 0},
                           "messages": [{"role": "user", "content": old_prompt.format(sentences=listing)}]})
        req = urllib.request.Request(prep.OLLAMA, body.encode(), {"Content-Type": "application/json"})
        reply = json.loads(json.load(urllib.request.urlopen(req, timeout=300))["message"]["content"])
        for it in reply.get("cut", []):
            try:
                flagged.add(int(it["i"]))
            except Exception:
                pass
    return [i in flagged or bool(prep.AD_RE.search(x["text"])) for i, x in enumerate(ss)]


T5_PROMPT = ('Here is a sentence from the audio of a motivational speech video.\n'
             'Sentence: "{s}"\n'
             'Is this sentence an advertisement, a sponsor message, a product or app promotion, '
             'a request to like, subscribe, comment or follow, or a channel greeting or goodbye, '
             'rather than part of the motivational speech? Answer yes or no.')
_t5 = {}


def t5_yes(texts, name):
    import torch
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    if name not in _t5:
        tok = AutoTokenizer.from_pretrained(name)
        model = AutoModelForSeq2SeqLM.from_pretrained(name).eval()
        _t5[name] = (tok, model)
    tok, model = _t5[name]
    yes, no = tok("yes").input_ids[0], tok("no").input_ids[0]
    out = []
    for k in range(0, len(texts), 16):
        batch = [T5_PROMPT.format(s=t) for t in texts[k:k + 16]]
        enc = tok(batch, return_tensors="pt", padding=True)
        start = torch.full((len(batch), 1), model.config.decoder_start_token_id)
        with torch.no_grad():
            logits = model(**enc, decoder_input_ids=start).logits[:, 0, :]
        out += (logits[:, yes] > logits[:, no]).tolist()
    return out


def t5(name):
    return lambda ss, words: t5_yes([x["text"] for x in ss], name)


def t5_gated(name):
    """Flan-T5 with the same safety rules the app uses (keyword OR (T5 yes AND cue word))."""
    def run(ss, words):
        y = t5_yes([x["text"] for x in ss], name)
        return [bool(prep.AD_RE.search(x["text"])) or (v and bool(prep.CUE_RE.search(x["text"])))
                for v, x in zip(y, ss)]
    return run


DETECTORS = [
    ("今の仕組み(キーワード + qwen2.5 + 安全装置)", current),
    ("前の仕組み(qwen2.5 をそのまま信用)", old_raw_llm),
    ("Flan-T5 base(250M)", t5("google/flan-t5-base")),
    ("Flan-T5 large(780M)", t5("google/flan-t5-large")),
    ("Flan-T5 large + 同じ安全装置", t5_gated("google/flan-t5-large")),
]


# ---------------------------------------------------------------- scoring
def score(pred, ss):
    tp = fp = fn = tn = 0
    wrong = []
    for p, x in zip(pred, ss):
        if x["label"] is None:
            continue
        if p and x["label"]: tp += 1
        elif p and not x["label"]: fp += 1; wrong.append(("誤カット", x["text"]))
        elif not p and x["label"]: fn += 1; wrong.append(("見逃し", x["text"]))
        else: tn += 1
    return tp, fp, fn, tn, wrong


def main():
    sets = load_sets()
    rows, details = [], []
    for name, fn in DETECTORS:
        tot = [0, 0, 0, 0]; per = {}; t0 = time.time()
        for sname, (ss, words) in sets.items():
            tp, fp, fn_, tn, wrong = score(fn(ss, words), ss)
            per[sname] = (tp, fp, fn_)
            tot = [a + b for a, b in zip(tot, (tp, fp, fn_, tn))]
            details += [f"- {name} / {sname}: {kind}「{t}」" for kind, t in wrong]
        tp, fp, fn_, tn = tot
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / (tp + fn_) if tp + fn_ else 0
        rows.append((name, tp, fp, fn_, prec, rec, time.time() - t0, per))
        print(name, "TP", tp, "誤カット", fp, "見逃し", fn_, f"{time.time() - t0:.1f}s", flush=True)

    n_ads = sum(x["label"] is True for ss, _ in sets.values() for x in ss)
    n_speech = sum(x["label"] is False for ss, _ in sets.values() for x in ss)
    md = [f"# 宣伝判定の比較\n\n宣伝・告知 {n_ads} 文 / スピーチ {n_speech} 文(3セット合計)\n",
          "| 方法 | 正しくカット | 誤カット(スピーチを切った) | 見逃し(宣伝が残った) | 適合率 | 再現率 | 時間 |",
          "|---|---|---|---|---|---|---|"]
    for name, tp, fp, fn_, prec, rec, sec, per in rows:
        md.append(f"| {name} | {tp} | {fp} | {fn_} | {prec:.0%} | {rec:.0%} | {sec:.0f}秒 |")
    md += ["", "## セット別(正しくカット / 誤カット / 見逃し)", "",
           "| 方法 | " + " | ".join(sets) + " |", "|---|" + "---|" * len(sets)]
    for name, *_, per in rows:
        md.append(f"| {name} | " + " | ".join(f"{a} / {b} / {c}" for a, b, c in per.values()) + " |")
    md += ["", "## 間違えた文", ""] + details
    (ROOT / "bench/results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
