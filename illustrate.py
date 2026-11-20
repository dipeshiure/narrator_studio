"""Original black-and-white ink illustrations for script videos (local FLUX.1-schnell).

The look of long-form motivational channels (monochrome manga / gekiga panels with a
slow zoom) without using anyone's anime or manga: every image is generated from our own
prompt with an original recurring character. Existing character, series and artist names
are removed from prompts. FLUX.1-schnell is Apache-2.0 (fine for monetised videos).

Runs mflux in its own venv (tools/mflux_venv) through tools/flux_batch.py, so the model
is loaded once per video. Images are cached by prompt + seed, so a re-run after editing
the script only draws the changed scenes.
"""
import hashlib, json, re, subprocess, urllib.request
from pathlib import Path

import make_video as mv

HERE = Path(__file__).resolve().parent
FLUX_PY = HERE / "tools" / "mflux_venv" / "bin" / "python"
BATCH = HERE / "tools" / "flux_batch.py"
MODEL = "dhairyashil/FLUX.1-schnell-mflux-4bit"
MODEL_DIR = HERE / "tools" / "models" / "flux1-schnell-mflux-4bit"   # tools/fetch_flux.py
# FLUX.2 [klein] 4B (Apache-2.0): the default. 2-4x faster than FLUX.1-schnell here (~10 s an
# image), half the disk, cleaner screentone ink and no stray corner text in tests.
KLEIN_DIR = HERE / "tools" / "models" / "flux2-klein-4b-mflux-4bit"
KLEIN = "Runpod/FLUX.2-klein-4B-mflux-4bit"
CACHE = mv.MEDIA / "ai_illustrations"
SETUP = ("uv venv --python 3.11 tools/mflux_venv && uv pip install --python tools/mflux_venv/bin/python mflux"
         " && venv/bin/python tools/fetch_flux.py")

STYLE = ("single full-bleed illustration filling the entire frame edge to edge, no panels, no borders, no frames, "
         "black and white ink manga illustration, seinen gekiga style, heavy ink hatching, "
         "strong chiaroscuro shadows, high contrast monochrome, cinematic composition, "
         "dramatic lighting, detailed background, clean empty margins, no text, no letters, no writing, "
         "no handwriting, no signature, no page number, no logo, no caption, no speech bubbles, no watermark")
COLOR_STYLE = ("single full-bleed illustration filling the entire frame edge to edge, no panels, no borders, no frames, "
               "full colour manga illustration, rich vivid colours, clean line art, cinematic lighting, "
               "detailed background, clean empty margins, no text, no letters, no writing, "
               "no handwriting, no signature, no page number, no logo, no caption, no speech bubbles, no watermark")
# faces must survive the slow zoom and the captions: whole head in frame, a little room above and below
FRAMING = "the whole head fully visible, subject centred, nothing cropped at the frame edge"


def style_for(s):
    """The genre's own look (config/presets/<genre>.json) for the chosen colour mode, else the ink default."""
    if s.get("art") == "color":
        return s.get("style_color") or COLOR_STYLE
    return s.get("style_mono") or STYLE


HERO = "a stoic young man with short black hair and a long dark coat"
SHOTS = ["wide shot", "medium shot", "low angle shot", "silhouette", "over the shoulder shot", "medium wide shot"]

# names of existing works / characters / artists never go into a prompt
BANNED = re.compile(r"\b(" + "|".join([
    "goku", "vegeta", "naruto", "sasuke", "itachi", "luffy", "zoro", "gojo", "sukuna", "itadori",
    "guts", "griffith", "berserk", "vagabond", "miura", "inoue", "toriyama", "oda", "kishimoto",
    "eren", "levi", "mikasa", "attack on titan", "isayama", "baki", "hanma", "yujiro", "jojo",
    "araki", "dragon ball", "one piece", "bleach", "ichigo", "tanjiro", "demon slayer", "saitama",
    "one punch", "light yagami", "death note", "ghibli", "miyazaki", "batman", "spider-man",
    "superman", "marvel", "disney", "pixar", "anime screenshot",
]) + r")\b", re.I)


def engine(s=None):
    """('flux2', dir, steps) for klein when it is downloaded (and not turned off), else FLUX.1."""
    want = (s or {}).get("image_model", "klein")
    klein, schnell = (KLEIN_DIR / ".complete").exists(), (MODEL_DIR / ".complete").exists()
    if klein and (want != "schnell" or not schnell):     # schnell asked for but not on disk: klein
        return "flux2", KLEIN_DIR, 4
    return "flux1", MODEL_DIR, int((s or {}).get("image_steps", 2))


def model_ready():
    """Weights fully downloaded (fetch_flux.py writes .complete after the last file)."""
    return (KLEIN_DIR / ".complete").exists() or (MODEL_DIR / ".complete").exists()


def available():
    return FLUX_PY.exists() and BATCH.exists() and model_ready()


def trim_bands(png, out):
    """Cut flat black/white bands off the sides (klein likes to draw a narrow panel with bars
    around it). Writes the trimmed image to `out` and returns it, or the original if fine."""
    from PIL import Image
    import numpy as np
    im = Image.open(png).convert("L")
    a = np.asarray(im, np.float32)
    h, w = a.shape

    MIN_RUN = 12                                 # the drawing starts where texture lasts this long

    def inner_edge(texture):
        """First index, walking in from the edge, where a long textured stretch begins (bars and
        margins have no texture; the thin seams between them are too short to count)."""
        n = 0
        for i, on in enumerate(texture):
            n = n + 1 if on else 0
            if n >= MIN_RUN:
                return i - MIN_RUN + 1
        return 0

    def plain(strip, axis=0):
        """What we'd cut: made of flat black or white lines (a white margin then a black bar is
        fine), allowing a few seam lines between them? Lines run along `axis`."""
        if strip.size == 0:
            return True
        # a line is flat when 97 % of it is one black or white tone (thin frame lines crossing a
        # bar are fine); a seam or two between margin and bar is allowed
        dark = (strip < 35).mean(axis=axis) >= 0.97
        light = (strip > 220).mean(axis=axis) >= 0.97
        return (~(dark | light)).sum() <= 4
    # texture = mean absolute difference to the neighbour, measured in the middle of the image
    cols = np.abs(np.diff(a[h // 5: h - h // 5], axis=1)).mean(axis=0) > 2.0
    rows = np.abs(np.diff(a[:, w // 5: w - w // 5], axis=0)).mean(axis=1) > 2.0
    left, right = inner_edge(cols), w - 1 - inner_edge(cols[::-1])
    top, bottom = inner_edge(rows), h - 1 - inner_edge(rows[::-1])
    # a side is cut only when everything outside the drawing there is a flat field (a dark night
    # sky or a black coat at the edge is part of the picture and stays)
    mid = a[top:bottom + 1]
    if not plain(mid[:, :left], axis=0):         # columns of the left strip
        left = 0
    if not plain(mid[:, right + 1:], axis=0):
        right = w - 1
    if not plain(a[:top, left:right + 1], axis=1):   # rows of the top strip
        top = 0
    if not plain(a[bottom + 1:, left:right + 1], axis=1):
        bottom = h - 1
    # a wide flat field on ONE side is negative space in the picture (a dark void beside a
    # face), not a bar: wide cuts only come in pairs (pillar/letterbox), single sides only
    # lose a thin margin
    def limit(a_cut, b_cut, size):
        thin = 0.08 * size
        if a_cut > thin and b_cut > thin:
            return a_cut, b_cut
        return min(a_cut, thin) if a_cut <= thin else 0, min(b_cut, thin) if b_cut <= thin else 0
    lc, rc = limit(left, w - 1 - right, w)
    tc, bc = limit(top, h - 1 - bottom, h)
    left, right, top, bottom = int(lc), int(w - 1 - rc), int(tc), int(h - 1 - bc)
    if (right - left + 1) > 0.95 * w and (bottom - top + 1) > 0.95 * h:
        return png
    if (right - left + 1) < 0.3 * w or (bottom - top + 1) < 0.3 * h:
        return png                               # nearly blank: leave it, don't zoom into a sliver
    Image.open(png).crop((left, top, right + 1, bottom + 1)).save(out)
    return out


def clean(prompt):
    p = BANNED.sub("", str(prompt))
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.])", r"\1", p)).strip(" ,.")


PLAN_BATCH = 6          # scenes per LLM call: a 7B model keeps each paragraph's meaning when the list is short


def _ask_json(prompt, model):
    import prep
    body = json.dumps({"model": model, "stream": False, "keep_alive": 0, "format": "json",
                       "options": {"temperature": 0.4, "num_ctx": 8192},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(prep.OLLAMA, body, {"Content-Type": "application/json"})
    out = json.loads(json.load(urllib.request.urlopen(req, timeout=300))["message"]["content"])
    if isinstance(out, dict):             # {"pictures": [...]} as asked, or {"1": "...", "image2": "..."} as small models do
        out = next((v for v in out.values() if isinstance(v, list)), None) or \
            [v for v in out.values() if isinstance(v, (str, dict))]
    return [clean(x if isinstance(x, str) else next(iter(x.values()), "") if isinstance(x, dict) else str(x)) for x in out]


def llm_prompts(scenes, hero, model, log=print, hint=""):
    """One visual description per scene from the local LLM (None when it fails). Scenes go in small
    batches, each with the paragraph before and after for context, and the picture must SHOW what
    the words say; the image model then spends its attention on that subject, not on a stock symbol."""
    import prep
    try:
        prep.ensure_ollama(log)
    except Exception as e:
        log(f"  ! AI で絵の指示文を作れませんでした({e})。キーワードで代用します")
        return None
    out, missed = [], 0
    for lo in range(0, len(scenes), PLAN_BATCH):
        batch = scenes[lo:lo + PLAN_BATCH]
        before = scenes[lo - 1]["text"][-250:] if lo else ""
        after = scenes[lo + len(batch)]["text"][:250] if lo + len(batch) < len(scenes) else ""
        numbered = "\n".join(f"{i + 1}. {sc['text'][:600]}" for i, sc in enumerate(batch))
        prompt = (
            "You direct the illustrations of a black-and-white motivational video. For each numbered narration "
            "paragraph write ONE picture (20-35 English words) that SHOWS what the words are saying at that moment: "
            "its main idea made concrete: the literal object, place, creature or situation the words mention, or a "
            "person doing, feeling or facing something. Name the subject first, then pose, place, light. "
            + (f"The recurring main character is '{hero}'; use him when a person is needed, otherwise show the thing itself. "
               if hero else "There is no recurring character: show the subject itself. ")
            + (f"Genre guidance: {hint} " if hint else "")
            + "Do not fall back on a stock symbol unless the paragraph is about it. Every picture must be different. "
            "Only what is visible; no names of real people, no existing anime/manga/film characters. Never describe text, numbers, labels, scale bars, "
            "charts, maps with writing, signs or captions: the picture must contain no writing at all."
            + (f"\nThe paragraph before (context only): {before}" if before else "")
            + (f"\nThe paragraph after (context only): {after}" if after else "")
            + f"\nAnswer as JSON: {{\"pictures\": [exactly {len(batch)} strings, one per numbered paragraph, in order]}}.\n\n{numbered}")
        got = None
        for _ in range(2):                                   # one retry per batch
            try:
                got = _ask_json(prompt, model)
                if len(got) == len(batch) and all(got):
                    break
                log(f"  ! 絵の指示文の数が合いません({len(got)}/{len(batch)})。もう一度頼みます")
            except Exception as e:
                log(f"  ! 絵の指示文の作成に失敗({e})")
            got = None
        if got is None:
            missed += len(batch)
            got = [""] * len(batch)                          # keyword fallback for these scenes only
        out += got
    if missed == len(scenes):
        return None
    if missed:
        log(f"  ! {missed} 場面は AI で絵の指示文を作れなかったので、キーワードで代用します")
    return out


def keyword_prompt(sc, hero):
    import story
    q = sc.get("query") or story.keyword_query(sc["text"], ["lonely road at night"])
    return f"{hero}, {q}, moody atmosphere" if hero else f"{q}, atmospheric"


SHOT_SCHEMA = {   # Ollama structured output: the reply is forced into this JSON shape
    "type": "object",
    "properties": {"shots": {"type": "array", "items": {"type": "object", "properties": {
        "prompt": {"type": "string"}, "sentence": {"type": "integer"}}, "required": ["prompt", "sentence"]}}},
    "required": ["shots"]}
MIN_SHOT = 1.5          # a picture stays at least this long (s); Shorts need room for ~5-6s caps


def _sentences(text):
    parts = [x.strip() for x in re.findall(r"[^.!?…]+(?:[.!?…]+[\"')\]]*)?\s*", text) if x.strip()]
    return parts or [text.strip()]


def sentence_starts(sc):
    """Start time (s from the scene start) of every sentence, from the narration's word timings."""
    sents, words = _sentences(sc["text"]), sc.get("words") or []
    counts = [len(x.split()) for x in sents]
    starts, k = [], 0
    for c in counts:
        starts.append(round(float(words[k]["s"]), 2) if k < len(words) and isinstance(words[k], dict) and "s" in words[k] else None)
        k += c
    if starts and starts[0] is None:
        starts[0] = 0.0
    if any(x is None for x in starts):                      # no usable timings: spread by word count
        total, acc, out = max(1, sum(counts)), 0, []
        for c in counts:
            out.append(round(sc["dur"] * acc / total, 2)); acc += c
        return out
    starts[0] = 0.0
    return starts


def plan_shots(scenes, s, log=print, counts=None):
    """For every scene: n pictures, each with an English prompt and the moment it appears (seconds from the
    scene start), asked from the local LLM with a JSON schema so the answer is always valid JSON. Pictures
    change on the sentence that talks about them, not on a fixed grid. Sets sc['shots'] =
    [{prompt, start, end}] and returns the whole plan; scenes the LLM fails on keep one picture per scene
    prompt (the old behaviour)."""
    import prep
    hero = "" if s.get("hero") == "none" else clean(s.get("hero") or HERO)
    hint = s.get("planner_hint", "")
    try:
        prep.ensure_ollama(log)
    except Exception as e:
        log(f"  ! AI で絵の指示文を作れませんでした({e})。場面ごとに 1 つの指示文で作ります")
        return None
    plan = []
    for i, sc in enumerate(scenes):
        n = (counts or {}).get(i, 1)
        sents = _sentences(sc["text"])
        starts = sentence_starts(sc)
        n = max(1, min(n, len(sents)))                       # never more pictures than sentences
        if n == 1:
            continue                                          # one picture: the scene prompt does it
        numbered = "\n".join(f"{k + 1}. {x}" for k, x in enumerate(sents))
        ask = ("You direct the pictures of a narrated video. The narration of one scene is below, one numbered sentence per line. "
               f"Choose exactly {n} pictures. For each give an English prompt (15-35 words, subject first, then place and light) that SHOWS what is "
               "being said at that moment, and the number of the sentence on which the picture should appear. The first picture appears on "
               "sentence 1; the sentence numbers must increase. "
               + (f"The recurring main character is '{hero}'; use him when a person is needed. " if hero else "There is no recurring character. ")
               + (f"Guidance: {hint} " if hint else "")
               + "Every picture is different; no names of real people, no existing characters; the picture must contain no writing, numbers, "
               "labels or scale bars.\n\nNarration:\n" + numbered)
        shots = None
        for _ in range(2):
            try:
                body = json.dumps({"model": s["llm_model"], "stream": False, "keep_alive": "5m", "format": SHOT_SCHEMA,
                                   "options": {"temperature": 0.4, "num_ctx": 8192},
                                   "messages": [{"role": "user", "content": ask}]}).encode()
                req = urllib.request.Request(prep.OLLAMA, body, {"Content-Type": "application/json"})
                raw = json.loads(json.loads(urllib.request.urlopen(req, timeout=300).read())["message"]["content"])["shots"]
                cand = [(max(1, min(len(sents), int(x["sentence"]))), clean(x["prompt"])) for x in raw if str(x.get("prompt", "")).strip()]
                cand.sort(key=lambda x: x[0])
                if len(cand) >= 2:
                    shots = cand[:n]
                    break
            except Exception as e:
                log(f"  ! 場面 {i + 1} の絵の計画に失敗({e})")
        if not shots:
            continue
        # timestamps: a picture starts when its sentence is spoken; short ones are dropped
        keep, last = [], -9.0
        for k, (sn, pr) in enumerate(shots):
            t0 = 0.0 if not keep else starts[sn - 1]
            if keep and (t0 - last < MIN_SHOT or sc["dur"] - t0 < MIN_SHOT):
                continue
            keep.append({"prompt": pr, "start": round(t0, 2)}); last = t0
        for k, sh in enumerate(keep):
            sh["end"] = round(keep[k + 1]["start"] if k + 1 < len(keep) else sc["dur"], 2)
        if len(keep) >= 2:
            sc["shots"] = keep
            plan.append({"scene": i + 1, "text": sc["text"][:120], "shots": keep})
    try:
        mv.unload_llm()                                       # give the memory back before the picture model starts
    except Exception:
        pass
    return plan


# One Ollama call sees the whole script, then writes one English image prompt per spoken beat.
PROMPT_SYSTEM = (
    "You illustrate one narrated video. Read the WHOLE script before you write any picture, "
    "so a word means what it means in this story (a bell on a ship is the ship's bell, not a school bell). "
    "Force visual progression: every picture moves the story forward. "
    "Each picture shows a new action, a new object, or a new moment. "
    "Do not repeat the previous pose, object, or composition. "
    "A picture stays on screen for the spoken length of the lines it covers, so show those lines, not one symbol for the whole paragraph. "
    "If a list of subjects is given, use a different subject each time. Do not put every picture in the same room. "
    "Art style: {art_style}\n"
    "Character: {character_setting}\n"
    "Target image model: {target_ai}\n"
    "Keep one world and the same character design, but change what the camera sees every time. "
    "When a person is needed, use the character. No real people, no existing characters or franchises, no writing in the picture."
)


def _fill(template, **kw):
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def timeline_inputs(s):
    """Art style, character and target model for the meta-prompt, from the video's own settings."""
    art = str(s.get("art_style") or "").strip()
    if not art:
        art = str(s.get("style_color") if s.get("art") == "color" else s.get("style_mono") or "").strip()
    if not art:
        art = style_for(s)
    hint = str(s.get("planner_hint") or "").strip()
    if hint:
        art = f"{art} Subjects to show: {hint}"
    character = s.get("character_setting")
    if character is None:
        character = "" if s.get("hero") == "none" else str(s.get("hero") or HERO)
    character = str(character).strip() or "No recurring character. Show the subject itself."
    family, _, _ = engine(s)
    target = str(s.get("target_ai") or "").strip() or ("FLUX.2 klein 4B" if family == "flux2" else "FLUX.1-schnell")
    return art, character, target


def _plain(text):
    text = str(text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def parse_timeline(raw):
    """Ollama's reply -> [{script_segment, image_prompt}, ...] with those keys only."""
    data = json.loads(_plain(raw)) if isinstance(raw, str) else raw
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        if lists:
            data = lists[0]
        elif data and all(isinstance(v, dict) for v in data.values()):
            data = list(data.values())
        else:
            data = [data]
    out = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        seg = str(item.get("script_segment") or "").strip()
        prompt = clean(item.get("image_prompt") or "")
        if seg and prompt:
            out.append({"script_segment": seg, "image_prompt": prompt})
    return out


def timeline_schema(n):
    """Force one row per paragraph. n is the paragraph number, so a short reply cannot shift the rest."""
    row = {"type": "object", "properties": {"n": {"type": "integer"}, "image_prompt": {"type": "string"}},
           "required": ["n", "image_prompt"]}
    return {"type": "object", "properties": {"timeline": {"type": "array", "minItems": n, "maxItems": n, "items": row}},
            "required": ["timeline"]}


def _beat_groups(sc, s, t_video):
    """Group a scene's sentences into pictures. Each picture's length is the spoken length of its lines.
    A new picture starts once the current one has covered about shot_seconds of speech, and only on a
    sentence boundary, so one sentence is never split into copies of the same picture."""
    text = str(sc.get("text") or "").strip()
    dur = float(sc.get("dur") or 0)
    sents = _sentences(text) if text else []
    if dur <= 0 or len(sents) <= 1:
        return [{"text": text, "start": 0.0, "end": dur}]
    starts = sentence_starts(sc)
    spans = []
    for i, sent in enumerate(sents):
        a = float(starts[i])
        b = float(starts[i + 1] if i + 1 < len(starts) else dur)
        spans.append({"text": sent, "start": a, "end": max(a, b)})
    target = max(MIN_SHOT, shot_seconds(t_video + dur / 2.0, s))
    groups, cur = [], None
    for sp in spans:
        if cur is None:
            cur = dict(sp)
            continue
        held = cur["end"] - cur["start"]
        would = sp["end"] - cur["start"]
        # Cut before overflowing the target so Shorts stay near shot_seconds
        # (old logic waited until held >= target, which glued two lines into ~2x holds).
        if held >= target or (would > target and held >= MIN_SHOT):
            groups.append(cur)
            cur = dict(sp)
        else:
            cur["text"] = (cur["text"] + " " + sp["text"]).strip()
            cur["end"] = sp["end"]
    if cur:
        if groups and cur["end"] - cur["start"] < MIN_SHOT:
            groups[-1]["text"] = (groups[-1]["text"] + " " + cur["text"]).strip()
            groups[-1]["end"] = cur["end"]
        else:
            groups.append(cur)
    return groups or [{"text": text, "start": 0.0, "end": dur}]


def narration_beats(scenes, s):
    """One beat per picture. start/end are seconds from the scene start, and end-start is how long those words are spoken."""
    beats, t = [], 0.0
    for i, sc in enumerate(scenes):
        for g in _beat_groups(sc, s, t):
            if g["text"]:
                beats.append({"scene": i, "text": g["text"], "start": round(float(g["start"]), 2),
                               "end": round(float(g["end"]), 2)})
        t += float(sc.get("dur") or 0) + 0.35
    return beats


def _timeline_user(beats):
    """The whole script, numbered, in one message. Every retry sends this same text."""
    lines = []
    for n, beat in enumerate(beats, 1):
        start, end = beat.get("start"), beat.get("end")
        if start is not None and end is not None and float(end) > float(start):
            lines.append(f"{n}. ({float(start):.1f}-{float(end):.1f}s) {beat['text']}")
        else:
            lines.append(f"{n}. {beat['text']}")
    numbered = "\n\n".join(lines)
    return (f"Read every line before you write. This is one story. "
            f"Write exactly {len(beats)} rows, one per numbered beat, in this order. Do not merge beats. "
            "n is that beat's number. The seconds are how long those words are spoken: the picture stays up for that long. "
            "Force visual progression. Each image_prompt must show a new moment, and the next row must not repeat it. "
            "image_prompt is 15-35 English words of what a camera sees: subject, place, light. "
            "No sound, no dialogue, no writing in the picture.\n\n" + numbered)


def _read_prompts(raw):
    """Schema reply, or a bare array, -> ({paragraph number: prompt}, prompts with no number)."""
    data = json.loads(_plain(raw)) if isinstance(raw, str) else raw
    if isinstance(data, dict):
        arr = next((v for v in data.values() if isinstance(v, list)), None)
        data = arr if arr is not None else [data]
    found, loose = {}, []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        prompt = clean(item.get("image_prompt") or "")
        if not prompt:
            continue
        try:
            num = int(item.get("n"))
        except (TypeError, ValueError):
            num = None
        if num and num not in found:
            found[num] = prompt
        else:
            loose.append(prompt)
    return found, loose


def _ask_all(system, beats, model):
    """One structured call. The user message is the entire script."""
    import story
    numbers = list(range(1, len(beats) + 1))
    raw = story._ollama(_timeline_user(beats), model, system=system, temperature=0.2,
                        keep_alive="5m", timeout=600, schema=timeline_schema(len(beats)))
    found, loose = _read_prompts(raw)
    for n, prompt in zip([n for n in numbers if n not in found], loose):
        found[n] = prompt
    return {n: found[n] for n in numbers if n in found}


def video_timeline(script, art_style, character_setting, target_ai, model, log=print, beats=None):
    """One Ollama call reads the whole script and returns one image prompt per spoken beat.

    Returns {"system_prompt", "art_style", "character_setting", "target_ai", "timeline"}.
    Each timeline item has script_segment (the words that picture covers) and image_prompt.
    When the beat has spoken times, start and end are included so the picture lasts that long.
    The call uses a JSON schema and num_ctx=8192. A short reply is asked again with the same
    full script, never with one paragraph alone."""
    import prep
    if beats is None:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", str(script).strip()) if p.strip()]
        beats = [{"scene": i, "text": p, "start": None, "end": None} for i, p in enumerate(paragraphs)]
    else:
        beats = [b for b in beats if str(b.get("text") or "").strip()]
    if not beats:
        raise RuntimeError("台本が空です")
    if not prep.ensure_ollama(log):
        raise RuntimeError("Ollama が起動していません")
    system = _fill(PROMPT_SYSTEM, art_style=art_style, character_setting=character_setting, target_ai=target_ai)
    log(f"  画像タイムラインを作っています: {len(beats)} 場面")
    prompts = _ask_all(system, beats, model)
    if len(prompts) != len(beats):
        log("  画像タイムラインを、台本全文でもう一度作っています")
        again = _ask_all(system, beats, model)
        if len(again) == len(beats):
            prompts = again
        else:
            for n, prompt in again.items():
                prompts.setdefault(n, prompt)
    missing = [n for n in range(1, len(beats) + 1) if n not in prompts]
    if missing:
        raise RuntimeError(f"タイムラインが {len(beats) - len(missing)}/{len(beats)} 場面で止まりました")
    items = []
    for n, beat in enumerate(beats, 1):
        row = {"script_segment": beat["text"], "image_prompt": prompts[n]}
        start, end = beat.get("start"), beat.get("end")
        if start is not None and end is not None and float(end) > float(start):
            row["start"] = round(float(start), 2)
            row["end"] = round(float(end), 2)
        items.append(row)
    log(f"  画像タイムライン: {len(items)} 場面（朗読の秒数に合わせ、同じ絵は繰り返しません）")
    try:
        mv.unload_llm()
    except Exception:
        pass
    return {"system_prompt": system, "art_style": art_style, "character_setting": character_setting,
            "target_ai": target_ai, "timeline": items}


def plan(scenes, s, log=print):
    """Fill sc['image_prompt'] for every scene.

    AI pictures use one Ollama call over the whole script. The reply is saved as timeline.json
    and those image_prompt strings are what get drawn. A scene that already has image_prompt
    keeps it. If Ollama fails, the older batch prompts are used, then keywords."""
    hero = "" if s.get("hero") == "none" else clean(s.get("hero") or HERO)      # "none": no recurring character
    need = [i for i, sc in enumerate(scenes) if not sc.get("image_prompt")]
    filled = {}
    if need:
        art, character, target = timeline_inputs(s)
        subset = [scenes[i] for i in need]
        beats = narration_beats(subset, s)
        script = "\n\n".join(sc["text"] for sc in subset)
        try:
            made = video_timeline(script, art, character, target, s["llm_model"], log, beats=beats)
            s["_from_timeline"] = True
            if s.get("_work"):
                mv.write_json(Path(s["_work"]) / "timeline.json", made)
            items = made["timeline"]
            if len(items) != len(beats):
                log(f"  ! タイムラインの場面数があいません({len(items)}/{len(beats)})。合う分だけ使い、残りはキーワードで補います")
            groups = {}
            for beat, item in zip(beats, items):
                groups.setdefault(beat["scene"], []).append((beat, item))
            for local_i, rows in groups.items():
                i = need[local_i]
                filled[i] = rows[0][1]["image_prompt"]
                shots = []
                for beat, item in rows:
                    start, end = beat.get("start"), beat.get("end")
                    if start is not None and end is not None and float(end) > float(start):
                        shots.append({"prompt": item["image_prompt"], "start": round(float(start), 2),
                                       "end": round(float(end), 2)})
                if shots:
                    scenes[i]["shots"] = shots
        except Exception as e:
            log(f"  ! タイムラインを作れませんでした({e})。これまでの方法で絵の指示文を作ります")
            got = llm_prompts([scenes[i] for i in need], hero, s["llm_model"], log, s.get("planner_hint", ""))
            if got:
                for j, i in enumerate(need):
                    if got[j]:
                        filled[i] = got[j]
    for i, sc in enumerate(scenes):
        sc["image_prompt"] = clean(sc.get("image_prompt") or filled.get(i) or keyword_prompt(sc, hero))


def size_for(s):
    """FLUX works in multiples of 16. On the 24 GB M5: 960x544 at 2 steps ~22 s an image,
    1024x576 ~44 s, 1280x720 ~105 s (bigger frames push the model into swap). The ink look
    is the same once zoomed, grained and graded."""
    return (544, 960) if s["aspect"] == "9:16" else (960, 544)


def picture_count(sc, s, fallback):
    """How many pictures to draw. Each timeline shot is one picture, held for its spoken seconds.
    A timeline scene with no shot list stays one picture for the whole narration, never copies."""
    planned = sc.get("shots")
    if planned:
        return len(planned)
    if s.get("_from_timeline"):
        return 1
    return fallback


def shot_seconds(t, s):
    """How long one picture stays on screen at narration time t. The opening needs attention,
    so it cuts fast (s['hook_seconds'] per picture for the first s['hook_until'] s), then the
    cuts relax linearly to s['image_seconds'] over the next s['hook_ramp'] s."""
    slow = float(s.get("image_seconds", 9.0))
    fast = min(slow, float(s.get("hook_seconds", 3.0)))
    until, ramp = float(s.get("hook_until", 45.0)), max(1.0, float(s.get("hook_ramp", 120.0)))
    if t <= until:
        return fast
    return fast + (slow - fast) * min(1.0, (t - until) / ramp)


def _emit_ready(order, seen, done, on_image):
    """Tell the caller about images that now exist, in job order. `seen` is how many were already reported."""
    while seen < done and seen < len(order):
        item = order[seen]
        seen += 1
        path = Path(item["path"])
        if on_image and path.exists():
            on_image({**item, "path": path})
    return seen


def illustrate(scenes, s, log=print, on_image=None):
    """Draw every scene's images. Returns {scene index: [png, ...]} (missing on failure).
    One image per ~s['image_seconds'] of narration, each a different camera shot.
    on_image(item) runs as soon as that png exists (cached ones first), so the video encode
    can start while later pictures are still being drawn. It must return quickly."""
    plan(scenes, s, log)
    w, h = size_for(s)
    family, _, _ = engine(s)
    name = KLEIN if family == "flux2" else MODEL     # cache per model: never reuse the other's images
    images, per_scene, order = [], {}, []
    t0 = 0.0
    counts = {}
    t1 = 0.0
    for i, sc in enumerate(scenes):
        counts[i] = max(1, round(sc["dur"] / shot_seconds(t1 + sc["dur"] / 2, s)))   # faster cuts near the start
        t1 += sc["dur"] + 0.35
    # The timeline already chose each scene's picture. Extra shot prompts would replace it.
    sc_plan = None if s.get("_from_timeline") else (plan_shots(scenes, s, log, counts) if s.get("shot_plan", True) else None)
    if sc_plan:
        mv.write_json(Path(s["_work"]) / "shots.json" if s.get("_work") else mv.TMP / "shots.json", sc_plan)
    for i, sc in enumerate(scenes):
        planned = sc.get("shots")
        n = picture_count(sc, s, counts[i])
        for k in range(n):
            shot = SHOTS[(i + k) % len(SHOTS)] if n > 1 or i % 2 else SHOTS[0]
            base = planned[k]["prompt"] if planned else sc["image_prompt"]
            prompt = f"{base}, {shot}, {FRAMING}, {style_for(s)}" if s.get("hero") != "none" else f"{base}, {shot}, {style_for(s)}"
            seed = int(hashlib.md5(f"{prompt}|{k}".encode()).hexdigest()[:7], 16)
            key = hashlib.md5(f"{name}|{w}x{h}|{prompt}|{seed}".encode()).hexdigest()[:16]
            out = CACHE / f"{key}.png"
            images.append({"prompt": prompt, "out": str(out), "seed": seed})
            per_scene.setdefault(i, []).append(out)
            order.append({"scene": i, "shot": k, "shots": n, "path": out})
    CACHE.mkdir(parents=True, exist_ok=True)
    job = CACHE / f"job_{hashlib.md5(json.dumps(images).encode()).hexdigest()[:10]}.json"
    _, model_dir, steps = engine(s)
    mv.write_json(job, {"family": family, "model_path": str(model_dir), "width": w, "height": h, "steps": steps,
                        "images": images})
    log(f"  イラストを生成中: {len(images)} 枚({'FLUX.2 klein 4B' if family == 'flux2' else 'FLUX.1-schnell'}, 作成済みは再利用)")
    proc = subprocess.Popen([str(FLUX_PY), str(BATCH), str(job)], cwd=HERE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail, shown, seen = [], -1, 0
    for line in proc.stdout:
        if line.startswith("IMG "):
            done, total = map(int, line.split()[1:3])
            seen = _emit_ready(order, seen, done, on_image)
            step = max(1, total // 10)
            if done // step > shown or done == total:
                shown = done // step
                log(f"  イラスト {done}/{total}")
        elif line.startswith("MEMWAIT"):
            log("  メモリの空きが少ないので一時停止しています(スワップを防ぐため)。ほかのアプリを閉じると再開します")
        elif line.startswith("GPUWAIT"):
            log("  ほかの重い処理(声または絵)が終わるのを待っています(同時には動かしません)")
        elif line.startswith("MEMOK"):
            log("  メモリが空いたので再開しました")
        elif line.strip():
            tail = (tail + [line.strip()])[-5:]
    proc.wait()
    _emit_ready(order, seen, len(order), on_image)
    job.unlink(missing_ok=True)
    ok = {i: [p for p in ps if p.exists()] for i, ps in per_scene.items()}
    missing = sum(len(ps) for ps in per_scene.values()) - sum(len(ps) for ps in ok.values())
    if missing:
        log(f"  ! イラスト {missing} 枚を作れませんでした({' / '.join(tail[-2:])})。その場面は素材映像を使います")
    return {i: ps for i, ps in ok.items() if ps}
