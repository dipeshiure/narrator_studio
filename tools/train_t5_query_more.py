"""Continue the saved T5 for about 15 minutes on clean script-to-query pairs.

Starts from tools/models/flan-t5-ad-en and saves the best holdout checkpoint
back into that same folder. Stops early if free memory falls under 25% or
swap use jumps.

Run:  venv/bin/python tools/train_t5_query_more.py
"""
import json
import random
import subprocess
import time
from pathlib import Path

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "models" / "flan-t5-ad-en"
DATA = ROOT / "tools" / "data" / "t5_search_clean.json"
MINUTES = 15
PROMPT = (
    'Here is a line from a narration script.\n'
    'Line: "{s}"\n'
    "Write a stock-video search query of 2 to 4 English words. "
    "Include the place and the mood of this line. Reply with the query only."
)


def memory_stats():
    text = subprocess.check_output(["memory_pressure"], text=True)
    free, swapouts = None, None
    for line in text.splitlines():
        if "free percentage" in line:
            free = int(line.split()[-1].rstrip("%"))
        elif line.strip().startswith("Swapouts:"):
            swapouts = int(line.split()[-1])
    return free if free is not None else 100, swapouts if swapouts is not None else 0


def check_pair(text, query):
    words = query.split()
    if not (2 <= len(words) <= 4):
        raise SystemExit(f"query length: {query}")
    if query != query.lower() or any(ch in query for ch in ".,!?;:'\""):
        raise SystemExit(f"query not clean: {query}")
    if not text.endswith("."):
        raise SystemExit(f"line not a sentence: {text}")


def load_data():
    raw = json.loads(DATA.read_text())
    train = [tuple(row) for row in raw["train"]]
    holdout = [tuple(row) for row in raw["holdout"]]
    seen = set()
    for text, query in train + holdout:
        check_pair(text, query)
        if text in seen:
            raise SystemExit(f"duplicate line: {text}")
        seen.add(text)
    leaked = {t for t, _ in train} & {t for t, _ in holdout}
    if leaked:
        raise SystemExit(f"holdout leaked into train: {leaked}")
    return train, holdout


def encode(tokenizer, pairs):
    prompts = [PROMPT.format(s=text) for text, _ in pairs]
    targets = [query for _, query in pairs]
    batch = tokenizer(prompts, padding=True, truncation=True, max_length=192, return_tensors="pt")
    labels = tokenizer(targets, padding=True, truncation=True, max_length=16, return_tensors="pt")
    lab = labels["input_ids"]
    lab[lab == tokenizer.pad_token_id] = -100
    return batch["input_ids"], batch["attention_mask"], lab


def predict(model, tokenizer, text, device):
    ids = tokenizer(PROMPT.format(s=text), return_tensors="pt", truncation=True, max_length=192)
    ids = {k: v.to(device) for k, v in ids.items()}
    with torch.no_grad():
        out = model.generate(**ids, max_new_tokens=12)
    return tokenizer.decode(out[0], skip_special_tokens=True).strip()


def score_holdout(model, tokenizer, holdout, device):
    model.eval()
    total = 0.0
    exact = 0
    lines = []
    for text, gold in holdout:
        pred = predict(model, tokenizer, text, device).lower()
        gw, pw = set(gold.split()), set(pred.split())
        hit = len(gw & pw) / len(gw)
        ok = pred == gold
        exact += int(ok)
        total += 0.7 * hit + 0.3 * int(ok)
        lines.append(f"    {pred!r}  want {gold!r}")
    model.train()
    return total / len(holdout), exact, lines


def main():
    train, holdout = load_data()
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    free, swap0 = memory_stats()
    print(f"memory free {free}%  swapouts {swap0}  device {device}", flush=True)
    print(f"clean pairs {len(train)}  holdout {len(holdout)}", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(OUT)
    model = AutoModelForSeq2SeqLM.from_pretrained(OUT).to(device)
    before, before_exact, before_lines = score_holdout(model, tokenizer, holdout, device)
    print(f"before  score {before:.2f}  exact {before_exact}/{len(holdout)}", flush=True)
    print("\n".join(before_lines), flush=True)

    ids, mask, labels = encode(tokenizer, train)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-5, weight_decay=0.01)
    order = list(range(len(train)))
    best_score, best_state, best_epoch = before, None, 0
    start = time.time()
    epoch = 0
    model.train()
    while time.time() - start < MINUTES * 60:
        epoch += 1
        random.Random(epoch).shuffle(order)
        loss_sum = 0.0
        for start_i in range(0, len(order), 8):
            batch = order[start_i:start_i + 8]
            opt.zero_grad()
            out = model(
                input_ids=ids[batch].to(device),
                attention_mask=mask[batch].to(device),
                labels=labels[batch].to(device),
            )
            out.loss.backward()
            opt.step()
            loss_sum += float(out.loss.detach()) * len(batch)
        score, exact, lines = score_holdout(model, tokenizer, holdout, device)
        free, swapouts = memory_stats()
        elapsed = int(time.time() - start)
        mark = ""
        if score > best_score:
            best_score, best_epoch = score, epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            mark = "  best"
        print(
            f"epoch {epoch}  {elapsed // 60}:{elapsed % 60:02d}  "
            f"loss {loss_sum / len(train):.3f}  score {score:.2f}  "
            f"exact {exact}/{len(holdout)}  free {free}%  swap {swapouts}{mark}",
            flush=True,
        )
        if free < 25 or swapouts > swap0 + 2000:
            print("stopping to leave memory free", flush=True)
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    after, after_exact, after_lines = score_holdout(model, tokenizer, holdout, device)
    print(f"saved best epoch {best_epoch}  score {after:.2f}  exact {after_exact}/{len(holdout)}", flush=True)
    print("\n".join(after_lines), flush=True)
    model.save_pretrained(OUT)
    tokenizer.save_pretrained(OUT)
    (OUT / "train.json").write_text(json.dumps({
        "task": "script_to_search_query",
        "prompt": PROMPT,
        "started_from": "flan-t5-ad-en",
        "data": "tools/data/t5_search_clean.json",
        "minutes": MINUTES,
        "best_epoch": best_epoch,
        "holdout_score": round(after, 3),
        "train": train,
        "holdout": holdout,
    }, ensure_ascii=False, indent=2))
    print(f"saved {OUT}", flush=True)


if __name__ == "__main__":
    main()
