"""Fine-tune Flan-T5 on a small English set: ad / greeting versus motivational speech.

The bench showed google/flan-t5-base answers "no" to almost every sentence.
A few labeled examples are enough for it to separate the two.

Run:  venv/bin/python tools/train_t5_ad.py
Saves: tools/models/flan-t5-ad-en
"""
import json
import random
from pathlib import Path

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "models" / "flan-t5-ad-en"
BASE = "google/flan-t5-base"
PROMPT = (
    'Here is a sentence from the audio of a motivational speech video.\n'
    'Sentence: "{s}"\n'
    "Is this sentence an advertisement, a sponsor message, a product or app promotion, "
    "a request to like, subscribe, comment or follow, or a channel greeting or goodbye, "
    "rather than part of the motivational speech? Answer yes or no."
)

# Held out of training. Several of these are lines the untuned model missed.
HOLDOUT = [
    ("Audible is offering a free thirty day trial.", "yes"),
    ("Let me know in the comments what you thought.", "yes"),
    ("That's it for today's video, peace out.", "yes"),
    ("Hey guys, before we start, download the karma app.", "yes"),
    ("Subscribe to your own goals, not to other people's opinions.", "no"),
    ("Thank you, pain, for making me stronger.", "no"),
    ("The bell rings and the fight begins.", "no"),
    ("Nobody is coming to save you.", "no"),
]

TRAIN = [
    ("Hey guys, welcome back to the channel.", "yes"),
    ("Before we get started, a quick word from today's sponsor.", "yes"),
    ("This video is brought to you by BetterHelp.", "yes"),
    ("Head over to the link in the description.", "yes"),
    ("Use code RISE at checkout to get 15 percent off.", "yes"),
    ("Download the app for free on the App Store.", "yes"),
    ("If you enjoyed this video, hit the like button.", "yes"),
    ("Make sure you subscribe and turn on notifications.", "yes"),
    ("Follow us on Instagram for daily motivation.", "yes"),
    ("Check out our new merch store.", "yes"),
    ("Thanks so much for watching, see you next time.", "yes"),
    ("Our podcast is available on Spotify every Monday.", "yes"),
    ("Sign up for the free newsletter on our website.", "yes"),
    ("Today's episode is sponsored by Squarespace.", "yes"),
    ("Don't forget to share this video with a friend.", "yes"),
    ("Support the channel on Patreon.", "yes"),
    ("Grab your copy of the book, link below.", "yes"),
    ("The link is in the description.", "yes"),
    ("Use promo code motivation for 20 percent off your first year.", "yes"),
    ("Thanks for watching.", "yes"),
    ("Don't forget to subscribe and hit the notification bell.", "yes"),
    ("Invest in yourself before anything else.", "no"),
    ("Download a new mindset every single morning.", "no"),
    ("Follow your dreams even when nobody believes in you.", "no"),
    ("Like a lion, you have to hunt for what you want.", "no"),
    ("The price of greatness is responsibility.", "no"),
    ("Your struggle is your gift.", "no"),
    ("Wake up at five and go to work on yourself.", "no"),
    ("Money will not make you happy, purpose will.", "no"),
    ("Check your excuses at the door.", "no"),
    ("Share your story with the world, it might save someone.", "no"),
    ("You were born to win.", "no"),
    ("Comment less on other people's lives and build your own.", "no"),
    ("Watch what happens when you refuse to quit.", "no"),
    ("Every video of your life is still being recorded.", "no"),
    ("Stay focused, stay hungry, stay humble.", "no"),
    ("Today is the day you stop making excuses.", "no"),
    ("Discipline is choosing what you want most over what you want now.", "no"),
    ("The easy road never made a strong man.", "no"),
    ("Sit with the silence until it tells you the truth.", "no"),
]


def encode(tokenizer, pairs):
    src = [PROMPT.format(s=text) for text, _ in pairs]
    tgt = [label for _, label in pairs]
    model_inputs = tokenizer(src, max_length=160, truncation=True, padding=True, return_tensors="pt")
    labels = tokenizer(tgt, max_length=4, truncation=True, padding=True, return_tensors="pt").input_ids
    labels[labels == tokenizer.pad_token_id] = -100
    return model_inputs.input_ids, model_inputs.attention_mask, labels


@torch.no_grad()
def accuracy(model, tokenizer, pairs, device):
    yes, no = tokenizer("yes").input_ids[0], tokenizer("no").input_ids[0]
    right = 0
    for text, label in pairs:
        enc = tokenizer(PROMPT.format(s=text), return_tensors="pt").to(device)
        start = torch.full((1, 1), model.config.decoder_start_token_id, device=device)
        logits = model(**enc, decoder_input_ids=start).logits[0, 0]
        pred = "yes" if logits[yes] > logits[no] else "no"
        right += pred == label
    return right, len(pairs)


def main():
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForSeq2SeqLM.from_pretrained(BASE).to(device)
    before = accuracy(model, tokenizer, HOLDOUT, device)
    print(f"before fine-tune: {before[0]}/{before[1]} on held-out English lines", flush=True)

    ids, mask, labels = encode(tokenizer, TRAIN)
    torch.manual_seed(0)
    order = list(range(len(TRAIN)))
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4)
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
    after = accuracy(model, tokenizer, HOLDOUT, device)
    print(f"after fine-tune:  {after[0]}/{after[1]} on held-out English lines", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(OUT)
    tokenizer.save_pretrained(OUT)
    (OUT / "train.json").write_text(json.dumps(
        {"train": TRAIN, "holdout": HOLDOUT, "prompt": PROMPT, "base": BASE}, ensure_ascii=False, indent=2))
    print(f"saved {OUT}", flush=True)


if __name__ == "__main__":
    main()
