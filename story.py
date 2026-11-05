#!/usr/bin/env python3.11
"""Script -> narrated long-form video (BHATT-style calm narration; preset "bhatt").

A script (JSON or plain text) is read as a list of scenes. Each scene is voiced with
TTS, gets stock footage matched to its own words (or an animated chart), and the
whole thing is assembled with burned-in subtitles, section headings and BGM.
The motivation pipeline (make_video.py) is untouched; its helpers are reused.

Usage:
  venv/bin/python story.py scripts/costco.json                 # one script
  venv/bin/python story.py scripts/                            # every .json/.txt in folder
Options:
  --preset NAME   presets/NAME.json on top of settings.json (default: bhatt)
  --out DIR       output folder (default: ./videos_story)
  --offline       only use already-downloaded clips

JSON script:
  {"title": "...", "description": "...", "thumbnail": "SHORT TEXT", "sources": ["url", ...],
   "voice": "en-US-GuyNeural",                       # optional, overrides the preset
   "scenes": [
     {"heading": "What Happened", "text": "...", "query": "costco warehouse"},
     {"text": "...", "chart": {"type": "line", "title": "Membership fees ($B)",
                               "labels": ["2020", "2021"], "values": [3.5, 3.9], "unit": "$B"}}]}
     {"text": "...", "photo": "Blockbuster store"},   # real photos from Wikimedia Commons
  heading / query / chart / photo are optional. Without a query the local LLM picks one.
  "photo" uses freely licensed Commons photos (slow zoom) and credits them on screen and
  in the description; if none are found the scene falls back to stock footage.
Plain text script: first line = title, blank-line separated paragraphs = scenes,
a line "## Heading" starts a new section.
Inline tags (plain text, or inside a JSON scene's "text") set the same fields and are
removed from the narration; they apply to the paragraph they are written in:
  [[photo:Keyence headquarters Osaka]]   ([[entity:...]] is an alias)
  [[query:factory sensor line]]
  [[chart:bar|Operating margin (%)|Keyence=55,Toyota=10|%|FY2023 annual reports]]
"""
import argparse, asyncio, json, math, random, re, subprocess, sys, time
from pathlib import Path

import commons
import make_video as mv

HERE = Path(__file__).resolve().parent
PRESETS = HERE / "config" / "presets"
OUTPUT = mv.OUTPUT                    # same finished-video folder as the app (output/videos)
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
FPS = 30
GAP = 0.35            # silence between scenes (s)

STORY_DEFAULTS = {
    "tts_engine": "cosy",            # cosy (CosyVoice 2 on this Mac) / edge (free neural voices) / say
    "voice_id": "atlas",             # cosy: the narrator (assets/voices or media/voices)
    "voice": "en-US-GuyNeural",
    "tts_rate": "+0%",
    "tts_speed": 1.0,                # speaking speed as a multiplier (1.0 = normal): edge rate and CosyVoice tempo
    "tts_pitch": "+0Hz",             # lower = deeper voice, e.g. "-6Hz"
    "visuals": "stock",              # stock (Pexels) / ai (ink illustrations, illustrate.py) / folder (your own media)
    "media_folder": "",              # folder: your clips / photos, matched to each paragraph by meaning (media_match.py)
    "image_seconds": 9.0,            # ai: one illustration per this many seconds of narration (later in the video)
    "hook_seconds": 3.0,             # ai: ...but this often at the start (attention)
    "hook_until": 45.0,              # ai: fast cuts last this many seconds
    "hook_ramp": 120.0,              # ai: then relax to image_seconds over this many seconds
    "image_steps": 2,                # ai: FLUX.1-schnell steps (2 looks the same as 4 here, 1.5x faster)
    "image_model": "klein",          # ai: klein (FLUX.2 klein 4B, default) / schnell (FLUX.1-schnell)
    "hero": "",                      # ai: recurring main character ("" = illustrate.HERO)
    "grain": False,                  # film grain on every shot
    "show_headings": True,           # section headings ("## ...") on screen
    "heading_seconds": 3.5,          # how long a section heading stays on screen
    "chart_theme": {"bg": "0E1116", "fg": "FFFFFF", "accent": "FFC800", "grid": "2A2F38"},
}


# ---------------------------------------------------------------- script

def slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "story"


TAG = re.compile(r"\[\[(\w+):([^\]]*)\]\]")


def parse_chart_tag(body, where):
    """'bar|Operating margin (%)|Keyence=55,Toyota=10|%|FY2023 filings' -> chart dict."""
    parts = [p.strip() for p in body.split("|")]
    if len(parts) < 3 or parts[0] not in ("line", "bar"):
        raise ValueError(f"{where}: chart タグは [[chart:line|タイトル|ラベル=値,...|単位|出典]] の形式です")
    labels, values = [], []
    for pair in filter(None, (x.strip() for x in parts[2].split(","))):
        lab, sep, val = pair.rpartition("=")
        try:
            values.append(float(val.replace("_", "")))
        except ValueError:
            sep = ""
        if not sep or not lab.strip():
            raise ValueError(f"{where}: chart の値「{pair}」は ラベル=数値 にしてください")
        labels.append(lab.strip())
    chart = {"type": parts[0], "title": parts[1], "labels": labels, "values": values}
    if len(parts) > 3 and parts[3]:
        chart["unit"] = parts[3]
    if len(parts) > 4 and parts[4]:
        chart["source"] = parts[4]
    return chart


def parse_tags(text, where="script"):
    """Strip [[photo:...]] / [[entity:...]] / [[query:...]] / [[chart:...]] from narration.
    Tags belong to the scene (paragraph) they are written in, wherever they sit in it,
    so a tag after the final period is not pushed onto the next scene."""
    tags = {}
    for m in TAG.finditer(text):
        kind, body = m.group(1).lower(), m.group(2).strip()
        key = {"entity": "photo"}.get(kind, kind)
        if key not in ("photo", "query", "chart", "media"):
            raise ValueError(f"{where}: 不明なタグ [[{kind}:...]] (photo / entity / query / chart / media)")
        if key in tags:
            raise ValueError(f"{where}: 1 つのシーンに {key} タグが 2 つあります。段落を分けてください")
        if not body:
            raise ValueError(f"{where}: [[{kind}:]] の中身が空です")
        tags[key] = parse_chart_tag(body, where) if key == "chart" else body
    clean = re.sub(r"\s{2,}", " ", TAG.sub("", text)).strip()
    clean = re.sub(r"\s+([.,!?;:])", r"\1", clean)
    return clean, tags


SENTENCES_PER_SCENE = 3.5     # a scene (one voice take + its pictures) is 3-4 whole sentences


def sentences_of(text):
    """Split narration into sentences; a sentence is never cut in the middle."""
    parts = [x.strip() for x in re.findall(r"[^.!?…]+(?:[.!?…]+[\"')\]]*)?\s*", text) if x.strip()]
    return parts or [text.strip()]


def group_scenes(scenes):
    """Scenes of 3-4 sentences. Long paragraphs are cut BETWEEN sentences; short consecutive paragraphs
    are joined up to 4 sentences. A chapter heading, an inline tag ([[query:..]], photo, chart, media ...) or
    a chart always starts a new scene and keeps its own text together with the tag."""
    out, run = [], []                                   # run: sentences waiting to be grouped

    def flush():
        nonlocal run
        if run:
            n = max(1, round(len(run) / SENTENCES_PER_SCENE))
            n = min(n, len(run))
            size, extra = divmod(len(run), n)             # sizes differ by at most one sentence
            i = 0
            for k in range(n):
                take = size + (1 if k < extra else 0)
                out.append({"text": " ".join(run[i:i + take])})
                i += take
            run = []

    for sc in scenes:
        text = sc.get("text", "").strip()
        if not text:
            continue
        tagged = any(k in sc for k in ("query", "photo", "entity", "chart", "media", "image_prompt"))
        if sc.get("chart"):
            flush(); out.append({**sc, "text": text}); continue
        if sc.get("heading") or tagged:
            flush()
            sents = sentences_of(text)
            first = {k: v for k, v in sc.items() if k != "text"}
            n = max(1, round(len(sents) / SENTENCES_PER_SCENE)) if len(sents) > 4 else 1
            size, extra = divmod(len(sents), n)
            i = 0
            for k in range(n):                              # tags and heading stay on the first part
                take = size + (1 if k < extra else 0)
                part = " ".join(sents[i:i + take]); i += take
                out.append({**first, "text": part} if k == 0 else {"text": part})
            continue
        run += sentences_of(text)
    flush()
    return out


def load_script(path):
    path = Path(path)
    if path.suffix == ".json":
        data = json.loads(path.read_text())
    else:
        lines = path.read_text().splitlines()
        data = {"title": lines[0].strip(), "scenes": []}
        heading, para = None, []

        def flush():
            nonlocal heading, para
            if para:
                data["scenes"].append({"text": " ".join(para), **({"heading": heading} if heading else {})})
                heading, para = None, []
        for line in lines[1:] + [""]:
            line = line.strip()
            if line.startswith("## "):
                flush(); heading = line[3:].strip()
            elif not line:
                flush()
            else:
                para.append(line)
    for sc in data.get("scenes", []):
        sc["text"], tags = parse_tags(sc.get("text", ""), path.name)
        for k, v in tags.items():
            sc.setdefault(k, v)                       # explicit JSON fields win over inline tags
    scenes = group_scenes(data.get("scenes", []))
    if not data.get("title") or not scenes:
        raise ValueError(f"{path.name}: title と scenes(text) が必要です")
    data["scenes"] = scenes
    return data


# ---------------------------------------------------------------- AI draft (local LLM)

STYLES = {
    "bhatt": ("a deep, calm, sermon-like long-form motivational narration in the second person "
              "(\"you\"). Stay strictly on the video's theme; the tone is heavy and quiet: short sentences, "
              "rhetorical questions, occasional one-line truths. Motifs you may draw on when they fit the "
              "theme: solitude, silence, patience, faith in God, discipline, purpose, pain as a teacher. "
              "Stories of an unnamed man or an old teacher are welcome. No hype words like 'smash that like button'."),
    "motivation": ("an intense motivational speech in the second person (\"you\"): discipline, "
                   "hard work, failure, getting back up, chasing a dream. Short punchy sentences, "
                   "rising energy, a powerful ending."),
}


# Retention structure for the spoken script (adapted from the "youtube-script-writer" skill by mohitagw15856,
# pm-claude-skills): packaging -> cold open -> stakes -> chapters with a payoff each -> climax -> no goodbye.
RETENTION = {
    "first": ("This is the opening. Start with a hook in the first two sentences that delivers on the title's "
              "promise at once: a bold claim, a short story setup, or a question the viewer needs answered. No "
              "greeting, no 'welcome', no introduction of yourself. Then, within about 30 seconds of narration, "
              "state the stakes: why this matters, what it costs to ignore it, and what the viewer will have by the end."),
    "middle": ("Give this chapter one clear idea and end it with a small payoff (a truth the viewer can keep). "
               "Open it with a short re-hook: a question, a contrast or a surprising line, never a recap. "
               "Vary the rhythm: a short punchy sentence after every long one."),
    "last": ("This is the climax: deliver the biggest truth the opening promised. Do not say 'in conclusion', "
             "'to sum up', 'thank you for watching' or any goodbye. End on a strong closing line that points "
             "the viewer forward to what to do next."),
    "all": "Conversational spoken English: short sentences, natural pauses, no academic words, no filler openers.",
}


def _ollama(prompt, model, as_json=False, timeout=600):
    import prep, urllib.request
    body = {"model": model, "stream": False, "keep_alive": 0, "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.8, "num_ctx": 8192}}
    if as_json:
        body["format"] = "json"
    req = urllib.request.Request(prep.OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))["message"]["content"]


def _paragraphs(text):
    """LLM chapter text -> narration paragraphs (2-3 sentences each, no markdown)."""
    text = re.sub(r"(?m)^[ \t]*(#+|\*\*|[-*]\s|\d+\.\s)", "", text).replace("**", "")
    text = re.sub(r"\[[^\]]*\]|\([^)]*(pause|music|sfx)[^)]*\)", "", text, flags=re.I)
    out = []
    for block in re.split(r"\n\s*\n", text):
        sents = re.findall(r"[^.!?]+[.!?]+[\"')]*", " ".join(block.split())) or [block.strip()]
        for i in range(0, len(sents), 3):
            p = " ".join(x.strip() for x in sents[i:i + 3]).strip()
            if len(p.split()) >= 3:
                out.append(p)
    return out


def _dedupe(paras, seen):
    """Drop sentences already narrated (continuations often repeat the last lines)."""
    out = []
    for p in paras:
        keep = []
        for sent in re.findall(r"[^.!?]+[.!?]+[\"')]*", p) or [p]:
            key = re.sub(r"[^a-z0-9]", "", sent.lower())
            if key and key not in seen:
                seen.add(key); keep.append(sent.strip())
        if len(" ".join(keep).split()) >= 3:
            out.append(" ".join(keep))
    return out


def draft_script(theme, minutes=10, style="bhatt", s=None, log=print):
    """Theme -> plain-text script (title line, '## chapter' headings, paragraphs) by the
    local LLM (Ollama). Written chapter by chapter so long videos reach their length
    (~140 spoken words per minute). The user edits it before rendering."""
    import prep
    s = s or mv.load_settings()
    model = s["llm_model"]
    prep.ensure_ollama(log)
    minutes = max(1, min(60, int(minutes)))
    n = max(1, min(10, round(minutes / 5)))
    words = int(minutes * 140 / n)
    voice = STYLES.get(style, STYLES["bhatt"])
    log(f"台本の構成を作っています({minutes} 分 / {n} 章)")
    outline = json.loads(_ollama(
        f"Plan a YouTube video script: {voice}\nTheme: {theme}\n"
        "Structure: a hook that promises a result, the stakes, then chapters that each build one idea toward a "
        "final payoff. Make the title under 60 characters, specific, and curiosity-driven. "
        f"Return JSON: {{\"title\": catchy title under 70 characters, \"thumbnail\": 2-4 word thumbnail "
        f"text, \"description\": 2 sentence YouTube description, \"chapters\": [exactly {n} short chapter "
        "headings]}. English only.", model, as_json=True))
    def head_text(h):          # the LLM sometimes returns {"heading": ...} objects
        if isinstance(h, dict):
            h = next((v for k, v in h.items() if k in ("heading", "title", "name")), None) or \
                next((v for v in h.values() if isinstance(v, str)), "")
        return " ".join(str(h).split()).strip(" \"'")
    heads = [x for x in map(head_text, outline.get("chapters") or []) if x][:n] or [theme]
    title = str(outline.get("title") or theme).strip().strip('"')
    lines, story_so_far, seen = [title, ""], "", set()
    rules = ("Plain spoken text, paragraphs separated by blank lines, no headings, no lists, no stage "
             "directions, no emojis. Never mention chapters, videos or the script itself.")
    for i, head in enumerate(heads):
        log(f"  {i + 1}/{len(heads)} 章を書いています: {head}")
        body = _ollama(
            f"You are writing {voice}\nVideo title: {title}\nChapter {i + 1} of {len(heads)}: {head}\n"
            + (f"Earlier chapters covered: {story_so_far[-600:]}\n" if story_so_far else "")
            + f"Write about {words} words of narration for this chapter only. " + rules + " " + RETENTION["all"] + " "
            + " ".join(RETENTION[k] for k in (["first"] if i == 0 else []) + (["last"] if i == len(heads) - 1 else [])
                       or ["middle"]), model)
        paras = _dedupe(_paragraphs(body), seen)
        for _ in range(3):             # models stop early: ask for more until the chapter is long enough
            have = sum(len(x.split()) for x in paras)
            if not paras or have >= words * 0.8:
                break
            more = _paragraphs(_ollama(
                f"You are writing {voice}\nVideo title: {title}\nChapter: {head}\n"
                f"The chapter so far ends with: \"{' '.join(paras[-2:])[-500:]}\"\n"
                f"Continue with about {words - have} more words of NEW narration: new images, new "
                "questions, a short story if it fits. Do not repeat or summarise what was said. " + rules, model))
            more = _dedupe(more, seen)
            if not more:
                break
            paras += more
        if not paras:
            continue
        story_so_far += " " + paras[0]
        lines += [f"## {head}", ""] + [x for p in paras for x in (p, "")]
    if len(lines) <= 2:
        raise RuntimeError("AI が台本を書けませんでした(Ollama の状態を確認してください)")
    meta = {"title": title, "thumbnail": str(outline.get("thumbnail") or "").strip(),
            "description": str(outline.get("description") or "").strip()}
    return "\n".join(lines).rstrip() + "\n", meta


# ---------------------------------------------------------------- footage queries

STOP = set("the a an and or but of to in on for with at by from is are was were be been it its this that "
           "these those they them their you your we our he she his her as so than then there here what "
           "which who whom how why when where not no can could would should will just also more most "
           "very into over about after before only even still every each one two three".split())


def keyword_query(text, fallback):
    """No LLM: the two most frequent content words of the scene."""
    words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", text) if w.lower() not in STOP]
    if not words:
        return random.choice(fallback)
    top = sorted(set(words), key=lambda w: (-words.count(w), words.index(w)))[:2]
    return " ".join(top)


def llm_queries(scenes, model, log=print):
    """Ask the local LLM for one short, filmable Pexels query per scene."""
    import prep, urllib.request
    try:
        prep.ensure_ollama(log)
        numbered = "\n".join(f"{i + 1}. {sc['text'][:400]}" for i, sc in enumerate(scenes))
        prompt = ("For each numbered narration paragraph, write ONE stock-footage search query "
                  "(2-4 English words) describing something a camera can film that fits it "
                  "(e.g. 'supermarket shopping cart', 'stock market screen', 'factory workers'). "
                  "No brand names, no abstract words. Answer as a JSON list of strings, one per paragraph, "
                  f"exactly {len(scenes)} items.\n\n{numbered}")
        body = json.dumps({"model": model, "stream": False, "keep_alive": 0, "format": "json",
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(prep.OLLAMA, body, {"Content-Type": "application/json"})
        out = json.loads(json.load(urllib.request.urlopen(req, timeout=180))["message"]["content"])
        if isinstance(out, dict):     # {"queries": [...]} or similar
            out = next((v for v in out.values() if isinstance(v, list)), [])
        out = [str(q).strip() for q in out]
        return out if len(out) == len(scenes) and all(out) else None
    except Exception as e:
        log(f"  ! LLM で検索語を作れませんでした({e})。キーワードで代用します")
        return None


def plan_queries(scenes, s, log=print, chunk=8):
    """One filmable search query per scene. The local LLM is asked 8 scenes at a time
    (a 7B model miscounts long lists); a chunk it can't answer falls back to the preset's
    curated footage list, cycled so neighbours differ (keyword pairs such as "because
    within" find nothing filmable)."""
    need = [i for i, sc in enumerate(scenes) if not sc.get("query") and not sc.get("chart")]
    if need and not s["offline"]:
        missed = 0
        for a in range(0, len(need), chunk):
            part = need[a:a + chunk]
            got = llm_queries([scenes[i] for i in part], s["llm_model"], log)
            for j, i in enumerate(part):
                if got:
                    scenes[i]["query"] = got[j]
            missed += 0 if got else len(part)
        if missed:
            log(f"  ! {missed} 場面は AI で検索語を作れなかったので、プリセットの映像リストから選びます")
    pool = list(s["queries"])
    random.shuffle(pool)
    for k, sc in enumerate(scenes):
        sc.setdefault("query", pool[k % len(pool)])


def take_for_query(query, n, s, log=print):
    """n distinct fresh clips for one query (downloaded on demand), moved to 素材/stock_used/.
    Falls back to the preset's general pool when the query has too few results."""
    folder = mv.LIB / query.replace(" ", "_")
    picks = []
    if not s["offline"] and mv.has_pexels_key():
        have = len([c for c in folder.glob("*.mp4") if mv._is_clip(c)]) if folder.exists() else 0
        if have < n:
            got = mv.fetch_new(query, mv.pexels_key(), n - have, log)
            log(f"  + {query}: {got} 本ダウンロード")
    with mv.file_lock("library"):
        fresh = [c for c in folder.glob("*.mp4") if mv._is_clip(c)] if folder.exists() else []
        random.shuffle(fresh)
        for c in fresh[:n]:
            dest = mv.USED / c.parent.name / c.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            c.replace(dest)
            picks.append(dest)
    if len(picks) < n:              # already-used clips of the same query beat unrelated footage
        used = [c for c in (mv.USED / folder.name).glob("*.mp4") if mv._is_clip(c) and c not in picks]
        random.shuffle(used)
        picks += used[:n - len(picks)]
    if len(picks) < n:
        picks += mv.take_clips(n - len(picks), query, s, log)
    return picks


# ---------------------------------------------------------------- TTS

async def _edge(text, voice, rate, dest, pitch="+0Hz"):
    """Save edge-tts audio to dest; return word timings [(text, start, end)] in seconds."""
    import edge_tts
    bounds = []
    with open(dest, "wb") as f:
        async for chunk in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch,
                                                boundary="WordBoundary").stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                st = chunk["offset"] / 1e7
                bounds.append((chunk["text"], st, st + chunk["duration"] / 1e7))
    return bounds


def script_words(text, bounds):
    """Put the script's own spelling and punctuation back on the TTS word timings
    (subtitles then match the script exactly, e.g. '$4.8' instead of a transcription guess)."""
    norm = lambda w: re.sub(r"[^a-z0-9]", "", w.lower())
    tokens, ti, words = text.split(), 0, []
    for b, st, en in bounds:
        nb, word = norm(b), b
        for j in range(ti, min(ti + 4, len(tokens))):
            nt = norm(tokens[j])
            if nb and nt and (nt.startswith(nb) or nb.startswith(nt)):
                k = max(1, len(b.split()))            # one boundary can cover "In 2004"
                word, ti = " ".join(tokens[j:j + k]), j + k
                break
        words.append({"w": word, "s": round(st, 3), "e": round(en, 3)})
    return words


def tts(text, dest, s, voice):
    """Voice one scene into dest (.wav, 44.1 kHz mono) with edge-tts (or macOS say when
    tts_engine is "say"). Returns word timings relative to the scene start, or None.
    edge-tts failing is an error, not a silent switch to another voice: one scene in a
    different voice would ruin a long video. Finished scenes are cached, so running the
    script again continues where it stopped."""
    raw, words = dest.with_suffix(".raw.mp3"), None
    if s["tts_engine"] == "edge":
        for attempt in range(5):
            try:
                bounds = asyncio.run(_edge(text, voice, s["tts_rate"], raw, s.get("tts_pitch", "+0Hz")))
                if not raw.exists() or raw.stat().st_size == 0:
                    raise RuntimeError("音声が空でした")
                words = script_words(text, bounds) if bounds else None
                break
            except Exception as e:
                print(f"  ! edge-tts (try {attempt + 1}): {e}", flush=True)
                time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError("ナレーションを作れませんでした(edge-tts に接続できません)。"
                               "ネット接続を確認して、もう一度作ると続きから再開します")
    else:
        raw = None
    if raw is None:                                   # offline / failed: macOS voice
        raw = dest.with_suffix(".raw.aiff")
        subprocess.run(["say", "-o", str(raw), text], check=True)
    mv.run(["ffmpeg", "-y", "-i", str(raw), "-ar", "44100", "-ac", "1", str(dest)])
    raw.unlink(missing_ok=True)
    return words


# ---------------------------------------------------------------- narrator voices (CosyVoice 2)

MY_VOICES = mv.MEDIA / "voices"            # voices the user recorded: <id>.wav + <id>.json {name, text}
ASSET_VOICES = HERE / "assets" / "voices"  # built-in male voices that ship with the tool (Atlas, Orion, Titan)
DEFAULT_VOICE = "atlas"


def my_voice(vid):
    """(wav, info) of a built-in or recorded voice, or None (also for ids that are not plain names)."""
    if not vid or not re.fullmatch(r"[a-z0-9]{4,16}", str(vid)):
        return None
    for d in (MY_VOICES, ASSET_VOICES):
        wav, meta = d / f"{vid}.wav", d / f"{vid}.json"
        if wav.exists() and meta.exists():
            return wav, json.loads(meta.read_text())
    return None


def voice_label(s):
    mine = my_voice(s.get("voice_id")) or my_voice(DEFAULT_VOICE)
    return f"CosyVoice 2 {mine[1]['name']}" if mine else "CosyVoice 2"


COSY_PY = HERE / "tools" / "cosyvoice_venv" / "bin" / "python"
COSY_BATCH = HERE / "tools" / "cosy_batch.py"
COSY_REPO = HERE / "tools" / "CosyVoice"
COSY_MODEL = COSY_REPO / "pretrained_models" / "CosyVoice2-0.5B"
COSY_SETUP = "see docs/cosyvoice2_setup.md"
COSY_REF_TEXT = ("Every man reaches a point where nobody claps for him anymore. "
                 "Nobody checks on him. And that is exactly where his real life begins.")


def cosy_available():
    return COSY_PY.exists() and COSY_BATCH.exists() and (COSY_MODEL / "cosyvoice2.yaml").exists()


def cosy_voice(scenes, wavs, s, log=print):
    """Voice every scene without a wav with CosyVoice 2 (tools/cosy_batch.py): one reference clip for
    the whole video (a built-in or recorded voice), then word timings."""
    todo = [(sc, w) for sc, w in zip(scenes, wavs) if not w.exists()]
    if todo:
        mine = my_voice(s.get("voice_id")) or my_voice(DEFAULT_VOICE)
        if not mine:
            raise RuntimeError("声の見本がありません(assets/voices/atlas.wav)")
        ref, ref_text = str(mine[0]), mine[1]["text"]
        job = wavs[0].parent / "cosy_job.json"
        mv.write_json(job, {"repo": str(COSY_REPO), "model": str(COSY_MODEL), "ref_audio": str(ref),
                            "ref_text": ref_text, "speed": float(s.get("tts_speed", 1.0)),
                            "items": [{"text": sc["text"], "out": str(w)} for sc, w in todo]})
        log(f"  ナレーションを作成中(CosyVoice 2・この Mac の GPU): {len(todo)} 段落")
        proc = subprocess.Popen([str(COSY_PY), str(COSY_BATCH), str(job)], cwd=HERE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        tail, shown = [], -1
        for line in proc.stdout:
            if line.startswith("TTS "):
                done, total = map(int, line.split()[1:3])
                if done * 10 // max(1, total) > shown:
                    shown = done * 10 // max(1, total)
                    log(f"  ナレーション {done}/{total}")
            elif "Error" in line or "Traceback" in line or "Exception" in line:
                tail = (tail + [line.strip()])[-4:]
        if proc.wait() != 0:
            raise RuntimeError("CosyVoice でナレーションを作れませんでした: " + " / ".join(tail[-2:]))
        job.unlink(missing_ok=True)
    write_words(scenes, wavs)


def write_words(scenes, wavs):
    """Per-scene word timings (Whisper), carrying the script's own spelling."""
    for sc, w in zip(scenes, wavs):
        wj = w.with_suffix(".words.json")
        hj = w.with_suffix(".heard.json")             # Whisper timings
        if not wj.exists():
            heard = mv.read_json(hj, None) or mv.whisper_words(w, "base")   # timing only: spelling from the script
            mv.write_json(wj, script_words(sc["text"], [(x["w"], x["s"], x["e"]) for x in heard]) or None)


def free_models():
    """Give back the memory of models this process loaded for one video (Whisper, CLIP)."""
    import gc, sys as _sys
    mv._whisper.clear()
    mm = _sys.modules.get("media_match")
    if mm is not None:
        mm._clip.clear()
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass


# ---------------------------------------------------------------- charts

def _rgb(hex6):
    h = hex6.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _fmt(v, unit):
    txt = f"{v:,.1f}" if abs(v) < 100 and v != int(v) else f"{v:,.0f}"
    if unit.startswith("$"):
        return f"${txt}{unit[1:]}"
    return f"{txt}{unit}" if unit in ("%",) else f"{txt} {unit}".strip()


def chart_frame(chart, progress, size, theme):
    """One frame of an animated line/bar chart. progress 0..1 = how much is drawn."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = size
    img = Image.new("RGB", size, _rgb(theme["bg"]))
    d = ImageDraw.Draw(img)
    fg, accent, grid = _rgb(theme["fg"]), _rgb(theme["accent"]), _rgb(theme["grid"])
    k = W / 1920
    f_title = ImageFont.truetype(FONT_BLACK, int(64 * k))
    f_lab = ImageFont.truetype(FONT_BOLD, int(34 * k))
    f_val = ImageFont.truetype(FONT_BOLD, int(40 * k))
    labels, values = [str(x) for x in chart["labels"]], [float(v) for v in chart["values"]]
    unit = chart.get("unit", "")
    d.text((W * 0.08, H * 0.08), chart.get("title", ""), font=f_title, fill=fg)
    if chart.get("source"):
        d.text((W * 0.08, H * 0.83), "Source: " + chart["source"], font=f_lab, fill=(140, 140, 140))
    x0, x1, y0, y1 = W * 0.10, W * 0.92, H * 0.22, H * 0.74   # bottom 20 % stays free for subtitles
    lo = min(0.0, min(values)); hi = max(values) * 1.15 or 1.0
    Y = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)
    for i in range(5):                                # grid lines
        gy = y0 + (y1 - y0) * i / 4
        d.line([(x0, gy), (x1, gy)], fill=grid, width=max(1, int(2 * k)))
    n = len(values)
    ease = 1 - (1 - min(progress, 1)) ** 3
    if chart.get("type", "line") == "bar":
        slot = (x1 - x0) / n
        bw = slot * 0.6
        for i, (lab, v) in enumerate(zip(labels, values)):
            cx = x0 + slot * (i + 0.5)
            grow = max(0.0, min(1.0, ease * n - i * 0.6))   # bars rise one after another
            top = Y(lo + (v - lo) * grow)
            d.rectangle([cx - bw / 2, top, cx + bw / 2, Y(lo)], fill=accent if i == n - 1 else fg)
            d.text((cx, y1 + 20 * k), lab, font=f_lab, fill=fg, anchor="ma")
            if grow >= 1:
                d.text((cx, top - 12 * k), _fmt(v, unit), font=f_val, fill=fg, anchor="md")
    else:
        xs = [x0 + (x1 - x0) * (i / max(n - 1, 1)) for i in range(n)]
        pts = [(x, Y(v)) for x, v in zip(xs, values)]
        reach = ease * (n - 1)
        drawn = pts[:int(reach) + 1]
        if int(reach) < n - 1:                        # partial segment to the moving tip
            a, b, t = pts[int(reach)], pts[int(reach) + 1], reach - int(reach)
            drawn.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        if len(drawn) > 1:
            d.line(drawn, fill=accent, width=int(8 * k), joint="curve")
        for i, (lab, (x, y)) in enumerate(zip(labels, pts)):
            d.text((x, y1 + 20 * k), lab, font=f_lab, fill=fg, anchor="ma")
            if i <= reach + 1e-6:
                r = 10 * k
                d.ellipse([x - r, y - r, x + r, y + r], fill=accent)
                d.text((x, y - 24 * k), _fmt(values[i], unit), font=f_val, fill=fg, anchor="md")
    return img


def render_chart(chart, seconds, dest, s):
    """Animated chart clip: drawn during the first 60 %, then held."""
    size = mv.frame_size(s)
    theme = {**STORY_DEFAULTS["chart_theme"], **s.get("chart_theme", {})}
    frames = max(1, round(seconds * FPS))
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-",
                          "-vf", "format=yuv420p", "-c:v", "h264_videotoolbox", "-b:v", s["bitrate"],
                          str(dest)], stdin=subprocess.PIPE)
    last, last_p = None, None
    for f in range(frames):
        prog = min(1.0, f / max(1, frames * 0.6))
        if prog != last_p:                            # held frames are reused
            last, last_p = chart_frame(chart, prog, size, theme).tobytes(), prog
        p.stdin.write(last)
    p.stdin.close()
    if p.wait():
        raise RuntimeError("グラフ動画の作成に失敗しました")


# ---------------------------------------------------------------- photos

def render_photo(jpg, seconds, dest, s, n=0, trim=0.0):
    """Still photo -> slow zoom/pan clip (Ken Burns); direction alternates by n.
    trim: share cut from every edge first (AI images: stray signatures and page numbers
    that FLUX draws in the corners)."""
    w, h = mv.frame_size(s)
    frames = max(1, round(seconds * FPS))
    amt = mv.ZOOM_AMOUNT.get(s.get("zoom_amount", "normal"), 0.06)      # Settings: how far the picture pushes in
    step = amt / frames
    z = f"{1 + amt:.3f}-{step}*on" if n % 2 else f"1.0+{step}*on"       # in, out, in ...: neighbours meet at the same scale
    focus = s.get("zoom_focus", "face")
    if focus == "auto":                                                  # a different anchor each shot
        focus = ["face", "left", "right", "center"][n % 4]
    fx, fy = mv.ZOOM_FOCUS.get(focus, mv.ZOOM_FOCUS["face"])           # the anchor point stays where it is while zooming
    x = f"(iw-iw/zoom)*{fx}"
    cut = f"crop=iw*{1 - 2 * trim:.3f}:ih*{1 - 2 * trim:.3f}," if trim else ""
    vf = (f"{cut}scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
          f"zoompan=z='{z}':x='{x}':y='(ih-ih/zoom)*{fy}':d={frames}:s={w}x{h}:fps={FPS},"
          f"eq=contrast={s['contrast']}:brightness={s['brightness']}:saturation={s['saturation']}"
          + (",vignette=PI/4.5" if s.get("vignette") else "")
          + (",noise=alls=10:allf=t" if s.get("grain") else "") + ",format=yuv420p")
    mv.run(["ffmpeg", "-y", "-loop", "1", "-i", str(jpg), "-t", f"{seconds:.3f}", "-vf", vf,
            "-c:v", "h264_videotoolbox", "-b:v", s["bitrate"], str(dest)])


def join_crosszoom(segs, s, work, group=10):
    """Join clips with a dissolve while both pictures are zooming around the chosen anchor (the clips
    alternate push-in / pull-out, so the scale matches across the overlap: a cross zoom). Every clip was
    rendered s["cross"] seconds longer than its slot, so overlapping them keeps the timeline exactly where the
    narration expects it. Done in groups of `group` clips (one small ffmpeg graph each), then the groups are joined."""
    CROSS = float(s["cross"])

    def chain(clips, dest):
        durs = [mv.duration(c) for c in clips]
        if len(clips) == 1:
            return clips[0]
        cmd, graph, off = ["ffmpeg", "-y"], [], 0.0
        for c in clips:
            cmd += ["-i", str(c)]
        prev = "[0:v]"
        for i in range(1, len(clips)):
            off += durs[i - 1] - CROSS
            lab = f"[v{i}]"
            graph.append(f"{prev}[{i}:v]xfade=transition=fade:duration={CROSS}:offset={max(off, 0):.3f}{lab}")
            prev = lab
        mv.run(cmd + ["-filter_complex", ";".join(graph), "-map", prev, "-r", str(FPS), "-pix_fmt", "yuv420p",
                      "-c:v", "h264_videotoolbox", "-b:v", s["bitrate"], str(dest)])
        return dest
    level, tag = list(segs), 0
    while len(level) > 1:
        nxt = []
        for k in range(0, len(level), group):
            part = level[k:k + group]
            nxt.append(chain(part, work / f"xf{tag}_{k // group:03d}.mp4") if len(part) > 1 else part[0])
        level, tag = nxt, tag + 1
    return level[0]


def photo_shots(sc, used, log=print, shot=5.0):
    """Split a photo scene into ~5 s shots, one Commons photo each. [] if none found."""
    want = max(1, round(sc["dur"] / shot))
    photos = commons.fetch(sc["photo"], want, used, log)
    if not photos:
        log(f"  ! 「{sc['photo']}」の写真が見つからないので、素材映像を使います")
        return []
    for ph in photos:
        used.add(ph["title"])
    each = sc["dur"] / len(photos)
    return [(ph, each) for ph in photos]


# ---------------------------------------------------------------- headings / thumbnail

def heading_events(heads, s):
    """ASS lines for section headings (top-left box), appended to the subtitle file."""
    w, h = mv.frame_size(s)
    style = (f"Style: Heading,Arial Black,{int(58 * w / 1920)},&H00FFFFFF,&H00FFFFFF,&H0000C8FF,"
             f"&H0000C8FF,0,0,0,0,100,100,1,0,3,14,0,7,{int(90 * w / 1920)},0,{int(80 * h / 1080)},1")
    lines = [f"Dialogue: 1,{mv.ass_time(t)},{mv.ass_time(t + dur)},Heading,,0,0,0,,"
             f"{{\\fad(250,250)\\1c&H000000&}}{mv.ass_safe(text).upper()}" for t, dur, text in heads]
    return style, lines


def credit_events(credits, s):
    """Small photo credit in the top-right corner while the photo is on screen."""
    w, h = mv.frame_size(s)
    style = (f"Style: Credit,Arial,{int(24 * w / 1920)},&H00FFFFFF,&H00FFFFFF,&H00000000,"
             f"&H80000000,0,0,0,0,100,100,0,0,1,2,0,9,0,{int(40 * w / 1920)},{int(36 * h / 1080)},1")
    lines = [f"Dialogue: 2,{mv.ass_time(t)},{mv.ass_time(t + dur)},Credit,,0,0,0,,{mv.ass_safe(text)}"
             for t, dur, text in credits]
    return style, lines


def add_headings(ass, heads, s, credits=()):
    styles, lines = [], []
    for events, make in ((heads, heading_events), (credits, credit_events)):
        if events:
            st, ln = make(events, s)
            styles.append(st); lines += ln
    if not lines:
        return
    txt = ass.read_text()
    txt = txt.replace("\n\n[Events]", "\n" + "\n".join(styles) + "\n\n[Events]", 1)
    ass.write_text(txt.rstrip("\n") + "\n" + "\n".join(lines) + "\n")


def make_thumbnail(clip, text, dest, s):
    """Darkened frame from the first clip + big two-line text in the accent colour."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont
    frame = dest.with_suffix(".frame.jpg")
    still = Path(clip).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    seek = [] if still else ["-ss", f"{max(1.0, mv.duration(clip) * 0.45):.2f}"]   # mid-shot: in focus, not a fade
    mv.run(["ffmpeg", "-y", *seek, "-i", str(clip), "-frames:v", "1",
            "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", str(frame)])
    img = Image.open(frame).convert("RGB")
    img = ImageEnhance.Color(img).enhance(float(s.get("saturation", 1.0)))    # same grade as the video
    img = ImageEnhance.Brightness(img).enhance(0.55)
    d = ImageDraw.Draw(img)
    words = text.upper().split()
    lines = [" ".join(words[:math.ceil(len(words) / 2)]), " ".join(words[math.ceil(len(words) / 2):])]
    lines = [l for l in lines if l]
    size = 150
    font = ImageFont.truetype(FONT_BLACK, size)
    while size > 60 and max(d.textlength(l, font=font) for l in lines) > 1180:
        size -= 6; font = ImageFont.truetype(FONT_BLACK, size)
    accent = _rgb(s.get("chart_theme", {}).get("accent", STORY_DEFAULTS["chart_theme"]["accent"]))
    y = 360 - len(lines) * size * 0.55
    for i, l in enumerate(lines):
        d.text((60, y + i * size * 1.1), l, font=font, fill=accent if i == len(lines) - 1 else (255, 255, 255),
               stroke_width=8, stroke_fill=(0, 0, 0))
    img.save(dest, quality=92)
    frame.unlink(missing_ok=True)


def write_publish(data, chapters, dest_md, dest_json, video=None):
    """Title / description (with chapters and sources) for upload."""
    desc = data.get("description", "").strip()
    if len(chapters) > 1 and chapters[0][0] == 0:     # YouTube chapters need 0:00 first
        desc += "\n\n" + "\n".join(f"{int(t // 60)}:{int(t % 60):02d} {name}" for t, name in chapters)
    if data.get("sources"):
        desc += "\n\nSources:\n" + "\n".join(f"- {u}" for u in data["sources"])
    if data.get("photo_credits"):
        desc += "\n\nPhoto credits:\n" + "\n".join(f"- {c}" for c in data["photo_credits"])
    meta = {"title": data["title"], "description": desc.strip(), "tags": data.get("tags", [])}
    if video is not None:     # same fields as publish_meta, so the 投稿 page shows script videos too
        video = Path(video)
        meta.update({"title_en": data["title"], "description_en_full": meta["description"],
                     "title_ja": data.get("title_ja", ""), "description_ja_full": data.get("description_ja", ""),
                     "hashtags": data.get("hashtags", []), "video": video.name,
                     "thumbnail_text": data.get("thumbnail") or data["title"],
                     "speech_source": "original script (TTS)"})
        if video.with_suffix(".thumb.jpg").exists():
            meta["thumbnail"] = video.with_suffix(".thumb.jpg").name
    dest_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    dest_md.write_text(f"# {meta['title']}\n\n## Description\n\n{meta['description']}\n\n"
                       f"## Tags\n\n{', '.join(meta['tags'])}\n")


# ---------------------------------------------------------------- render

def make_story(script, outdir, s, log=print):
    """Render one script; models loaded on the way are released afterwards (the server
    keeps running between videos, so they would otherwise stay in memory)."""
    # one heavy render at a time on this Mac (CosyVoice, FLUX, Whisper and CLIP all share the
    # 24 GB of unified memory; two at once is what pushed it deep into swap)
    lock = mv.TMP / "heavy.lock"
    lk = open(lock, "w")
    try:
        try:
            mv.fcntl.flock(lk, mv.fcntl.LOCK_EX | mv.fcntl.LOCK_NB)
        except BlockingIOError:
            log("  ほかの動画を作成中です。終わるまで待ってから始めます(重い処理は 1 本ずつ)")
            mv.fcntl.flock(lk, mv.fcntl.LOCK_EX)
        swap0 = mv.swap_used_mb()
        result = _make_story(script, outdir, s, log)
        grew = mv.swap_used_mb() - swap0
        if grew > 256:
            log(f"  ! この作成中にスワップが {grew:.0f}MB 増えました(ほかのアプリを閉じて作ると防げます)")
        return result
    finally:
        free_models()
        mv.unload_llm()
        mv.fcntl.flock(lk, mv.fcntl.LOCK_UN)
        lk.close()


def _make_story(script, outdir, s, log=print):
    data = load_script(script)
    name = slug(data["title"])
    out = Path(outdir) / f"{name}.mp4"
    work = mv.TMP / f"story_{name}"
    work.mkdir(parents=True, exist_ok=True)
    scenes = data["scenes"]
    voice = data.get("voice") or s["voice"]
    log(f"== {data['title']}  ({len(scenes)} シーン, 声: "
        f"{voice_label(s) if s['tts_engine'] == 'cosy' else voice})")

    # 1. narration, one file per scene (cached by text+voice)
    parts, t, heads, chapters = [], 0.0, [], []
    silence = work / "gap.wav"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono", "-t", str(GAP), str(silence)])
    engine = s["tts_engine"]
    if engine == "cosy" and not cosy_available():
        log("  ! CosyVoice の準備ができていないので、edge-tts の声で作ります")
        engine = "edge"
    if engine == "cosy":
        mine = my_voice(s.get("voice_id")) or my_voice(DEFAULT_VOICE)
        vkey = f"cosy{s.get('voice_id') or DEFAULT_VOICE}{int(mine[0].stat().st_mtime) if mine else 0}s{float(s.get('tts_speed', 1)):.2f}"
    else:
        vkey = voice + s["tts_rate"] + s.get("tts_pitch", "+0Hz")
    wavs = [work / f"v{i:03d}_{mv.hashlib.md5((vkey + sc['text']).encode()).hexdigest()[:10]}.wav"
            for i, sc in enumerate(scenes)]
    if engine == "cosy":
        mv.memory_gate(log)
        cosy_voice(scenes, wavs, s, log)
    for i, (sc, wav) in enumerate(zip(scenes, wavs)):
        wj = wav.with_suffix(".words.json")
        if not wav.exists():
            mv.write_json(wj, tts(sc["text"], wav, {**s, "tts_engine": engine}, voice))
        sc["words"] = mv.read_json(wj, None)
        sc["dur"] = mv.duration(wav) + GAP
        sc["start"] = t
        if sc.get("heading"):
            chapters.append((t, sc["heading"]))      # YouTube chapters even when not shown
            if s.get("show_headings", True):
                heads.append((t, min(s["heading_seconds"], sc["dur"]), sc["heading"]))
        t += sc["dur"]
        parts += [wav, silence]
    log(f"  ナレーション完成: {t / 60:.1f} 分")
    voice_wav = work / "voice.wav"
    lst = work / "voice.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    mv.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(voice_wav)])
    total = mv.duration(voice_wav)

    # 2. visuals: original ink illustrations (ai), footage matched to each scene, or a chart
    log("  映像を準備しています(場面ごとの検索語を決めています)")
    plan_queries(scenes, s, log)
    art, own = {}, {}
    if s.get("visuals") == "folder":
        import media_match
        folder = str(s.get("media_folder") or "").strip()
        index = media_match.index_folder(folder, log) if folder else {"items": []}
        if index["items"]:
            log(f"  自分の素材 {len(index['items'])} 本から、段落の意味に合うものを選んでいます")
            own = media_match.match(scenes, index, s, log)
            mv.write_json(work / "matches.json", {str(i): v for i, v in own.items()})
        else:
            log("  ! 素材フォルダに使えるファイルがないので、素材映像(Pexels)で作ります")
    if s.get("visuals") == "ai":
        import illustrate
        if illustrate.available():
            illustrate.plan(scenes, s, log)          # LLM first, then free it for FLUX
            mv.memory_gate(log)
            art = illustrate.illustrate([sc for sc in scenes], s, log)
        else:
            log("  ! AI イラストのモデルがまだ準備できていないので、白黒の素材映像で作ります")
    finish = mv.load_finish()                          # Settings: one look for every video
    crosszoom = finish["transition"] == "crosszoom" and s.get("visuals") == "ai"
    grade, segs, first_clip = mv.grade_filter(s), [], None
    used_photos, credits, photo_credits = set(), [], []
    for i, sc in enumerate(scenes):
        if i in art and not sc.get("chart"):
            each = sc["dur"] / len(art[i]) + (s["cross"] if crosszoom else 0)    # neighbours overlap by s["cross"] seconds
            for j, png in enumerate(art[i]):
                seg = work / f"s{i:03d}_a{j:02d}.mp4"
                pic = illustrate.trim_bands(png, work / f"s{i:03d}_a{j:02d}.png")   # no side bars
                render_photo(pic, each, seg, s, n=i + j, trim=0.07)
                segs.append(seg)
            first_clip = first_clip or art[i][0]
            if i % 10 == 0 or i == len(scenes) - 1:
                log(f"  シーン {i + 1}/{len(scenes)}: イラスト {len(art[i])} 枚")
            continue
        if i in own and not sc.get("chart"):
            for j, sh in enumerate(own[i]):
                seg = work / f"s{i:03d}_m{j:02d}.mp4"
                if sh["kind"] == "image":
                    render_photo(sh["path"], sh["dur"], seg, s, n=i + j)
                else:                                 # loop clips shorter than the shot
                    mv.run(["ffmpeg", "-y", "-stream_loop", "-1", "-ss", f"{sh['start']:.2f}", "-i", sh["path"],
                            "-t", f"{sh['dur']:.3f}", "-an", "-vf", grade, "-c:v", "h264_videotoolbox",
                            "-b:v", s["bitrate"], str(seg)])
                segs.append(seg)
            first_clip = first_clip or own[i][0]["path"]
            names = ", ".join(Path(sh["path"]).name for sh in own[i])
            log(f"  シーン {i + 1}/{len(scenes)}: {names}")
            continue
        shots = photo_shots(sc, used_photos, log) if sc.get("photo") and not s["offline"] else []
        if shots:
            t0 = sc["start"]
            for j, (ph, d) in enumerate(shots):
                seg = work / f"s{i:03d}_p{j:02d}.mp4"
                render_photo(ph["path"], d, seg, s, n=i + j)
                segs.append(seg)
                credits.append((t0, d, f"Photo: {ph['author']} / {ph['license']} / Wikimedia Commons"))
                photo_credits.append(commons.credit(ph))
                t0 += d
            first_clip = first_clip or shots[0][0]["path"]
            log(f"  シーン {i + 1}/{len(scenes)}: 写真「{sc['photo']}」 {len(shots)} 枚")
            continue
        if sc.get("chart"):
            seg = work / f"s{i:03d}_chart.mp4"
            render_chart(sc["chart"], sc["dur"], seg, s)
            segs.append(seg)
            log(f"  シーン {i + 1}/{len(scenes)}: グラフ「{sc['chart'].get('title', '')}」")
            continue
        cut = float(s["cut"])
        lengths, left = [], sc["dur"]
        while left > 0.05:
            d = min(round(random.uniform(cut * 0.8, cut * 1.2), 2), left)
            if left - d < 1.0:                        # no tiny last shot
                d = left
            lengths.append(d); left -= d
        clips = take_for_query(sc["query"], len(lengths), s, log)
        if not clips:
            raise RuntimeError("素材がありません")
        first_clip = first_clip or clips[0]
        for j, d in enumerate(lengths):
            src = clips[j % len(clips)]
            start = random.uniform(0, max(0, mv.duration(src) - d - 0.1))
            seg = work / f"s{i:03d}_{j:02d}.mp4"
            mv.run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{d:.3f}", "-i", str(src), "-an",
                    "-vf", grade, "-c:v", "h264_videotoolbox", "-b:v", s["bitrate"], str(seg)])
            segs.append(seg)
        log(f"  シーン {i + 1}/{len(scenes)}: 「{sc['query']}」 {len(lengths)} カット")

    # 3. subtitles (Whisper on our own narration) + section headings
    vf = f"fade=t=in:d={min(float(s['fade_in']), 1.0):.2f},fade=t=out:st={total - 1.0:.2f}:d=1.0"
    look = mv.finish_filter(finish)
    if look:
        vf += "," + look
    words = []
    kinetic = s["subs"] and s["subs_engine"] == "kinetic"
    if s["subs"] or heads or credits:
        ass = work / "subs.ass"
        if s["subs"]:
            if all(sc.get("words") for sc in scenes):          # exact TTS timings
                words = [{**w, "s": round(w["s"] + sc["start"], 3), "e": round(w["e"] + sc["start"], 3)}
                         for sc in scenes for w in sc["words"]]
            else:
                log("  字幕を作成中(Whisper)...")
                words = mv.transcribe(voice_wav, work / "words.json", s["whisper_model"])
            if kinetic:            # captions drawn by kinetic-captions after the encode
                mv.write_simple_ass(words, work / "subs_plain.ass", s)
                mv.write_simple_ass([], ass, s)
            else:
                mv.write_simple_ass(words, ass, s)
        else:
            mv.write_simple_ass([], ass, s)
        add_headings(ass, heads, s, credits)
        if not kinetic or heads or credits:
            vf += f",ass={ass}"

    # 4. assemble with BGM under the voice
    if crosszoom and len(segs) > 1:
        log("  絵と絵のあいだをクロスズームでつないでいます...")
        segs = [join_crosszoom(segs, s, work)]
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in segs))
    log("  最終エンコード中...")
    music = mv.pick_music(total, s, log) if s.get("bgm") else None
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(voice_wav)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", music["path"],
                "-filter_complex", mv.bgm_filter("1:a", "2:a", total, 1.0, s), "-vf", vf,
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-vf", vf, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-map", "0:v", "-map", "1:a"]
    Path(outdir).mkdir(parents=True, exist_ok=True)
    tmp_out = work / "out.mp4"
    mv.run(cmd + ["-c:v", "h264_videotoolbox", "-b:v", s["bitrate"], "-c:a", "aac", "-b:a", "192k",
                  "-shortest", str(tmp_out)])
    if kinetic:
        log("  kinetic-captions で字幕を入れています...")
        ks = dict(s)
        if heads and ks.get("kc_position", "auto") == "auto":
            ks["kc_position"] = "center"     # headings sit top-left: keep captions off them
        plain, tmp_out = tmp_out, work / "out_kc.mp4"
        mv.burn_kinetic(plain, words, tmp_out, ks, work, log)
        plain.unlink(missing_ok=True)
    tmp_out.replace(out)

    # 5. upload kit: thumbnail, title/description with chapters + sources, music credit
    if first_clip:
        make_thumbnail(first_clip, data.get("thumbnail") or data["title"], out.with_suffix(".thumb.jpg"), s)
    if music:
        data["description"] = (data.get("description", "") +
                               f"\n\nMusic: \"{music['title']}\" by {music['artist']} ({music['license']})").strip()
    data["photo_credits"] = list(dict.fromkeys(photo_credits))
    write_publish(data, chapters, out.with_suffix(".publish.md"), out.with_suffix(".publish.json"), out)
    mv.write_json(work / "render.json", {"video": str(out), "script": str(script), "total": total,
                                         "voice": voice, "queries": [sc.get("query") for sc in scenes],
                                         "time": time.time()})
    for p in segs:
        p.unlink(missing_ok=True)
    log(f"  -> {out}")
    return out


def load_story_settings(preset):
    s = {**mv.load_settings(), **STORY_DEFAULTS}
    fin = mv.load_finish()                             # Settings: colour of the pictures, zoom, transition
    s.update(art=fin["art"], zoom_amount=fin["zoom_amount"], zoom_focus=fin["zoom_focus"],
             cross=mv.TRANSITION_SECONDS[fin["transition_speed"]])
    if preset:
        p = PRESETS / f"{preset}.json"
        if not p.exists():
            raise SystemExit(f"プリセットがありません: {p}")
        s.update(mv.read_json(p, {}))
    if s["art"] == "color":
        s["saturation"] = 1.0                          # the ink preset greys every picture: colour needs it back
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--preset", default="bhatt")
    ap.add_argument("--out", default=str(OUTPUT))
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    s = load_story_settings(a.preset)
    if a.offline:
        s["offline"] = True
    p = Path(a.input)
    files = sorted([*p.glob("*.json"), *p.glob("*.txt")]) if p.is_dir() else [p]
    say = lambda m: print(m, flush=True)
    for f in files:
        try:
            make_story(f, a.out, s, log=say)
        except Exception as e:
            say(f"  FAILED {f.name}: {e}")
            if len(files) == 1:
                sys.exit(1)
