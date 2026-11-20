"""Teach the already-trained Flan-T5 to turn a narration line into a search query.

Starts from tools/models/flan-t5-ad-en (the yes/no checkpoint) and saves back
into that same folder, so the app keeps one T5.

Run:  venv/bin/python tools/train_t5_query.py
"""
import json
import random
from pathlib import Path

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "models" / "flan-t5-ad-en"
PROMPT = (
    'Here is a line from a narration script.\n'
    'Line: "{s}"\n'
    "Write a stock-video search query of 2 to 4 English words. "
    "Include the place and the mood of this line. Reply with the query only."
)

HOLDOUT = [
    ("A lighthouse stands alone above a rough sea at dusk.", "lonely lighthouse dusk"),
    ("He walks through an empty city at night.", "empty city night"),
    ("The boxer sits alone after the loss.", "boxer sitting quiet"),
    ("Snow falls on an empty field.", "snow empty field"),
]

TRAIN = [
    ("Silence sits in the room after the argument.", "quiet dark room"),
    ("The crowd roars as he steps into the ring.", "boxing crowd roar"),
    ("Rain hits the window while he waits.", "rain window night"),
    ("A man runs alone on a dark road.", "lone runner night"),
    ("Faith keeps the small flame burning.", "candle flame dark"),
    ("The bell rings and the fight begins.", "boxing bell fight"),
    ("Nobody is coming to save you.", "lone figure storm"),
    ("Thank you, pain, for making me stronger.", "athlete training grit"),
    ("She watches the sunrise from an empty beach.", "empty beach sunrise"),
    ("The old church is quiet except for one candle.", "quiet church candle"),
    ("He lifts the weight again, sweat on the floor.", "gym weight sweat"),
    ("Fog covers the forest path at dawn.", "fog forest dawn"),
    ("A ship cuts through black water.", "ship night ocean"),
    ("Children laugh in a bright classroom.", "bright classroom children"),
    ("The door closes and the hallway goes still.", "empty hallway quiet"),
    ("Firelight moves across his face.", "firelight face dark"),
    ("He stands on the cliff and does not step back.", "cliff edge wind"),
    ("The city lights blur from the train window.", "train window city night"),
    ("Dust hangs in a sunbeam in the empty library.", "dusty library sunbeam"),
    ("Two hands let go in the rain.", "hands rain goodbye"),
    ("Waves break under a grey sky.", "grey waves storm"),
    ("A single streetlamp lights the wet road.", "wet street night"),
    ("He opens the old book and starts again.", "reading book quiet"),
    ("The engine starts and the road opens.", "car road dawn"),
    ("She breathes out and the room softens.", "calm room soft light"),
    ("The crowd is gone and the stage is dark.", "empty dark stage"),
    ("He stares at the ceiling and cannot sleep.", "sleepless dark ceiling"),
    ("The mountain path is steep and the wind is cold.", "cold mountain wind"),
    ("A mother waits by the window until morning.", "window waiting dawn"),
    ("The team celebrates under bright stadium lights.", "stadium lights celebration"),
]


def encode(tokenizer, pairs):
    prompts = [PROMPT.format(s=text) for text, _ in pairs]
    targets = [query for _, query in pairs]
    batch = tokenizer(prompts, padding=True, truncation=True, max_length=256, return_tensors="pt")
    labels = tokenizer(targets, padding=True, truncation=True, max_length=16, return_tensors="pt")
    lab = labels["input_ids"]
    lab[lab == tokenizer.pad_token_id] = -100
    return batch["input_ids"], batch["attention_mask"], lab


def predict(model, tokenizer, text, device):
    ids = tokenizer(PROMPT.format(s=text), return_tensors="pt", truncation=True, max_length=256)
    ids = {k: v.to(device) for k, v in ids.items()}
    with torch.no_grad():
        out = model.generate(**ids, max_new_tokens=12)
    return tokenizer.decode(out[0], skip_special_tokens=True).strip()


def main():
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(OUT)
    model = AutoModelForSeq2SeqLM.from_pretrained(OUT).to(device)
    print("before:", flush=True)
    for text, want in HOLDOUT:
        print(f"  {predict(model, tokenizer, text, device)!r}  (example {want!r})", flush=True)

    ids, mask, labels = encode(tokenizer, TRAIN)
    torch.manual_seed(0)
    order = list(range(len(TRAIN)))
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4)
    model.train()
    for epoch in range(8):
        random.Random(epoch).shuffle(order)
        total = 0.0
        for start in range(0, len(order), 8):
            batch = order[start:start + 8]
            opt.zero_grad()
            out = model(
                input_ids=ids[batch].to(device),
                attention_mask=mask[batch].to(device),
                labels=labels[batch].to(device),
            )
            out.loss.backward()
            opt.step()
            total += float(out.loss) * len(batch)
        print(f"epoch {epoch + 1}/8  loss {total / len(TRAIN):.3f}", flush=True)

    model.eval()
    print("after:", flush=True)
    for text, want in HOLDOUT:
        print(f"  {predict(model, tokenizer, text, device)!r}  (example {want!r})", flush=True)
    model.save_pretrained(OUT)
    tokenizer.save_pretrained(OUT)
    (OUT / "train.json").write_text(json.dumps(
        {"task": "script_to_search_query", "train": TRAIN, "holdout": HOLDOUT,
         "prompt": PROMPT, "started_from": "flan-t5-ad-en"}, ensure_ascii=False, indent=2))
    print(f"saved {OUT}", flush=True)


if __name__ == "__main__":
    main()
