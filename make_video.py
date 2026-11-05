#!/usr/bin/env python3.11
"""Motiversity-style video generator.

Speech MP3 -> cinematic stock-footage montage (Pexels) with colour grade,
vignette, fast cuts, fades and burned-in subtitles (kinetic-captions or simple).
Each video takes fresh clips from 素材/stock/ and moves them to 素材/stock_used/;
missing clips are downloaded from Pexels on demand, so clips are not reused.
All look/feel options live in settings.json (edit them in the web UI: app.py).

Usage:
  venv/bin/python make_video.py "speech.mp3"         # one file
  venv/bin/python make_video.py /path/to/folder       # every mp3 in folder
Options:
  --settings FILE  settings JSON (default: ./settings.json)
  --offline        only use already-downloaded clips
  --watch          keep watching the folder for new mp3s
  --out DIR        output folder (default: ./output/videos, the app's single output folder)
After each render (settings "auto_publish" / "qa"): publishing kit + thumbnail
(publish_meta), quality check (qa.py) and the posting folder output/posting.
"""
import argparse, fcntl, hashlib, json, time, os, random, re, shutil, subprocess, sys, tomllib, urllib.parse, urllib.request
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEDIA = HERE / "media"          # every material the videos are made from, in one place
LIB = MEDIA / "stock"           # downloaded Pexels clips (reused across videos)
TMP = HERE / "tmp"
LOCKS = TMP / "locks"           # one lock per mp3 being rendered (parallel workers)
USED = MEDIA / "stock_used"     # clips already used in a video
CONF = HERE / "config"          # settings, screen state, presets, download bookkeeping
PAGES = CONF / "pexels_pages.json"   # next Pexels result page per query
REUSE = CONF / "reuse_counts.json"   # how often each used clip was re-used (offline mode)
SETTINGS = CONF / "settings.json"
DONE = HERE / "output"          # everything the tool produced
OUTPUT = DONE / "videos"        # single finished-video folder (完成動画 + 投稿 pages, auto workers)
POSTING = DONE / "posting"       # ready-to-post folders (make_posting.py), inside the tool folder
CONFIG = Path("/Users/niuredipesh/Desktop/MoneyPrinterTurbo/config.toml")

# Motiversity-style shot pool: training, struggle, nature, city, success.
QUERIES = [
    "boxer training", "athlete sprinting", "man running at night", "runner sunrise",
    "weightlifting gym", "boxing punching bag", "push ups", "athlete exhausted",
    "mountain climber", "hiker summit", "climbing rock", "man walking alone",
    "man thinking dark", "rain window", "storm clouds", "lightning",
    "ocean waves", "sunrise mountains", "city night aerial", "city timelapse",
    "eagle flying", "lion", "fire burning", "football training", "basketball player",
    "swimmer", "cycling mountain", "stairs running", "man in hoodie", "businessman city",
    "chess", "road trip", "desert walking", "victory celebration", "crowd stadium",
]
# extra queries matched from the file title
TITLE_HINTS = {
    "lion": ["lion", "lion roaring"], "warrior": ["martial arts", "warrior"],
    "discipline": ["early morning workout", "alarm clock"], "fall": ["falling", "getting up"],
    "struggle": ["athlete exhausted", "struggle"], "quit": ["tired runner"],
    "opportunity": ["door opening", "sunrise", "road ahead"], "unbroken": ["boxer"],
    "journey": ["road trip", "hiker"], "business": ["businessman city", "office night"],
    "money": ["businessman city", "luxury car"], "champion": ["trophy", "victory celebration"],
}

DEFAULTS = {
    # video
    "aspect": "16:9",            # "16:9" (YouTube) or "9:16" (Shorts/TikTok)
    "cut": 3.5,                  # average seconds per shot
    "bitrate": "6M",
    "fade_in": 1.5, "fade_out": 2.0,
    # colour grade
    "contrast": 1.12, "brightness": -0.04, "saturation": 0.75, "vignette": True,
    # footage
    "offline": False,            # True = never download, only use clips on disk
    "queries": QUERIES,          # Pexels search terms when online
    # audio
    "remove_music": True,        # Demucs: drop the background music, keep only the voice
    "trim_ads": True,
    "tighten_pauses": True,      # remove long silent stretches (pauses > max_pause seconds)
    "max_pause": 1.2,            # cut ads / sponsor reads / subscribe requests (local AI)
    "llm_model": "qwen2.5:7b-instruct",
    # subtitles
    "subs": True,
    "subs_engine": "kinetic",    # "kinetic" (kinetic_captions/) or "simple"
    # kinetic-captions: per-word size / colour / animation decided by the word itself
    "kc_preset": "motiversity",  # bhatt / motiversity / hormozi / clean
    "kc_font": "",               # "" = the 演出's own font; any id from kinetic_captions fonts (incl. Google Fonts)
    "kc_accent_font": "",        # bhatt: font of the key word ("" = Instrument Serif Italic)
    "kc_size": 1.0,              # text size multiplier (0.6 .. 1.6)
    "kc_position": "auto",       # auto / center / lower / top
    "char_max": 22,
    "font": "Impact", "font_size": 78, "uppercase": True,
    "color": "FFFFFF", "outline": 4, "outline_color": "000000", "shadow": 2,
    "highlight": True, "highlight_color": "FFC800",
    "sub_fade_ms": 80, "appear": False,
    "alignment": 2,              # 2 bottom centre, 5 middle centre, 8 top centre
    "margin_v": 110,
    "whisper_model": "base",
    # after each render
    "bgm": True,                 # free background music (incompetech, CC BY) under the voice
    "bgm_volume": 0.22,          # music level before ducking (0-1)
    "bgm_mood": "auto",          # auto / epic / uplifting / dark
    "auto_publish": True,        # publishing kit (titles/description/tags) + thumbnail + posting folder
    "qa": True,                  # quality check (qa.py): length, decode, resolution, subtitles, ads
    "qa_sync": True,             # + subtitle/voice sync check on a 60 s excerpt (Whisper base)
}


def read_json(path, default):
    """Read a JSON file; fall back to `default` if missing or corrupt."""
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as e:
        print(f"! {Path(path).name} could not be read ({e}); using defaults", file=sys.stderr)
        return default


def write_json(path, data):
    """Atomic write: readers never see a half-written file."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def load_settings(path=None):
    """Defaults, overlaid with the saved settings (unknown keys are ignored)."""
    saved = read_json(path or SETTINGS, {})
    s = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    if s["subs_engine"] not in ("kinetic", "simple"):   # SubtitleFX was removed (2026-11-04)
        s["subs_engine"] = "kinetic"
    return s


def save_settings(s, path=None):
    write_json(path or SETTINGS, {k: s[k] for k in DEFAULTS if k in s})


# ---------------------------------------------------------------- finishing look (Settings page)
# One look for every video, applied once to the finished picture (captions drawn later are untouched).
FINISH = CONF / "finish.json"
FINISH_DEFAULTS = {"look": "original", "saturation": 1.0, "contrast": 1.0, "brightness": 0.0, "warmth": 0.0,
                   "art": "genre",            # AI illustrations: "genre" (the genre's own look), "mono" or "color"
                   "transition": "crosszoom",  # between pictures: "crosszoom" or "cut"
                   "zoom_amount": "normal",    # slow push-in inside a picture: none / slow / normal / strong
                   "zoom_focus": "face",       # where the zoom is anchored: face / center / left / right / bottom / auto
                   "transition_speed": "normal"}  # how long two pictures overlap: fast / normal / slow
ZOOM_AMOUNT = {"none": 0.0, "slow": 0.03, "normal": 0.06, "strong": 0.10}
ZOOM_FOCUS = {"face": (0.5, 0.30), "center": (0.5, 0.5), "left": (0.3, 0.45), "right": (0.7, 0.45), "bottom": (0.5, 0.7)}
TRANSITION_SECONDS = {"fast": 0.4, "normal": 0.9, "slow": 1.5}
DRAMA = "curves=m='0/0 0.28/0.2 0.72/0.78 1/1'"          # deeper shadows, brighter highlights
GRAY = "hue=s=0"
LOOKS = {   # name -> (saturation x, contrast x, brightness +, warmth, extra ffmpeg filter): the filters people know from phones
    "original": (1.0, 1.0, 0.0, 0.0, ""),
    "vivid": (1.35, 1.08, 0.0, 0.0, ""),
    "vivid_warm": (1.35, 1.08, 0.0, 0.4, ""),
    "vivid_cool": (1.35, 1.08, 0.0, -0.4, ""),
    "dramatic": (0.85, 1.22, -0.03, 0.0, DRAMA),
    "dramatic_warm": (0.85, 1.22, -0.03, 0.4, DRAMA),
    "dramatic_cool": (0.85, 1.22, -0.03, -0.4, DRAMA),
    "mono": (1.0, 1.0, 0.0, 0.0, GRAY),
    "silvertone": (1.0, 0.95, 0.02, 0.0, GRAY + ",curves=all='0/0.05 1/0.95'"),
    "noir": (1.0, 1.45, -0.06, 0.0, GRAY),
    "warm": (1.1, 1.03, 0.0, 0.45, ""),
    "cool": (1.05, 1.03, 0.0, -0.45, ""),
    "cinema": (1.15, 1.1, -0.02, 0.0, "colorbalance=rs=-0.12:gs=-0.02:bs=0.16:rh=0.16:gh=0.03:bh=-0.12"),
    "sepia": (1.0, 1.05, 0.0, 0.0, "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131"),
}


def load_finish():
    saved = read_json(FINISH, {})
    f = {**FINISH_DEFAULTS, **{k: v for k, v in saved.items() if k in FINISH_DEFAULTS}}
    if f["look"] not in LOOKS:
        f["look"] = "original"
    for k, lo, hi in (("saturation", 0.0, 2.0), ("contrast", 0.6, 1.6), ("brightness", -0.3, 0.3), ("warmth", -1.0, 1.0)):
        try:
            f[k] = max(lo, min(hi, float(f[k])))
        except (TypeError, ValueError):
            f[k] = FINISH_DEFAULTS[k]
    f["art"] = f["art"] if f["art"] in ("genre", "mono", "color") else "genre"
    f["transition"] = f["transition"] if f["transition"] in ("crosszoom", "cut") else "crosszoom"
    f["zoom_amount"] = f["zoom_amount"] if f["zoom_amount"] in ZOOM_AMOUNT else "normal"
    f["zoom_focus"] = f["zoom_focus"] if f["zoom_focus"] in (*ZOOM_FOCUS, "auto") else "face"
    f["transition_speed"] = f["transition_speed"] if f["transition_speed"] in TRANSITION_SECONDS else "normal"
    return f


def save_finish(f):
    write_json(FINISH, {k: f[k] for k in FINISH_DEFAULTS if k in f})


def finish_filter(f):
    """ffmpeg filter chain for the look + sliders ('' when nothing changes)."""
    sat, con, bri, warm, extra = LOOKS[f["look"]]
    sat, con, bri = sat * f["saturation"], con * f["contrast"], bri + f["brightness"]
    warm = max(-1.0, min(1.0, warm + f["warmth"]))
    parts = [extra] if extra else []
    if abs(sat - 1) > 0.01 or abs(con - 1) > 0.01 or abs(bri) > 0.002:
        parts.append(f"eq=saturation={sat:.3f}:contrast={con:.3f}:brightness={bri:.3f}")
    if abs(warm) > 0.01:                 # warm = more red, less blue (cool the other way round)
        w = 0.14 * warm
        parts.append(f"colorbalance=rs={w / 2:.3f}:bs={-w / 2:.3f}:rm={w:.3f}:bm={-w:.3f}:rh={w:.3f}:bh={-w:.3f}")
    return ",".join(parts)


def frame_size(s):
    return (1080, 1920) if s["aspect"] == "9:16" else (1920, 1080)


def grade_filter(s):
    w, h = frame_size(s)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,"
          f"eq=contrast={s['contrast']}:brightness={s['brightness']}:saturation={s['saturation']},"
          "colorbalance=rs=-0.03:bs=0.04:rh=0.03")
    if s["vignette"]:
        vf += ",vignette=PI/4.5"
    if s.get("grain"):                   # film grain (script videos: monochrome look)
        vf += ",noise=alls=10:allf=t"
    return vf + ",format=yuv420p"


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(out.strip())


def pexels_key():
    """Pexels API key: $PEXELS_API_KEY, motiversity_auto/.env, or MoneyPrinterTurbo's config."""
    if os.environ.get("PEXELS_API_KEY"):
        return os.environ["PEXELS_API_KEY"].strip()
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("PEXELS_API_KEY=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip().strip('"\'')
    if CONFIG.exists():
        keys = tomllib.load(open(CONFIG, "rb"))["app"].get("pexels_api_keys") or []
        if keys:
            return keys[0]
    raise RuntimeError("Pexels の API キーがありません(motiversity_auto/.env に PEXELS_API_KEY=... を書いてください)")


def has_pexels_key():
    try:
        return bool(pexels_key())
    except Exception:
        return False


# ---------------------------------------------------------------- footage
# 素材/stock/<query>/<id>.mp4       not used yet  (fresh)
# 素材/stock_used/<query>/<id>.mp4  already used in a video
# A video takes clips from 素材/stock/ and moves them to 素材/stock_used/.
# When 素材/stock/ runs short, new clips are fetched from Pexels on demand
# (next result page per query, skipping every id we already have).

def _is_clip(p):
    return p.suffix == ".mp4" and p.stat().st_size > 200_000


def library_pool():
    """Fresh (unused) clips."""
    return [c for c in LIB.glob("*/*.mp4") if _is_clip(c)]


def used_pool():
    return [c for c in USED.glob("*/*.mp4") if _is_clip(c)]


def known_ids():
    return {p.stem for root in (LIB, USED) for p in root.glob("*/*.mp4")}


def pexels_search(query, key, page, per_page=15):
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
        {"query": query, "orientation": "landscape", "per_page": per_page, "page": page, "size": "medium"})
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            print(f"  ! pexels '{query}' (try {attempt + 1}): {e}", flush=True)
            time.sleep(3 * (attempt + 1))
    return None


def fetch_new(query, key, want, log=print):
    """Download up to `want` clips for `query` that we have never had before."""
    folder = LIB / query.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    pages = read_json(PAGES, {})
    page, got, seen = pages.get(query, 1), 0, known_ids()
    for _ in range(5):                              # at most 5 result pages per call
        data = pexels_search(query, key, page)
        if not data or not data.get("videos"):
            page = 1; break                         # results exhausted: start over next time
        for v in data["videos"]:
            if got >= want:
                break
            if str(v["id"]) in seen or v.get("duration", 0) < 4:
                continue
            files = [f for f in v["video_files"] if f.get("width") and 1280 <= f["width"] <= 1920
                     and f.get("file_type") == "video/mp4"]
            if not files:
                continue
            f = min(files, key=lambda f: f["width"])  # smallest HD (>=1280) keeps downloads fast
            dest = folder / f"{v['id']}.mp4"
            try:
                req = urllib.request.Request(f["link"], headers={"User-Agent": "Mozilla/5.0"})
                part = dest.with_suffix(".part")
                part.write_bytes(urllib.request.urlopen(req, timeout=90).read())
                part.rename(dest)
                seen.add(str(v["id"])); got += 1
            except Exception as e:
                log(f"  ! download {v['id']}: {e}")
        if got >= want:
            break
        page += 1
    with file_lock("pages"):
        pages = read_json(PAGES, {}); pages[query] = page; write_json(PAGES, pages)
    return got


def top_up(n, title, queries, log=print):
    """Make sure 素材/stock/ holds at least n fresh clips, downloading what's missing."""
    missing = n - len(library_pool())
    if missing <= 0:
        return
    key = pexels_key()
    t = title.lower()
    qs = [q for k, extra in TITLE_HINTS.items() if k in t for q in extra]
    qs += random.sample(queries, len(queries))
    qs = list(dict.fromkeys(qs))
    per_query = max(2, -(-missing // min(len(qs), 12)))   # spread over ~12 queries for variety
    log(f"  素材が {missing} 本足りないので Pexels からダウンロードします")
    for q in qs:
        missing = n - len(library_pool())
        if missing <= 0:
            break
        got = fetch_new(q, key, min(per_query, missing), log)
        log(f"  + {q}: {got} 本")


@contextmanager
def file_lock(name):
    """Cross-process lock (parallel workers share the library)."""
    with open(TMP / f"{name}.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield


def take_clips(n, title, s, log=print):
    """Reserve n distinct fresh clips: move them to 素材/stock_used/ and return their paths.
    Online: tops up 素材/stock/ from Pexels first. Offline: falls back to used clips."""
    TMP.mkdir(exist_ok=True)
    for attempt in range(3):
        if not s["offline"]:
            if has_pexels_key():
                top_up(n, title, s["queries"], log)
            else:
                log("  Pexels の API キーがないため、手元の素材だけで作ります")
                s = {**s, "offline": True}
        with file_lock("library"):
            fresh = library_pool()
            if len(fresh) >= n or s["offline"] or attempt == 2:
                random.shuffle(fresh)
                picks = []
                for c in fresh[:n]:
                    dest = USED / c.parent.name / c.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(c, dest)
                    picks.append(dest)
                if len(picks) < n:   # not enough fresh clips: reuse used ones (still distinct)
                    # least re-used first, so repeats are spread as thin as possible
                    counts = read_json(REUSE, {})
                    extra = [c for c in used_pool() if c not in picks]
                    random.shuffle(extra)
                    extra.sort(key=lambda c: counts.get(c.name, 0))
                    more = extra[:n - len(picks)]
                    for c in more:
                        counts[c.name] = counts.get(c.name, 0) + 1
                    write_json(REUSE, counts)
                    picks += more
                    log(f"  未使用の素材が足りないため、使用済み素材(使用回数の少ない順)も使います")
                return picks
        # another worker took clips meanwhile: download again
    return []


# ---------------------------------------------------------------- subtitles

_whisper = {}


def whisper_model(name):
    """Load a Whisper model once per process (per model name)."""
    if name not in _whisper:
        import whisper
        _whisper[name] = whisper.load_model(name)
    return _whisper[name]


# Whisper on the Apple GPU (mlx-audio in tools/dia_venv, the folder keeps its old name): same weights, ~10x faster than
# openai-whisper on the CPU (2 min of speech: small 4 s vs 41 s, base 3 s vs 17 s on an M5).
# MV_STT=cpu forces the CPU path; any failure falls back to it.
GPU_STT_PY = HERE / "tools" / "dia_venv" / "bin" / "python"
GPU_STT = HERE / "tools" / "stt.py"
GPU_STT_MODELS = {"tiny": "mlx-community/whisper-tiny-asr-fp16", "base": "mlx-community/whisper-base-asr-fp16",
                  "small": "mlx-community/whisper-small-asr-fp16"}


def gpu_whisper_words(audio, model):
    if os.environ.get("MV_STT") == "cpu" or model not in GPU_STT_MODELS:
        return None
    if not (GPU_STT_PY.exists() and GPU_STT.exists()):
        return None
    try:
        r = subprocess.run([str(GPU_STT_PY), str(GPU_STT), GPU_STT_MODELS[model], str(audio)],
                           capture_output=True, text=True, timeout=3600)
        line = next((l for l in r.stdout.splitlines() if l.startswith("[")), None)
        return json.loads(line) if r.returncode == 0 and line else None
    except Exception:
        return None


def whisper_words(audio, model, offset=0.0):
    words = gpu_whisper_words(audio, model)
    if words is not None:
        return [{**w, "s": round(w["s"] + offset, 3), "e": round(w["e"] + offset, 3)} for w in words]
    res = whisper_model(model).transcribe(str(audio), language="en", word_timestamps=True, fp16=False)
    return [{"w": w["word"].strip(), "s": round(float(w["start"]) + offset, 3),
             "e": round(float(w["end"]) + offset, 3)}
            for seg in res["segments"] for w in seg.get("words", []) if w["word"].strip()]


def fill_gaps(audio, words, total, model, scratch, min_gap=2.5, min_db=-38.0):
    """Whisper sometimes skips a whole stretch (often the last part, or a different
    voice such as an ad read). Re-transcribe any long untranscribed stretch that
    still contains sound, and merge the words back in."""
    starts = [w["s"] for w in words] + [total]
    ends = [0.0] + [w["e"] for w in words]
    extra = []
    for a, b in zip(ends, starts):
        if b - a < min_gap:
            continue
        clip = scratch / "gap.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                        "-i", str(audio), str(clip)], check=True)
        if mean_volume(clip) > min_db:
            extra += [w for w in whisper_words(clip, model, offset=a) if a <= w["s"] < b]
        clip.unlink(missing_ok=True)
    return sorted(words + extra, key=lambda w: w["s"])


def transcribe(audio, cache, model):
    """Word-level timestamps from Whisper (cached as JSON), with skipped parts filled in."""
    if cache.exists():
        return json.loads(cache.read_text())
    words = whisper_words(audio, model)
    words = fill_gaps(audio, words, duration(audio), model, Path(cache).parent)
    cache.write_text(json.dumps(words))
    return words


def ass_time(t):
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_color(hex6):
    """'RRGGBB' -> ASS '&H00BBGGRR'."""
    h = hex6.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_safe(text):
    """Drop characters ASS treats as markup ({override}, \\N...) from transcript text."""
    return text.replace("{", "").replace("}", "").replace("\\", "").strip()


def write_simple_ass(words, path, s, max_words=5):
    """Short, bold, centred captions (no external deps)."""
    w_, h_ = frame_size(s)
    lines, chunk = [], []
    for i, w in enumerate(words):
        chunk.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end_of_phrase = w["w"][-1:] in ".,!?;:" or (nxt and nxt["s"] - w["e"] > 0.6)
        if len(chunk) >= max_words or end_of_phrase or nxt is None:
            text = ass_safe(" ".join(x["w"] for x in chunk))
            text = text.upper() if s["uppercase"] else text
            end = min(chunk[-1]["e"] + 0.25, nxt["s"]) if nxt else chunk[-1]["e"] + 0.5
            lines.append(f"Dialogue: 0,{ass_time(chunk[0]['s'])},{ass_time(end)},Main,,0,0,0,,{text}")
            chunk = []
    path.write_text(
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w_}\nPlayResY: {h_}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Main,{s['font']},{s['font_size']},{ass_color(s['color'])},&H00FFFFFF,"
        f"{ass_color(s['outline_color'])},&H96000000,0,0,0,0,100,100,2,0,1,"
        f"{s['outline']},{s['shadow']},{s['alignment']},120,120,{s['margin_v']},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(lines) + "\n")


KC_DIR = HERE / "kinetic_captions"
KC_PY = KC_DIR / ".venv" / "bin" / "python"
KC_SETUP = "uv venv --python 3.11 kinetic_captions/.venv && uv pip install --python kinetic_captions/.venv/bin/python -r kinetic_captions/requirements.txt"


# ---------------------------------------------------------------- memory guard
# Everything heavy (CosyVoice, FLUX, Whisper, CLIP, the local LLM) shares the Mac's unified
# memory. Running short means swap, i.e. writes to the SSD; these helpers keep the
# heavy steps from starting while memory is low and unload the LLM before them.

def mem_free_pct():
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=10).stdout
        return int(re.search(r"free percentage:\s*(\d+)", out).group(1))
    except Exception:
        return 100


def swap_used_mb():
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
        return float(re.search(r"used = ([\d.]+)M", out).group(1))
    except Exception:
        return 0.0


def unload_llm(model=None):
    """Drop the local LLM from memory now (Ollama keeps it loaded otherwise)."""
    model = model or load_settings().get("llm_model")
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                     json.dumps({"model": model, "keep_alive": 0}).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def memory_gate(log=print, need=40, wait=600):
    """Before a heavy GPU step: unload the LLM and wait until at least `need` % of memory
    is free (another app may be holding it). Returns True once there is room."""
    unload_llm()
    t0, told = time.time(), False
    while mem_free_pct() < need:
        if not told:
            log(f"  メモリの空きが少ないので待っています(空き {mem_free_pct()}%、{need}% になったら再開)。"
                "ほかのアプリを閉じると早く進みます")
            told = True
        if time.time() - t0 > wait:
            log("  ! メモリが空かないまま続けます(スワップが増えるかもしれません)")
            return False
        time.sleep(5)
    return True


def kinetic_available():
    return KC_PY.exists() and (KC_DIR / "mv_bridge.py").exists()


def burn_kinetic(video, words, out, s, work, log=print):
    """Kinetic captions (kinetic_captions/, own venv) drawn onto a finished video.
    Uses our word timings as they are (no second transcription)."""
    if not kinetic_available():
        raise RuntimeError("kinetic-captions の環境がありません。motiversity_auto で次を実行してください: " + KC_SETUP)
    work, video, out = Path(work).resolve(), Path(video).resolve(), Path(out).resolve()
    work.mkdir(parents=True, exist_ok=True)
    job = work / "kc_job.json"          # the bridge runs in kinetic_captions/: absolute paths only
    write_json(job, {"video": str(video), "out": str(out), "work": str(work / "kc"),
                     "words": [w for w in words if str(w.get("w", "")).strip()],
                     "opts": {"preset": s.get("kc_preset", "motiversity"),
                              "font": s.get("kc_font", ""),
                              "accent_font": s.get("kc_accent_font", ""),
                              "size": float(s.get("kc_size", 1.0)),
                              "text_color": "#" + str(s.get("color", "FFFFFF")).lstrip("#"),
                              "accent_color": ("#" + str(s["highlight_color"]).lstrip("#"))
                              if s.get("highlight") and s.get("kc_preset") == "hormozi" else None,
                              "position": s.get("kc_position", "auto")}})
    proc = subprocess.Popen([str(KC_PY), str(KC_DIR / "mv_bridge.py"), str(job)], cwd=KC_DIR,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail, shown = [], -1
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("PROGRESS "):
            pct = int(float(line.split()[1]) * 100)
            if pct // 10 > shown:            # log every 10 %
                shown = pct // 10
                log(f"  字幕 {pct}%")
        elif line and line != "DONE":
            tail = (tail + [line])[-15:]
            if not line.startswith((" ", "Traceback", "File")):
                log("  " + line)
    if proc.wait() != 0 or not Path(out).exists():
        raise RuntimeError("kinetic-captions の書き出しに失敗しました: " + " / ".join(tail[-3:]))
    return Path(out)


# ---------------------------------------------------------------- render

def lock_path(mp3):
    return LOCKS / (hashlib.md5(str(mp3).encode()).hexdigest() + ".lock")


def claim(mp3):
    """Exclusive per-file lock so several workers can share one folder."""
    LOCKS.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(lock_path(mp3), os.O_CREAT | os.O_EXCL))
        return True
    except FileExistsError:
        return False


def make(mp3, outdir, s, log=print):
    """Render one mp3. Returns the output path, or None if skipped/failed."""
    mp3 = Path(mp3)
    out = Path(outdir) / (mp3.stem + ".mp4")
    if out.exists() or not claim(mp3):
        return None
    try:
        return _make(mp3, out, s, log)
    except Exception as e:
        log(f"  FAILED {mp3.name}: {e}")
        return None
    finally:
        lock_path(mp3).unlink(missing_ok=True)


def mean_volume(path):
    """Mean loudness in dB (ffmpeg volumedetect); -91 for silence/unknown."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    m = [l for l in err.splitlines() if "mean_volume" in l]
    return float(m[0].split(":")[1].split()[0]) if m else -91.0


def isolate_voice(mp3, work, log):
    """Strip the background music with Demucs and keep only the speech (cached)."""
    voice = work / "voice.wav"
    if voice.exists():
        return voice
    log("  音楽を除去して声だけにしています(Demucs)...")
    sep = work / "demucs"
    for device in ("mps", "cpu"):     # Apple GPU first; CPU if it fails (e.g. out of memory)
        try:
            subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
                            "-d", device, "-o", str(sep), "--filename", "{stem}.{ext}", str(mp3)],
                           check=True, capture_output=True, text=True)
            break
        except subprocess.CalledProcessError as e:
            log(f"  ! demucs ({device}) failed: {e.stderr.strip().splitlines()[-1:]}")
    else:
        raise RuntimeError("音楽の除去に失敗しました")
    os.replace(sep / "htdemucs" / "vocals.wav", voice)
    write_json(work / "music.json", {"db": mean_volume(sep / "htdemucs" / "no_vocals.wav")})
    shutil.rmtree(sep, ignore_errors=True)
    return voice


def work_dir(mp3):
    """Per-audio cache dir (hash of the file name: safe inside ffmpeg filters)."""
    return TMP / hashlib.md5(Path(mp3).stem.encode()).hexdigest()[:12]


def _make(mp3, out, s, log):
    import prep
    log(f"== {mp3.name}")
    work = work_dir(mp3)
    work.mkdir(parents=True, exist_ok=True)
    # voice only (Demucs) -> transcript -> ads/intros cut out (prep.py)
    if s["subs"] or s["trim_ads"]:
        audio, words = prep.prepare(mp3, work, s, log)
    else:
        audio, words = prep.source_audio(mp3, work, s, log), []
    total = duration(audio)
    # plan shot lengths first, then take that many distinct clips
    cut, lengths, t = float(s["cut"]), [], 0.0
    while total - t >= 0.5:
        d = min(round(random.uniform(cut * 0.7, cut * 1.3), 2), total - t)
        lengths.append(d); t += d
    clips = take_clips(len(lengths), mp3.stem, s, log)
    if not clips:
        raise RuntimeError("素材がありません(オフライン設定で素材が0本の可能性)")
    log(f"  素材 {len(clips)} 本を確保(未使用 残り {len(library_pool())} 本)")

    grade = grade_filter(s)
    segs = []
    for n, d in enumerate(lengths):
        src = clips[n % len(clips)]
        for attempt in range(3):           # unreadable clip: take one replacement
            try:
                start = random.uniform(0, max(0, duration(src) - d - 0.1))
                seg = work / f"{n:04d}.mp4"
                run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{d:.2f}", "-i", str(src), "-an",
                     "-vf", grade, "-c:v", "h264_videotoolbox", "-b:v", s["bitrate"], str(seg)])
                segs.append(seg)
                break
            except (ValueError, subprocess.CalledProcessError):
                src = (take_clips(1, mp3.stem, s, log) or [src])[0]
        if n % 10 == 0 or n == len(lengths) - 1:
            log(f"  shots {len(segs)}/{len(lengths)}")

    # fades never longer than a third of the video (short clips would break ffmpeg)
    fi, fo = min(float(s["fade_in"]), total / 3), min(float(s["fade_out"]), total / 3)
    vf = f"fade=t=in:d={fi:.2f},fade=t=out:st={total - fo:.2f}:d={fo:.2f}"
    kinetic = s["subs"] and s["subs_engine"] == "kinetic"
    if s["subs"]:
        ass = work / "subs.ass"
        write_simple_ass(words, ass, s)   # kinetic: plain captions as the QA record, not burned in
        if not kinetic:
            vf += f",ass={ass}"

    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in segs))
    tmp_out = work / "out.mp4"
    log("  encoding final video...")
    af = f"afade=t=out:st={total - fo:.2f}:d={fo:.2f}"
    if s["remove_music"]:
        af = "loudnorm=I=-14:TP=-1.5:LRA=11," + af   # separated voice is quieter: normalise to YouTube loudness (-14 LUFS)
    music = pick_music(total, s, log) if s.get("bgm") else None
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio)]
    if music:
        # free BGM under the voice: ducked (sidechain) while someone speaks, faded in/out,
        # then the whole mix normalised to YouTube loudness
        fc = bgm_filter("1:a", "2:a", total, fo, s)
        cmd += ["-stream_loop", "-1", "-i", music["path"], "-filter_complex", fc, "-vf", vf,
                "-map", "0:v", "-map", "[a]"]
        log(f"  BGM: {music['title']} / {music['artist']}({music['license']})")
    else:
        cmd += ["-vf", vf, "-af", af, "-map", "0:v", "-map", "1:a"]
    run(cmd + ["-c:v", "h264_videotoolbox", "-b:v", s["bitrate"], "-c:a", "aac", "-b:a", "192k",
               "-shortest", str(tmp_out)])
    if kinetic:
        log("  kinetic-captions で字幕を入れています...")
        plain, tmp_out = tmp_out, work / "out_kc.mp4"
        burn_kinetic(plain, words, tmp_out, s, work, log)
        plain.unlink(missing_ok=True)
    tmp_out.rename(out)            # appears in the output folder only when complete
    # what this render used (qa.py checks the video against it)
    write_json(work / "words_render.json", words if s["subs"] else [])
    write_json(work / "render.json", {"video": str(out), "audio": str(audio), "source": str(mp3),
                                      "total": total, "subs": bool(s["subs"]), "aspect": s["aspect"],
                                      "size": list(frame_size(s)), "time": time.time(),
                                      "music": {k: music[k] for k in ("title", "artist", "source", "license", "file")}
                                      if music else None})
    for p in segs:
        p.unlink()
    log(f"  -> {out}")
    return out


# ---------------------------------------------------------------- after render
# publish kit -> quality check -> posting folder. Each step is optional and may fail
# without losing the video: failures become warnings in the returned summary.

def run_publish(video, audio, log=print, force=False):
    """Publishing kit (title/description/tags/thumbnail) via publish_meta.
    Uses publish_kit() when available, else the older process(). True on success."""
    import prep, publish_meta
    prep.ensure_ollama(log)          # titles / descriptions need the local LLM
    kit = getattr(publish_meta, "publish_kit", None)
    if kit:
        ok = bool(kit(Path(video), Path(audio), log=log, force=force))
        if ok:
            apply_music_credit(video, audio, log)
        return ok
    if not force and (Path(video).parent / (Path(video).stem + ".publish.json")).exists():
        return True
    return bool(publish_meta.process(Path(video), Path(audio).parent, load_settings()["llm_model"], log))


def refresh_posting(videos_dir, log=print):
    """Update the ready-to-post folders (incremental; cheap hard links)."""
    import make_posting
    with file_lock("posting"):
        return make_posting.main(Path(videos_dir), POSTING, log=log)


def bgm_filter(voice, bgm, total, fade_out, s):
    """filter_complex: BGM ducked under the voice (sidechain), faded, mix normalised to -14 LUFS -> [a]."""
    vol = float(s.get("bgm_volume", 0.22))
    fo = fade_out
    return (f"[{voice}]aresample=48000,asplit=2[v][key];"
            f"[{bgm}]aresample=48000,atrim=0:{total:.2f},volume={vol:.3f},"
            f"afade=t=in:d=2,afade=t=out:st={max(0.0, total - fo - 1):.2f}:d={fo + 1:.2f}[m];"
            f"[m][key]sidechaincompress=threshold=0.015:ratio=5:attack=20:release=500[md];"
            f"[v][md]amix=inputs=2:duration=first:normalize=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st={max(0.0, total - fo):.2f}:d={fo:.2f}[a]")


def remix_bgm(video, voice, s, log=print, music=None):
    """Swap a finished video's audio for voice + BGM without re-rendering the picture
    (video stream copied: same cuts/subtitles, takes seconds). `voice` is the voice-only
    audio the video was made from. Updates render.json, the description credit (CC BY)
    and returns the music dict (None if no track available)."""
    video, voice = Path(video), Path(voice)
    total = duration(video)
    fo = min(float(s["fade_out"]), total / 3)
    music = music or pick_music(total, s, log)
    if not music:
        return None
    tmp = video.with_name(video.stem + ".remix.mp4")
    run(["ffmpeg", "-y", "-i", str(video), "-i", str(voice), "-stream_loop", "-1", "-i", music["path"],
         "-filter_complex", bgm_filter("1:a", "2:a", total, fo, s),
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", "-movflags", "+faststart", str(tmp)])
    if abs(duration(tmp) - total) > 0.5:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("BGM 入りの長さが元の動画と合いません")
    os.replace(tmp, video)
    work = work_dir(voice)
    work.mkdir(parents=True, exist_ok=True)
    rj = read_json(work / "render.json", {}) or {}
    rj.update(video=str(video), audio=rj.get("audio") or str(voice), total=total,
              music={k: music[k] for k in ("title", "artist", "source", "license", "file")})
    write_json(work / "render.json", rj)
    apply_music_credit(video, voice, log)
    log(f"  BGM を入れました: {music['title']}")
    return music


def pick_music(total, s, log=print):
    """A free BGM track for this video (downloads a few if the library is empty)."""
    try:
        import music as mus
        m = mus.pick(total, s.get("bgm_mood", "auto"))
        if not m:
            log("  BGM の曲がないので、無料音楽(incompetech)をダウンロードします")
            mus.fetch(6, log=log)
            m = mus.pick(total, s.get("bgm_mood", "auto"))
        if not m:
            log("  ! BGM を用意できなかったので、声だけで作ります")
        return m
    except Exception as e:
        log(f"  ! BGM エラー({e})。声だけで作ります")
        return None


def apply_music_credit(video, audio, log=print):
    """BGM is CC BY: its credit must be in the description. Adds it to the kit (idempotent)."""
    rj = read_json(work_dir(audio) / "render.json", {}) or {}
    meta_path = Path(video).parent / (Path(video).stem + ".publish.json")
    meta = read_json(meta_path, None)
    if not isinstance(meta, dict):
        return
    import music as mus
    if rj.get("music"):
        if mus.add_credit(meta, rj["music"]):
            write_json(meta_path, meta)
    elif meta.get("music"):                       # re-rendered without BGM: drop the old credit
        for k in ("description_en_full", "description_ja_full"):
            meta[k] = re.sub(r"\n\n(Music:|音楽:)\n.*?http://creativecommons\.org/licenses/by/4\.0/", "",
                             meta.get(k, ""), flags=re.S)
        meta.pop("music", None)
        write_json(meta_path, meta)
    else:
        return
    try:
        import publish_meta
        (meta_path.parent / (Path(video).stem + ".publish.md")).write_text(publish_meta.markdown(meta))
    except Exception:
        pass


def after_render(video, audio, s, log=print, step=None):
    """Run the automatic steps after a successful render.
    step(stage, message) reports progress (stage: publishing | checking | posting).
    Returns {"publish": ok|error|skipped, "qa": pass|warn|fail|error|skipped, "warnings": [...]}."""
    video, audio = Path(video), Path(audio)
    step = step or (lambda stage, msg: log(msg))
    res = {"publish": "skipped", "qa": "skipped", "warnings": []}
    if s.get("auto_publish", True):
        step("publishing", "公開キット(タイトル・説明文・サムネイル)を作成中…")
        try:
            ok = run_publish(video, audio, log=log)
            res["publish"] = "ok" if ok else "error"
            if not ok:
                res["warnings"].append("公開キットを作れませんでした")
        except Exception as e:
            res["publish"] = "error"
            res["warnings"].append(f"公開キットのエラー: {e}")
        log(f"  公開キット: {res['publish']}")
    if s.get("qa", True):
        step("checking", "品質チェック中(長さ・デコード・字幕)…")
        try:
            import qa
            r = qa.check_video(video, audio, work_dir(audio), log=log, s=s)
            res["qa"] = r["status"]
            if r["status"] != "pass":
                res["warnings"].append("品質チェック: " + " / ".join(r.get("reasons") or [r["status"]]))
        except Exception as e:
            res["qa"] = "error"
            res["warnings"].append(f"品質チェックのエラー: {e}")
        log(f"  品質チェック: {res['qa']}")
    # the posting folder is always refreshed, so every new video shows up in output/posting
    step("posting", "投稿用フォルダを更新中…")
    try:
        refresh_posting(video.parent, log=lambda m: None)
    except Exception as e:
        res["warnings"].append(f"投稿用フォルダの更新エラー: {e}")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--settings", default=str(SETTINGS))
    ap.add_argument("--offline", action="store_true", help="use only already-downloaded clips")
    ap.add_argument("--watch", action="store_true", help="keep checking the folder for new mp3s")
    ap.add_argument("--out", default=str(OUTPUT))
    a = ap.parse_args()
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    p = Path(a.input)
    while True:
        s = load_settings(a.settings)          # re-read so UI changes apply to the next video
        if a.offline:
            s["offline"] = True
        files = sorted(p.glob("*.mp3")) if p.is_dir() else [p]
        say = lambda m: print(m, flush=True)
        for f in files:
            out = make(f, outdir, s, log=say)
            if out:
                r = after_render(out, f, s, log=say)
                for w in r["warnings"]:
                    say(f"  ! {w}")
        if not (a.watch and p.is_dir()):
            break
        time.sleep(60)
