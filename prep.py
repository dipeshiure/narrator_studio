"""Audio preparation: keep only the motivational speech.

  1. Demucs removes background music (make_video.isolate_voice)
  2. Whisper transcribes the voice with word timestamps
  3. Sentences are checked for ads / sponsor reads / app promotion / subscribe
     requests / channel intros & outros by a local LLM (Ollama) + keyword rules
  4. The flagged parts are cut out; the result (final.wav + words) is what the
     video is built on.

Everything is cached in the per-audio work dir (tmp/<hash>/):
  voice.wav, words_src.json, analysis.json, final.wav, words_final.json
analysis.json holds one entry per sentence with "cut" (bool), "reason" and
"auto"; the review screen edits "cut" and the trim is re-applied.
"""
import hashlib, json, re, shutil, subprocess, urllib.request
from pathlib import Path

import make_video as mv

OLLAMA = "http://127.0.0.1:11434/api/chat"
# Demucs' "no vocals" stem quieter than this = the input is already voice-only:
# use the original file (no separation artefacts, and later runs skip Demucs).
VOICE_ONLY_DB = -45.0
# per-audio cache files that only depend on the audio content (reusable across names)
CONTENT_CACHE = ("music.json", "voice.wav", "words_voice.json", "words_original.json")
REASONS = {"ad": "広告", "sponsor": "スポンサー紹介", "promo_code": "プロモコード", "app": "アプリの宣伝",
           "link": "リンクの案内", "subscribe": "チャンネル登録・高評価のお願い", "social": "SNS の案内",
           "intro": "チャンネルの挨拶(冒頭)", "outro": "締めの挨拶・告知", "other": "宣伝・告知"}

# phrases that are (almost) never part of the motivational speech itself
AD_PATTERNS = [
    r"\bsubscrib(e|ed|ing)\b(?!\s+to\s+(your|my|his|her|their|the idea|a\b))", r"\blike (and|&) subscribe", r"\bhit the (bell|like)", r"\bnotification bell",
    r"\blinks? (is |are )?(in|down in) (the )?(description|bio)", r"\blinks? below\b", r"\bdescription below", r"\bsponsor", r"\bbrought to you by",
    r"\bpromo code", r"\bdiscount code", r"\bpercent off\b", r"\bfree trial", r"\bdownload (the|our|my) app",
    r"\bapp store\b", r"\bgoogle play\b", r"\bpatreon\b", r"\bfollow (us|me) on\b", r"\binstagram\b",
    r"\btiktok\b", r"\bmerch\b", r"\bcheck out (our|my)\b", r"\bwelcome back to\b",
    r"\b(what'?s up|hey|hello|hi),? (everybody|everyone|guys|you guys)\b", r"\btoday'?s (video|episode)\b",
    r"\bthank(s| you) (so much )?for watching\b", r"\bsee you (in the )?next (video|time)\b", r"\bcomment below\b",
]
AD_RE = re.compile("|".join(AD_PATTERNS), re.I)

# An AI "cut" is only accepted if the sentence also contains at least one of these
# words: real speech ("your struggle is your gift") never gets cut on the AI's word
# alone. Small local models otherwise mislabel whole stretches of the speech.
CUE_WORDS = [
    # words that (almost) only appear in ads / channel talk
    "app", "apps", "download", "install", "link", "links", "description", "code", "coupon", "discount",
    "percent", "%", "offer", "trial", "sponsor", "sponsored", "website", "dot com", ".com", "click",
    "subscribe", "subscribed", "subscribers", "bell", "notification", "notifications", "instagram",
    "tiktok", "twitter", "facebook", "youtube", "patreon", "podcast", "channel", "video", "videos",
    "episode", "merch", "newsletter", "email", "audible", "spotify",
    # everyday words count only inside ad phrases ("Anybody like football?" is speech)
    "like button", "like and share", "like and subscribe", "hit like", "smash", "share this",
    "share it with", "follow us", "follow me on", "comment below", "in the comments", "free trial",
    "for free", "percent off", "sign up", "order now", "buy now", "check out", "our store",
    "our shop", "brought to you",
]
CUE_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(w) for w in CUE_WORDS) + r")(?![a-z])", re.I)


def _norm(t):
    return re.sub(r"[^a-z0-9% ]+", "", t.lower()).split()

PROMPT = """You are cleaning up the audio of a motivational speech video.
Below are numbered sentences from its transcript (with start time in seconds).
List ONLY the sentences that are NOT part of the motivational speech itself, such as:
- ads, sponsor reads, product or app promotion, discount/promo codes
- requests to like, subscribe, comment, follow, turn on notifications
- channel intro/outro, "welcome back", "thanks for watching", credits, links
Keep everything that is the actual speech (stories, advice, quotes, encouragement),
even if it mentions money, business or success.
Be conservative: if you are not sure, it is part of the speech.
For each sentence you list, give a category (ad, sponsor, promo_code, app, link,
subscribe, social, intro, outro, other) and quote the exact words in it that show
it is not speech (e.g. "download the app", "thanks for watching").
Return JSON: {{"cut": [{{"i": <number>, "reason": "<category>", "quote": "<exact words>"}}]}}
Return {{"cut": []}} if every sentence is part of the speech.

{sentences}"""


# ---------------------------------------------------------------- sentences
def sentences_from_words(words, max_words=28, gap=1.0):
    """Group Whisper words into sentences (punctuation, long pause or max length)."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end = (w["w"][-1:] in ".!?" or nxt is None or nxt["s"] - w["e"] > gap
               or len(cur) >= max_words)
        if end:
            out.append({"s": round(cur[0]["s"], 2), "e": round(cur[-1]["e"], 2),
                        "text": " ".join(x["w"] for x in cur), "wi": [i - len(cur) + 1, i]})
            cur = []
    return out


# ---------------------------------------------------------------- classification
def ensure_ollama(log=print, wait=20):
    """Start the local Ollama server if it is not running (e.g. after a reboot). True if reachable."""
    import shutil, time as _t
    def up():
        try:
            urllib.request.urlopen(OLLAMA.replace("/api/chat", "/api/tags"), timeout=3)
            return True
        except Exception:
            return False
    if up():
        return True
    exe = shutil.which("ollama") or ("/usr/local/bin/ollama" if Path("/usr/local/bin/ollama").exists() else None)
    if not exe:
        log("  ! Ollama が見つかりません(AI を使う処理はキーワードだけで行います)")
        return False
    log("  Ollama を起動しています…")
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(wait):
        _t.sleep(1)
        if up():
            return True
    log("  ! Ollama を起動できませんでした")
    return False


def ask_llm(sentences, model, chunk=25):
    """Return {index: reason} for sentences the LLM thinks are not the speech.
    Only accepted when the quoted evidence really occurs in that sentence and the
    sentence contains an ad cue word (see CUE_RE)."""
    flagged = {}
    for start in range(0, len(sentences), chunk):
        part = sentences[start:start + chunk]
        listing = "\n".join(f"{start + j} [{x['s']:.0f}s]: {x['text']}" for j, x in enumerate(part))
        body = json.dumps({"model": model, "stream": False, "keep_alive": "60s", "format": "json",
                           "options": {"temperature": 0},
                           "messages": [{"role": "user", "content": PROMPT.format(sentences=listing)}]})
        req = urllib.request.Request(OLLAMA, body.encode(), {"Content-Type": "application/json"})
        reply = json.load(urllib.request.urlopen(req, timeout=300))["message"]["content"]
        for item in json.loads(reply).get("cut", []):
            try:
                i = int(item["i"])
            except (KeyError, TypeError, ValueError):
                continue
            if not start <= i < start + len(part):
                continue
            text = sentences[i]["text"]
            quote = _norm(str(item.get("quote") or ""))
            words = _norm(text)
            quoted = bool(quote) and any(words[k:k + len(quote)] == quote for k in range(len(words)))
            if quoted and CUE_RE.search(text):
                flagged[i] = REASONS.get(str(item.get("reason", "")).strip().lower(), "宣伝・告知")
    return flagged


def _judge(items, model, log, use_llm=True):
    """{index: reason} from the LLM (if reachable) merged with keyword rules."""
    llm = {}
    if use_llm:
        ensure_ollama(log)
        try:
            llm = ask_llm(items, model)
        except Exception as e:                  # Ollama not running etc. -> rules only
            log(f"  ! AI 判定を使えませんでした({e.__class__.__name__})。キーワード判定のみ行います")
    out = {}
    for i, x in enumerate(items):
        rule = AD_RE.search(x["text"])
        if i in llm or rule:
            out[i] = llm.get(i) or f"キーワード「{rule.group(0).strip()}」"
    return out, bool(llm) or not use_llm


def split_clauses(x, words):
    """Split one sentence at commas/semicolons into timed clauses (needs word indices)."""
    a, b = x.get("wi", (0, -1))
    parts, cur = [], []
    for w in words[a:b + 1]:
        cur.append(w)
        if w["w"][-1:] in ",;":
            parts.append(cur); cur = []
    if cur:
        parts.append(cur)
    return [{"s": round(p[0]["s"], 2), "e": round(p[-1]["e"], 2), "text": " ".join(w["w"] for w in p),
             "wi": [a + sum(len(q) for q in parts[:k]), a + sum(len(q) for q in parts[:k + 1]) - 1]}
            for k, p in enumerate(parts)]


def classify(sentences, model, log=print, words=None):
    """Mark sentence["cut"]/["reason"]. LLM if available, keyword rules always.
    A flagged sentence that mixes speech and an ad ("...wish to find, thanks for
    watching") is split at its commas and the parts are judged on their own."""
    verdict, llm_ok = _judge(sentences, model, log)
    out = []
    for i, x in enumerate(sentences):
        x.update(cut=i in verdict, reason=verdict.get(i, ""))
        parts = split_clauses(x, words) if (x["cut"] and words and len(x["text"].split()) >= 8) else []
        if len(parts) >= 2:
            sub, _ = _judge(parts, model, log, use_llm=llm_ok)
            # a short lead-in / tail glued to the ad in the same sentence ("Quick break, <ad>",
            # "If this video helped you, <like request>") belongs to the ad
            for k, p in enumerate(parts):
                beside_ad = (k - 1 in sub) or (k + 1 in sub)
                if k not in sub and beside_ad and (len(p["text"].split()) <= 4 or CUE_RE.search(p["text"])):
                    sub[k] = "宣伝の前後のつなぎ"
            if sub and len(sub) < len(parts):         # only part of it is an ad: keep the rest
                for k, p in enumerate(parts):
                    p.update(cut=k in sub, reason=sub.get(k, ""), auto=k in sub)
                out.extend(parts)
                continue
        x["auto"] = x["cut"]
        out.append(x)
    absorb_islands(out)
    sentences[:] = out
    return sentences


def absorb_islands(sentences, max_len=3.0, max_words=6):
    """A tiny run of kept fragments squeezed between cuts, or before the first / after
    the last cut ("Hey guys, before we start," + ad), is part of the ad: cut it too."""
    n, i = len(sentences), 0
    while i < n:
        if sentences[i]["cut"]:
            i += 1
            continue
        j = i
        while j < n and not sentences[j]["cut"]:
            j += 1
        run = sentences[i:j]                      # kept run i..j-1
        before_cut, after_cut = i > 0, j < n      # neighbours are cuts (or the edge)
        if (before_cut or after_cut) and run[-1]["e"] - run[0]["s"] <= max_len \
                and sum(len(x["text"].split()) for x in run) <= max_words:
            for x in run:
                x.update(cut=True, auto=True, reason="宣伝の前後のつなぎ")
        i = j


# ---------------------------------------------------------------- ranges / trimming
def keep_ranges(sentences, total, pad_before=0.15, pad_after=0.3):
    """Time ranges to keep. Each run of cut sentences removes the span from the end of
    the previous kept sentence to the start of the next one (so the pause goes too)."""
    if not sentences or not any(x["cut"] for x in sentences):
        return [(0.0, total)]
    keep, i, n = [], 0, len(sentences)
    pos = 0.0                                   # start of the current keep range
    while i < n:
        if not sentences[i]["cut"]:
            i += 1
            continue
        j = i
        while j < n and sentences[j]["cut"]:
            j += 1
        # padding keeps the speech's last/first syllable, but never reaches into the ad itself
        cut_from = min(sentences[i - 1]["e"] + pad_after, sentences[i]["s"]) if i > 0 else 0.0
        cut_to = max(sentences[j]["s"] - pad_before, sentences[j - 1]["e"]) if j < n else total
        if cut_from > pos:
            keep.append((pos, cut_from))
        pos = max(pos, cut_to)
        i = j
    if pos < total:
        keep.append((pos, total))
    return [(round(a, 3), round(b, 3)) for a, b in keep if b - a > 0.05]


def cut_word_indices(sentences):
    """Word indices that belong to cut sentences (by the sentence's word index span)."""
    out = set()
    for x in sentences:
        if x.get("cut") and x.get("wi"):
            out.update(range(x["wi"][0], x["wi"][1] + 1))
    return out


def remap_words(words, ranges, drop=()):
    """Shift word timestamps onto the trimmed timeline; drop words that were cut.
    `drop` = indices of words in cut sentences: removed even when Whisper gave them a
    timestamp inside a kept range (it collapses hallucinated phrases such as
    "see you in the next video" onto the next real word's time)."""
    out, offset = [], 0.0
    drop = set(drop)
    for a, b in ranges:
        for k, w in enumerate(words):
            if k in drop:
                continue
            mid = (w["s"] + w["e"]) / 2
            if a <= mid < b:
                out.append({"w": w["w"], "s": round(max(w["s"], a) - a + offset, 3),
                            "e": round(min(w["e"], b) - a + offset, 3)})
        offset += b - a
    return out


def trim_audio(src, ranges, dest, fade=0.012):
    """Keep only `ranges` of src, joined back to back (cut parts are removed, never
    replaced by silence). Each piece gets a tiny fade in/out so joins don't click."""
    parts, labels = [], []
    for k, (a, b) in enumerate(ranges):
        f = min(fade, (b - a) / 4)
        parts.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={f:.3f},afade=t=out:st={max(0.0, b - a - f):.3f}:d={f:.3f}[p{k}]")
        labels.append(f"[p{k}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(ranges)}:v=0:a=1[out]"
    script = Path(dest).with_suffix(".filter.txt")      # long graphs: pass via file
    script.write_text(graph)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-/filter_complex", str(script),
                    "-map", "[out]", str(dest)], check=True)
    script.unlink(missing_ok=True)


def silences(audio, min_len=1.2, noise="-40dB"):
    """Silent stretches (start, end) by level (ffmpeg silencedetect)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(audio), "-af", f"silencedetect=noise={noise}:d={min_len}",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    out = list(zip(starts, ends))
    if len(starts) > len(ends):                      # silence running to the end of the file
        out.append((starts[-1], float("inf")))
    return [(max(0.0, a), b) for a, b in out]


def tighten_ranges(ranges, words, total, max_pause=1.2, pad=0.35, head=0.25, tail=0.6, drop=(), quiet=()):
    """Remove long stretches where nobody speaks (e.g. music-only interludes that became
    silence after Demucs) from the keep `ranges`. A pause longer than max_pause keeps
    `pad` seconds on each side; leading/trailing silence keeps head/tail seconds.
    Word timestamps decide what is speech (more reliable than a level threshold)."""
    spoken = [(w["s"], w["e"]) for k, w in enumerate(words) if k not in set(drop) and w["e"] > w["s"]]
    gaps = []
    if spoken:
        first, last = spoken[0][0], max(e for _, e in spoken)
        if first > head + 0.05:
            gaps.append((0.0, first - head))
        for (_, e1), (s2, _) in zip(spoken, spoken[1:]):
            if s2 - e1 > max_pause:
                gaps.append((e1 + pad, s2 - pad))
        if total - last > tail + 0.05:
            gaps.append((last + tail, total))
    # stretches that are silent by level too (no words at all, or Whisper "heard" words
    # in silence - those hallucinated words then also leave the subtitles)
    for a, b in quiet:
        b = min(b, total)
        if b - a > max_pause:
            gaps.append((0.0 if a <= 0.05 else a + pad, total if b >= total - 0.05 else b - pad))
    if not gaps:
        return ranges
    out = []
    for a, b in ranges:
        pieces = [(a, b)]
        for g1, g2 in gaps:
            nxt = []
            for x, y in pieces:
                if g2 <= x or g1 >= y:
                    nxt.append((x, y)); continue
                if g1 > x:
                    nxt.append((x, g1))
                if g2 < y:
                    nxt.append((g2, y))
            pieces = nxt
        out += [(round(x, 3), round(y, 3)) for x, y in pieces if y - x > 0.05]
    return out


# ---------------------------------------------------------------- public API
def content_hash(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha1").hexdigest()


def reuse_cache(mp3, work, log=print):
    """If another work dir already holds the transcript / separation of the same
    audio content (e.g. the file was uploaded again under another name), copy it.
    Index: tmp/content_index.json {sha1: work dir name}."""
    try:
        digest = content_hash(mp3)
    except OSError:
        return False
    index_path = mv.TMP / "content_index.json"
    with mv.file_lock("content_index"):
        index = mv.read_json(index_path, {})
        twin_name = index.get(digest)
        if twin_name != work.name:
            index[digest] = work.name
            mv.write_json(index_path, index)
    if not twin_name or twin_name == work.name:
        return False
    twin = mv.TMP / twin_name
    if not twin.is_dir() or any((work / n).exists() for n in CONTENT_CACHE):
        return False
    copied = []
    for n in CONTENT_CACHE:
        if (twin / n).exists():
            shutil.copy2(twin / n, work / n); copied.append(n)
    if copied:
        log("  同じ内容の音声の解析結果を再利用します")
    return bool(copied)


def source_audio(mp3, work, s, log=print):
    """Voice-only audio if music removal is on, else the original file.
    Inputs that turn out to be voice-only already (Demucs finds almost no music)
    keep the original file; that verdict is cached in music.json."""
    if not s["remove_music"]:
        return mp3
    voice = work / "voice.wav"
    info = mv.read_json(work / "music.json", {})
    if info.get("voice_only"):
        return mp3
    if voice.exists():
        return voice
    voice = mv.isolate_voice(mp3, work, log)
    info = mv.read_json(work / "music.json", {})
    if info.get("db") is not None and info["db"] < VOICE_ONLY_DB:
        mv.write_json(work / "music.json", {**info, "voice_only": True})
        voice.unlink(missing_ok=True)
        log(f"  音楽はほぼ含まれていません({info['db']:.0f} dB)。元の音声をそのまま使います")
        return mp3
    return voice


def analysis_path(work):
    return work / "analysis.json"


def analyze(mp3, work, s, log=print, force=False):
    """Steps 1-3. Cached in analysis.json (keyed by the source used)."""
    path = analysis_path(work)
    work.mkdir(parents=True, exist_ok=True)
    reuse_cache(Path(mp3), work, log)
    src = source_audio(mp3, work, s, log)
    kind = "voice" if Path(src) != Path(mp3) else "original"
    old = mv.read_json(path, None)
    if old and old.get("source") == kind and not force:
        return old
    log("  文字起こし中(Whisper)...")
    words_path = work / f"words_{kind}.json"
    words = mv.transcribe(src, words_path, s["whisper_model"])
    sentences = sentences_from_words(words)
    if s["trim_ads"]:
        log(f"  宣伝・告知をチェック中(AI: {s['llm_model']})...")
        classify(sentences, s["llm_model"], log, words)
    else:
        for x in sentences:
            x.update(cut=False, reason="", auto=False)
    data = {"source": kind, "total": mv.duration(src), "sentences": sentences,
            "music_db": mv.read_json(work / "music.json", {}).get("db")}
    mv.write_json(path, data)
    n = sum(x["cut"] for x in sentences)
    log(f"  宣伝・告知: {n} 文" if n else "  宣伝・告知は見つかりませんでした")
    return data


def prepare(mp3, work, s, log=print):
    """Return (audio_path, words) the video should be built on (after trimming)."""
    data = analyze(mp3, work, s, log)
    src = source_audio(mp3, work, s, log)
    words = mv.read_json(work / f"words_{data['source']}.json", [])
    ranges = keep_ranges(data["sentences"], data["total"]) if s["trim_ads"] else [(0.0, data["total"])]
    if not ranges:
        raise RuntimeError("すべての文がカット対象になっています。確認画面で残す部分を選んでください")
    drop = cut_word_indices(data["sentences"]) if s["trim_ads"] else set()
    ad_removed = data["total"] - sum(b - a for a, b in ranges)
    if s.get("tighten_pauses", True):
        mp = float(s.get("max_pause", 1.2))
        tight = tighten_ranges(ranges, words, data["total"], mp, drop=drop, quiet=silences(src, mp))
        if sum(b - a for a, b in tight) >= 1.0:      # all silent (no speech at all): leave it as it is
            ranges = tight
    if ranges == [(0.0, data["total"])]:
        return src, words
    final = work / "final.wav"
    trim_audio(src, ranges, final)
    removed = data["total"] - sum(b - a for a, b in ranges)
    if ad_removed > 0.05:
        log(f"  宣伝・告知など {ad_removed:.1f} 秒をカットしました")
    if removed - ad_removed > 0.05:
        log(f"  長い無音(間)を {removed - ad_removed:.1f} 秒詰めました")
    return final, remap_words(words, ranges, drop)
