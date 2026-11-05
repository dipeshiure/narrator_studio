"""Narrator Studio — local web app (Flask API + static UI).

Run:  venv/bin/python server.py      ->  http://localhost:8510
Design system: design-system/motiversity-auto/MASTER.md
"""
import glob, json, os, queue, re, shutil, subprocess, sys, threading, time, uuid
from pathlib import Path
from urllib.parse import quote

from flask import Flask, abort, jsonify, make_response, request, send_file, send_from_directory

import make_video as mv
import prep
import server_i18n

HERE = mv.HERE
# One finished-video folder for everything: inbox renders, the 作成 page, auto
# workers, the 完成動画 page and the 投稿 page (publish kits / thumbnails / QA live
# next to each video).
VIDEOS = mv.OUTPUT
UPLOADS = HERE / "uploads"     # uploaded audio waiting to become videos
LOGS = HERE / "logs"
STATIC = HERE / "static"
UI_STATE = mv.CONF / "ui_state.json"
ui_lock = threading.Lock()
DEFAULT_SRC = Path.home() / "Desktop" / "soundcloud_motivational"
WATCH_PATTERN = "make_video.py.*--watch"
for d in (VIDEOS, UPLOADS, LOGS):
    d.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=None)
jobs = {}   # id -> {"status": running|done|error, "log": [...], "result": str|None}


@app.after_request
def localize_json(resp):
    """UI not in Japanese (cookie lang=en/zh/...): server messages come in English."""
    if request.cookies.get("lang", "ja") != "ja" and resp.is_json and not resp.direct_passthrough:
        data = resp.get_json(silent=True)
        if data is not None:
            resp.set_data(json.dumps(server_i18n.localize(data), ensure_ascii=False))
    return resp


def start_job(fn, pass_job=False, **extra):
    """Run fn(log) in a thread; with pass_job=True it gets fn(log, job) so it can update
    its own job dict (batch progress, a cancel flag)."""
    jid = uuid.uuid4().hex[:8]
    job = jobs[jid] = {"status": "running", "log": [], "result": None, **extra}

    def runner():
        try:
            job["result"] = fn(job["log"].append, job) if pass_job else fn(job["log"].append)
            job["status"] = "done" if job["result"] is not False else "error"
        except Exception as e:
            job["log"].append(f"エラー: {e}")
            job["status"] = "error"
    threading.Thread(target=runner, daemon=True).start()
    return jid


def workers_running():
    out = subprocess.run(["pgrep", "-f", WATCH_PATTERN], capture_output=True, text=True).stdout
    return len(out.split())


# ------------------------------------------------------------------ inbox (upload page)
# Uploaded audio lives in inbox/. Each file has a small status record in
# inbox/.meta/<id>.json (id = its work-dir name). Two background threads:
#   analysis: music removal -> transcript -> ad check   (status queued -> analyzing -> ready)
#   render:   build the video from the reviewed plan, then the automatic steps
#             (status waiting -> generating -> publishing -> checking -> done)
BUSY_RUNNING = ("analyzing", "generating", "publishing", "checking")
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".aiff", ".aif"}
meta_lock = threading.Lock()
analysis_q, render_q = queue.Queue(), queue.Queue()
_workers_started = False


def meta_dir():
    d = UPLOADS / ".meta"
    d.mkdir(parents=True, exist_ok=True)
    return d


def item_id(path):
    return mv.work_dir(path).name


def load_meta(iid):
    return mv.read_json(meta_dir() / f"{iid}.json", None)


def update_meta(iid, **kw):
    with meta_lock:
        m = load_meta(iid)
        if m is None:
            return None
        m.update(kw)
        mv.write_json(meta_dir() / f"{iid}.json", m)
        return m


def add_to_inbox(file_storage):
    """Save one uploaded file and queue it for analysis. Returns its meta or None."""
    name = Path(file_storage.filename).name          # strips any folder part / traversal
    if Path(name).suffix.lower() not in AUDIO_EXT or name.startswith("."):
        return None
    dest = UPLOADS / name
    file_storage.save(dest)
    iid = item_id(dest)
    shutil.rmtree(mv.work_dir(dest), ignore_errors=True)   # same name re-uploaded: start fresh
    m = {"id": iid, "name": name, "path": str(dest), "size": dest.stat().st_size,
         "uploaded": time.time(), "status": "queued", "step": "", "error": None, "video": None}
    with meta_lock:
        mv.write_json(meta_dir() / f"{iid}.json", m)
    ensure_workers()
    analysis_q.put(iid)
    return m


def process_analysis(iid):
    m = load_meta(iid)
    if not m or m["status"] not in ("queued", "analyzing"):
        return
    update_meta(iid, status="analyzing", step="準備中", error=None)
    path = Path(m["path"])
    try:
        prep.analyze(path, mv.work_dir(path), mv.load_settings(),
                     log=lambda msg: update_meta(iid, step=msg.strip()), force=True)
        update_meta(iid, status="ready", step="")
    except Exception as e:
        update_meta(iid, status="error", step="", error=f"解析エラー: {e}")


def retire_video(path):
    """Move an existing video out of the way before re-rendering it (never deleted:
    it may be one of the user's finished videos). -> VIDEOS/_replaced/<stem>.<time>.mp4"""
    if not path.exists():
        return None
    dest = VIDEOS / "_replaced" / f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest)
    return dest


STAGES = {"publishing": "publishing", "checking": "checking", "posting": "publishing"}


def finish_render(iid, out, path, s):
    """Publish kit -> QA -> posting folder, with progress in the inbox record.
    A failure here never loses the video: the item ends 'done' with a warning."""
    def stage(name, msg):
        update_meta(iid, status=STAGES.get(name, "publishing"), step=msg)
    try:
        r = mv.after_render(out, path, s, log=lambda m: None, step=stage)
    except Exception as e:
        r = {"publish": "error", "qa": "error", "warnings": [f"後処理のエラー: {e}"]}
    update_meta(iid, status="done", step="", publish=r["publish"], qa=r["qa"],
                warning=" / ".join(r["warnings"]) or None)


def process_render(iid):
    m = load_meta(iid)
    if not m or m["status"] != "waiting":
        return
    path = Path(m["path"])
    old = VIDEOS / (path.stem + ".mp4")
    if m.get("rendered") and m.get("video") == old.name:
        old.unlink(missing_ok=True)        # our own earlier render: regenerate with the latest plan
    else:
        retire_video(old)                  # someone else's video with the same name: keep a copy
    update_meta(iid, status="generating", step="開始", error=None, warning=None, publish=None, qa=None)
    log = []

    def step(msg):
        log.append(msg.strip()); update_meta(iid, step=msg.strip())
    s = mv.load_settings()
    out = mv.make(path, VIDEOS, s, log=step)
    if out:
        update_meta(iid, step="", video=out.name, rendered=True)
        finish_render(iid, out, path, s)
    else:
        why = next((l for l in reversed(log) if "FAILED" in l), "")
        update_meta(iid, status="error", step="", error="動画を作れませんでした " + why.replace(f"FAILED {path.name}:", ""))


def _loop(q, fn):
    while True:
        fn(q.get())


def ensure_workers():
    global _workers_started
    if not _workers_started:
        _workers_started = True
        threading.Thread(target=_loop, args=(analysis_q, process_analysis), daemon=True).start()
        threading.Thread(target=_loop, args=(render_q, process_render), daemon=True).start()


def recover_inbox():
    """On server start: register files dropped into inbox/ and resume unfinished work."""
    old = HERE / "uploads"                     # files uploaded before the inbox existed
    if old.is_dir():
        for f in old.iterdir():
            if f.suffix.lower() in AUDIO_EXT and not (UPLOADS / f.name).exists():
                os.replace(f, UPLOADS / f.name)
    for f in sorted(UPLOADS.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXT and not load_meta(item_id(f)):
            iid = item_id(f)
            mv.write_json(meta_dir() / f"{iid}.json", {
                "id": iid, "name": f.name, "path": str(f), "size": f.stat().st_size,
                "uploaded": f.stat().st_mtime, "status": "queued", "step": "", "error": None,
                "video": (f.stem + ".mp4") if (VIDEOS / (f.stem + ".mp4")).exists() else None})
    ensure_workers()
    for p in sorted(meta_dir().glob("*.json"), key=lambda p: p.stat().st_mtime):
        m = mv.read_json(p, None)
        if not m:
            continue
        if not Path(m["path"]).exists():
            here = UPLOADS / Path(m["path"]).name     # the upload folder moved or was renamed
            if not here.exists():
                p.unlink(); continue
            update_meta(m["id"], path=str(here)); m["path"] = str(here)
        if m["status"] in ("queued", "analyzing"):
            update_meta(m["id"], status="queued"); analysis_q.put(m["id"])
        elif m["status"] in ("waiting", "generating"):
            update_meta(m["id"], status="waiting"); render_q.put(m["id"])
        elif m["status"] in ("publishing", "checking"):     # video is there; only the extras were cut off
            update_meta(m["id"], status="done", step="",
                        warning="再起動で後処理が中断されました(投稿ページの「公開キットを作り直す」で再実行できます)")


def summarize(m):
    """Meta + what the analysis found (length, music, cuts)."""
    a = mv.read_json(mv.work_dir(m["path"]) / "analysis.json", None)
    out = dict(m)
    if a and m["status"] not in ("queued", "analyzing"):
        ranges = prep.keep_ranges(a["sentences"], a["total"])
        cuts = [x for x in a["sentences"] if x["cut"]]
        out.update(duration=a["total"], final=sum(b - c for c, b in ranges),
                   cuts=len(cuts), music_db=a.get("music_db"),
                   cut_preview=[{"s": x["s"], "text": x["text"][:80]} for x in cuts[:3]])
    out["video_exists"] = bool(m.get("video")) and (VIDEOS / m["video"]).exists()
    if out["video_exists"]:
        q = mv.read_json(VIDEOS / (Path(m["video"]).stem + ".qa.json"), None)
        if isinstance(q, dict):
            out["qa"], out["qa_reasons"] = q.get("status"), q.get("reasons", [])
    return out


@app.post("/api/inbox/upload")
def inbox_upload():
    files = request.files.getlist("files")
    added, skipped = [], []
    for f in files:
        m = add_to_inbox(f) if f and f.filename else None
        (added if m else skipped).append(m["name"] if m else Path(f.filename or "?").name)
    return jsonify({"added": added, "skipped": skipped})


@app.get("/api/inbox")
def inbox_list():
    metas = [mv.read_json(p, None) for p in meta_dir().glob("*.json")]
    items = [summarize(m) for m in metas if m and Path(m["path"]).exists()]
    return jsonify(sorted(items, key=lambda m: -m["uploaded"]))


@app.get("/api/inbox/<iid>")
def inbox_item(iid):
    m = load_meta(iid)
    if not m:
        abort(404)
    a = mv.read_json(mv.work_dir(m["path"]) / "analysis.json", {})
    return jsonify({**summarize(m), "sentences": a.get("sentences", [])})


@app.post("/api/inbox/<iid>/sentences")
def inbox_set_cuts(iid):
    """Body {"cut": {"<index>": true|false, ...}} — the review screen's toggles."""
    m = load_meta(iid)
    if not m:
        abort(404)
    path = mv.work_dir(m["path"]) / "analysis.json"
    with meta_lock:
        a = mv.read_json(path, None)
        if not a:
            return jsonify({"error": "まだ解析が終わっていません"}), 409
        for k, v in (request.json or {}).get("cut", {}).items():
            i = int(k)
            if 0 <= i < len(a["sentences"]):
                a["sentences"][i]["cut"] = bool(v)
        mv.write_json(path, a)
    return jsonify(summarize(load_meta(iid)))


@app.post("/api/inbox/<iid>/reanalyze")
def inbox_reanalyze(iid):
    m = load_meta(iid)
    if not m or m["status"] in BUSY_RUNNING:
        return jsonify({"error": "処理中は再解析できません"}), 409
    update_meta(iid, status="queued", error=None)
    ensure_workers(); analysis_q.put(iid)
    return jsonify({"ok": True})


@app.post("/api/inbox/generate")
def inbox_generate():
    ids = (request.json or {}).get("ids", [])
    queued = []
    for iid in ids:
        m = load_meta(iid)
        if m and m["status"] in ("ready", "done", "error") and \
                mv.read_json(mv.work_dir(m["path"]) / "analysis.json", None):
            update_meta(iid, status="waiting", error=None)
            render_q.put(iid); queued.append(iid)
    ensure_workers()
    return jsonify({"queued": queued})


@app.post("/api/inbox/delete")
def inbox_delete():
    deleted, busy = [], []
    for iid in (request.json or {}).get("ids", []):
        m = load_meta(iid)
        if not m:
            continue
        if m["status"] in BUSY_RUNNING:
            busy.append(m["name"]); continue
        Path(m["path"]).unlink(missing_ok=True)
        shutil.rmtree(mv.work_dir(m["path"]), ignore_errors=True)
        (meta_dir() / f"{iid}.json").unlink(missing_ok=True)
        deleted.append(m["name"])
    return jsonify({"deleted": deleted, "busy": busy})


@app.get("/api/inbox/<iid>/audio")
def inbox_audio(iid):
    """Audio the transcript timestamps refer to (voice-only if music was removed)."""
    m = load_meta(iid)
    if not m:
        abort(404)
    voice = mv.work_dir(m["path"]) / "voice.wav"
    return send_file(voice if voice.exists() else m["path"], conditional=True)


# ------------------------------------------------------------------ pages
@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html", max_age=0)


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name, max_age=0)   # revalidate (304) so UI edits show without a hard reload


@app.get("/media/<name>")
def media(name):
    path = (VIDEOS / name).resolve()
    if path.parent != VIDEOS.resolve() or not path.exists():
        abort(404)
    return send_file(path, conditional=True)   # range requests for seeking


# ------------------------------------------------------------------ settings
@app.get("/api/settings")
def get_settings():
    return jsonify(mv.load_settings())


@app.post("/api/settings")
def save_settings():
    s = mv.load_settings()
    s.update({k: v for k, v in request.json.items() if k in mv.DEFAULTS})
    mv.save_settings(s)
    return jsonify(s)


@app.post("/api/settings/reset")
def reset_settings():
    mv.SETTINGS.unlink(missing_ok=True)
    return jsonify(mv.load_settings())


# ------------------------------------------------------------------ system (settings page)
@app.get("/api/system")
def system_status():
    """Memory / swap / disk and which local parts are ready — read-only, for the 設定 page."""
    import illustrate, story, urllib.request
    sys.path.insert(0, str(HERE / "tools"))
    import memguard
    swap = None
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout
        swap = float(re.search(r"used = ([\d.]+)M", out).group(1))
    except Exception:
        pass
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=1)
        ollama = True
    except Exception:
        ollama = shutil.which("ollama") is not None
    parts = [("cosy", "CosyVoice 2", story.cosy_available()), ("flux", "FLUX.2 klein 4B", illustrate.available()),
             ("kinetic", "kinetic-captions", mv.kinetic_available()), ("ollama", "Ollama", ollama),
             ("pexels", "Pexels", mv.has_pexels_key())]
    pw = subprocess.run(["pmset", "-g"], capture_output=True, text=True).stdout
    batt = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    low_power = bool(re.search(r"lowpowermode\s+1", pw))
    on_battery = "Battery Power" in batt
    return jsonify({"low_power": low_power, "on_battery": on_battery,
                    "mem_free_pct": memguard.free_pct(), "swap_used_mb": swap,
                    "disk_free": shutil.disk_usage(HERE).free,
                    "parts": [{"id": i, "name": n, "ok": bool(ok)} for i, n, ok in parts]})


# ------------------------------------------------------------------ ui state
# Screen state (folder, selected file, page, filters...) so a reload or a
# restart puts the user back where they were. Separate from settings.json,
# which only holds options that change the rendered video.
UI_DEFAULTS = {
    "page": "story", "folder": str(DEFAULT_SRC), "file_search": "", "selected": None,
    "overwrite": True, "auto_folder": str(DEFAULT_SRC), "workers": 3,
    "lib_per": 6, "current_video": None, "open_groups": ["映像", "字幕"],
    "posts_filter": "all", "posts_search": "",
    "story": {},          # 台本 page: draft title/text, options, running job id
}


def load_ui_state():
    saved = mv.read_json(UI_STATE, {})
    return {**UI_DEFAULTS, **{k: v for k, v in saved.items() if k in UI_DEFAULTS}}


@app.get("/api/ui-state")
def get_ui_state():
    return jsonify(load_ui_state())


@app.post("/api/ui-state")
def save_ui_state():
    # sendBeacon posts text/plain, so parse the body ourselves
    body = request.get_json(force=True, silent=True) or {}
    with ui_lock:
        state = load_ui_state()
        state.update({k: v for k, v in body.items() if k in UI_DEFAULTS})
        mv.write_json(UI_STATE, state)
    return jsonify(state)


# ------------------------------------------------------------------ create
@app.get("/api/files")
def files():
    folder = Path(request.args.get("folder") or DEFAULT_SRC).expanduser()
    if not folder.is_dir():
        return jsonify({"folder": str(folder), "files": [], "error": "フォルダが見つかりません"})
    items = [{"path": str(f), "name": f.name, "done": (VIDEOS / (f.stem + ".mp4")).exists()}
             for f in sorted(folder.glob("*.mp3"))]
    items += [{"path": str(f), "name": f.name, "done": (VIDEOS / (f.stem + ".mp4")).exists(),
               "uploaded": True} for f in sorted(UPLOADS.glob("*")) if f.suffix.lower() in AUDIO_EXT]
    return jsonify({"folder": str(folder), "files": items})


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "ファイルがありません"}), 400
    m = add_to_inbox(f)
    if not m:
        return jsonify({"error": "対応していない形式です"}), 400
    return jsonify({"path": m["path"], "name": m["name"]})


@app.post("/api/generate")
def generate():
    body = request.json
    mp3 = Path(body["path"])
    if not mp3.exists():
        return jsonify({"error": "音声ファイルが見つかりません"}), 400
    out = VIDEOS / (mp3.stem + ".mp4")
    if body.get("overwrite") and out.exists():
        retire_video(out)
    s = mv.load_settings()

    def work(log):
        if out.exists():
            log("同じ名前の動画がすでにあります")
            return False
        result = mv.make(mp3, VIDEOS, s, log=log)
        if result is None:
            log("生成できませんでした(他のワーカーが処理中の可能性があります)")
            return False
        r = mv.after_render(result, mp3, s, log=log)
        for w in r["warnings"]:
            log("注意: " + w)
        return result.name
    return jsonify({"job": start_job(work)})


@app.get("/api/jobs/<jid>")
def job(jid):
    j = jobs.get(jid)
    return jsonify(j) if j else (jsonify({"error": "not found"}), 404)


# ------------------------------------------------------------------ script (台本から作る)
# Script -> narrated video (story.py): TTS voice, original ink illustrations or stock
# footage, kinetic captions, BGM. Scripts are saved to scripts/ so they can be reopened.
SCRIPTS = HERE / "scripts"
VOICES = [  # edge-tts (free). Deep / calm voices first: they suit long-form narration
    {"id": "en-US-ChristopherNeural", "label": "Christopher(低く重厚・米)"},
    {"id": "en-IN-PrabhatNeural", "label": "Prabhat(落ち着いた・インド英語)"},
    {"id": "en-US-GuyNeural", "label": "Guy(情熱的・米)"},
    {"id": "en-US-AndrewNeural", "label": "Andrew(温かい・米)"},
    {"id": "en-US-BrianNeural", "label": "Brian(親しみ・米)"},
    {"id": "en-US-EricNeural", "label": "Eric(冷静・米)"},
    {"id": "en-GB-RyanNeural", "label": "Ryan(英国)"},
    {"id": "en-GB-ThomasNeural", "label": "Thomas(英国)"},
]
STORY_PRESETS = {"bhatt": "長尺の語り(白黒イラスト)"}
STORY_KEYS = {"voice", "tts_rate", "tts_pitch", "visuals", "aspect", "subs", "subs_engine", "kc_preset",
              "kc_font", "kc_size", "kc_position", "bgm", "bgm_mood", "bgm_volume",
              "hero", "image_seconds", "grain", "show_headings", "offline", "media_folder",
              "tts_engine", "voice_id", "kc_accent_font", "tts_speed"}
VOICE_SAMPLE = ("You were not built to quit. Every scar you carry is proof that you survived "
                "what was meant to break you.")
SIGNED = re.compile(r"^[+-]\d{1,3}(%|Hz)$")


def story_settings(body):
    """Preset on top of settings.json, then the per-video choices from the 台本 page."""
    import story
    preset = body.get("preset") if body.get("preset") in STORY_PRESETS else "bhatt"
    s = story.load_story_settings(preset)
    for k, v in (body.get("options") or {}).items():
        if k in STORY_KEYS and v is not None:
            s[k] = v
    for k in ("tts_rate", "tts_pitch"):
        if not SIGNED.match(str(s.get(k, ""))):
            s[k] = story.STORY_DEFAULTS[k]
    if s.get("tts_engine") == "dia":
        s["tts_engine"] = "cosy"                     # Dia was removed
    if s.get("tts_engine") not in ("cosy", "edge", "say"):
        s["tts_engine"] = "cosy"
    try:
        s["tts_speed"] = max(0.5, min(1.5, float(s.get("tts_speed", 1.0))))
    except (TypeError, ValueError):
        s["tts_speed"] = 1.0
    if not story.my_voice(s.get("voice_id")):
        s["voice_id"] = story.DEFAULT_VOICE
    return preset, s


def script_file(name):
    """A script directly inside scripts/, or None (blocks path traversal)."""
    path = (SCRIPTS / name).resolve()
    ok = path.parent == SCRIPTS.resolve() and path.suffix in (".txt", ".json") and path.is_file()
    return path if ok else None


@app.get("/api/story/options")
def story_options():
    import illustrate, story
    downloading = subprocess.run(["pgrep", "-f", "fetch_flux.py"], capture_output=True).returncode == 0
    scripts = sorted(SCRIPTS.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True) if SCRIPTS.exists() else []
    return jsonify({
        "voices": VOICES, "presets": STORY_PRESETS, "styles": list(story.STYLES),
        "defaults": {k: v for k, v in story_settings({"preset": "bhatt"})[1].items() if k in STORY_KEYS},
        "kinetic_ready": mv.kinetic_available(), "kinetic_setup": mv.KC_SETUP,
        "cosy_ready": story.cosy_available(), "cosy_setup": story.COSY_SETUP,
        "voices": voice_list(),
        "ai_ready": illustrate.available(), "ai_setup": illustrate.SETUP, "ai_downloading": downloading,
        "scripts": [script_info(p) for p in scripts[:100]],
    })


def script_info(p):
    """A saved script for the 台本 page lists: title, scene count, whether its video exists."""
    import story
    lines = p.read_text(errors="ignore").splitlines() or [p.stem]
    title = lines[0].strip()[:120]
    paras = sum(1 for block in "\n".join(lines[1:]).split("\n\n")
                if any(l.strip() and not l.strip().startswith("## ") for l in block.splitlines()))
    return {"name": p.name, "title": title[:80], "mtime": p.stat().st_mtime, "paras": paras,
            "has_video": (VIDEOS / f"{story.slug(title)}.mp4").exists()}


@app.get("/api/story/scripts/<name>")
def story_script(name):
    path = script_file(name)
    if not path:
        abort(404)
    lines = path.read_text(errors="ignore").splitlines()
    return jsonify({"name": path.name, "title": (lines or [""])[0].strip(),
                    "text": "\n".join(lines[1:]).strip()})


@app.post("/api/story/draft")
def story_draft():
    import story
    body = request.get_json(silent=True) or {}
    theme = str(body.get("theme") or "").strip()[:300]
    if not theme:
        return jsonify({"error": "テーマを入力してください"}), 400
    minutes = max(1, min(60, int(body.get("minutes") or 10)))
    style = body.get("style") if body.get("style") in story.STYLES else "bhatt"

    def work(log):
        text, meta = story.draft_script(theme, minutes, style, log=log)
        lines = text.splitlines()
        log("下書きができました。読んで直してから「動画を作る」を押してください")
        return {"title": lines[0].strip(), "text": "\n".join(lines[1:]).strip(), **meta}
    return jsonify({"job": start_job(work)})


@app.post("/api/story/voice-sample")
def story_voice_sample():
    import asyncio, story
    body = request.get_json(silent=True) or {}
    if body.get("engine") in ("cosy", "dia"):
        return cosy_sample(body)
    voice = body.get("voice") if body.get("voice") in {v["id"] for v in VOICES} else VOICES[0]["id"]
    rate = body.get("rate") if SIGNED.match(str(body.get("rate", ""))) else "+0%"
    pitch = body.get("pitch") if SIGNED.match(str(body.get("pitch", ""))) else "+0Hz"
    out = mv.TMP / "voice_samples" / f"{voice}_{rate}_{pitch}.mp3".replace("%", "pct").replace("+", "p")
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        try:
            asyncio.run(story._edge(VOICE_SAMPLE, voice, rate, out, pitch))
        except Exception as e:
            out.unlink(missing_ok=True)
            return jsonify({"error": f"声を作れませんでした(ネット接続を確認してください): {e}"}), 502
    return send_file(out, mimetype="audio/mpeg")


def cosy_sample(body):
    """The sample line read by CosyVoice 2 in the chosen voice = what the video will sound like."""
    import story
    if not story.cosy_available():
        return jsonify({"error": "CosyVoice が入っていません: " + story.COSY_SETUP}), 400
    vid = body.get("voice_id") or story.DEFAULT_VOICE
    mine = story.my_voice(vid)
    if not mine:
        return jsonify({"error": "声が見つかりません"}), 404
    out = mv.TMP / "voice_samples" / f"cosy_{vid}_{int(mine[0].stat().st_mtime)}.wav"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        job = mv.TMP / f"cosy_sample_{vid}.json"
        mv.write_json(job, {"repo": str(story.COSY_REPO), "model": str(story.COSY_MODEL), "ref_audio": str(mine[0]),
                            "ref_text": mine[1]["text"], "speed": 1.0, "items": [{"text": VOICE_SAMPLE, "out": str(out)}]})
        r = subprocess.run([str(story.COSY_PY), str(story.COSY_BATCH), str(job)], cwd=HERE,
                           capture_output=True, text=True)
        job.unlink(missing_ok=True)
        if not out.exists():
            return jsonify({"error": "声を作れませんでした: " + (r.stderr or r.stdout)[-300:]}), 502
    return send_file(out, mimetype="audio/wav")


# ------------------------------------------------------------------ narrator voices (built-in + recorded)
# A short recording of a voice the user may use (their own, or one they have permission for)
# becomes a narrator: CosyVoice 2 gets it + its words as the reference for every scene.
VOICE_MIN, VOICE_MAX = 3.0, 15.0          # seconds kept from the recording


def voice_list():
    """Built-in voices (assets/voices) first, then the user's own (media/voices, oldest first)."""
    import story
    out = []
    for d in (story.ASSET_VOICES, story.MY_VOICES):
        for meta in sorted(d.glob("*.json"), key=lambda p: (p.stat().st_mtime if d == story.MY_VOICES else p.name)):
            mine = story.my_voice(meta.stem)
            if mine and not any(v["id"] == meta.stem for v in out):
                out.append({"id": meta.stem, "name": mine[1]["name"], "desc": mine[1].get("desc", ""),
                            "text": mine[1]["text"], "seconds": mine[1].get("seconds"),
                            "builtin": bool(mine[1].get("builtin"))})
    return out


@app.get("/api/story/voices")
def story_voices():
    return jsonify(voice_list())


@app.post("/api/story/voices")
def story_voice_add():
    """multipart: file (any audio/video), name, text (optional: transcribed when empty),
    consent=1 (the user confirms it is their voice or they may use it)."""
    import story
    f = request.files.get("file")
    name = " ".join(str(request.form.get("name") or "").split())[:40]
    text = " ".join(str(request.form.get("text") or "").split())[:400]
    if not f or not f.filename:
        return jsonify({"error": "録音ファイルを選んでください"}), 400
    if request.form.get("consent") != "1":
        return jsonify({"error": "自分の声か、使う許可をもらった声だけ追加できます"}), 400
    vid = uuid.uuid4().hex[:10]
    story.MY_VOICES.mkdir(parents=True, exist_ok=True)
    raw = mv.TMP / f"voice_upload_{vid}{Path(f.filename).suffix.lower()[:6]}"
    f.save(raw)
    wav = story.MY_VOICES / f"{vid}.wav"
    try:                                      # mono 44.1 kHz, silence trimmed, at most VOICE_MAX s
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-vn", "-ac", "1", "-ar", "44100",
                            "-af", "silenceremove=start_periods=1:start_threshold=-45dB,loudnorm=I=-18",
                            "-t", str(VOICE_MAX), str(wav)], capture_output=True, text=True)
        if r.returncode or not wav.exists():
            return jsonify({"error": "対応していない形式です"}), 400
        secs = mv.duration(wav)
        if secs < VOICE_MIN:
            wav.unlink(missing_ok=True)
            return jsonify({"error": f"録音が短すぎます({VOICE_MIN:.0f} 秒以上にしてください)"}), 400
        if not text:                          # what is said in the clip: Whisper on this Mac
            words = mv.transcribe(wav, mv.TMP / f"voice_{vid}.words.json", "small")
            text = " ".join(w["w"].strip() for w in words).strip()
            (mv.TMP / f"voice_{vid}.words.json").unlink(missing_ok=True)
        if len(text.split()) < 3:
            wav.unlink(missing_ok=True)
            return jsonify({"error": "録音の中の言葉を聞き取れませんでした。話した内容を入力してください"}), 400
        mv.write_json(story.MY_VOICES / f"{vid}.json",
                      {"name": name or f"My voice {len(voice_list()) + 1}", "text": text,
                       "seconds": round(secs, 1), "added": time.time()})
    except Exception:
        wav.unlink(missing_ok=True)             # nothing half-saved
        raise
    finally:
        raw.unlink(missing_ok=True)
    return jsonify({"id": vid, "voices": voice_list()})


@app.get("/api/story/voices/<vid>/audio")
def story_voice_audio(vid):
    import story
    mine = story.my_voice(vid)
    if not mine:
        abort(404)
    return send_file(mine[0], mimetype="audio/wav")


@app.post("/api/story/voices/<vid>/delete")
def story_voice_delete(vid):
    import story
    mine = story.my_voice(vid)
    if not mine:
        abort(404)
    if mine[1].get("builtin"):
        return jsonify({"error": "最初から入っている声は削除できません"}), 400
    for p in (story.MY_VOICES / f"{vid}.wav", story.MY_VOICES / f"{vid}.json"):
        p.unlink(missing_ok=True)
    return jsonify({"voices": voice_list()})


@app.post("/api/story/pick-folder")
def story_pick_folder():
    """Finder's folder picker (the server runs on this Mac), so nobody types paths."""
    r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt '
                        '"動画に使う素材(動画・写真)のフォルダを選んでください")'],
                       capture_output=True, text=True, timeout=600)
    path = r.stdout.strip().rstrip("/")
    return jsonify({"folder": path} if r.returncode == 0 and path else {"folder": None})


@app.post("/api/story/match-preview")
def story_match_preview():
    """Which of the user's own clips each paragraph would get (before rendering).
    Paragraph lengths are estimated from the word count and the speaking rate."""
    import media_match, story
    body = request.get_json(silent=True) or {}
    folder = str((body.get("options") or {}).get("media_folder") or "").strip()
    if not folder or not Path(folder).expanduser().is_dir():
        return jsonify({"error": "素材フォルダが見つかりません"}), 400
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "台本を入力してください"}), 400
    _, s = story_settings(body)
    tmp = mv.TMP / "preview_script.txt"
    tmp.write_text("Preview\n\n" + text + "\n")
    try:
        scenes = story.load_script(tmp)["scenes"]
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    rate = 1 + int(str(s.get("tts_rate", "+0%")).rstrip("%")) / 100
    for sc in scenes:
        sc["dur"] = max(2.0, len(sc["text"].split()) / (2.5 * rate)) + story.GAP

    def work(log):
        index = media_match.index_folder(folder, log)
        if not index["items"]:
            log("このフォルダには使える動画・写真がありません")
            return False
        log(f"素材 {len(index['items'])} 本と照らし合わせています")
        picks = media_match.match(scenes, index, s, log)
        log("できました")
        return {"count": len(index["items"]), "scenes": [
            {"text": sc["text"][:160], "visuals": sc.get("visuals", []),
             "shots": [{"name": Path(sh["path"]).name, "kind": sh["kind"], "score": sh["fit"],
                        "thumb": "/api/story/thumb/" + Path(sh["thumb"]).name} for sh in picks.get(i, [])]}
            for i, sc in enumerate(scenes)]}
    return jsonify({"job": start_job(work)})


@app.get("/api/story/thumb/<name>")
def story_thumb(name):
    import media_match
    path = (media_match.THUMBS / name).resolve()
    if path.parent != media_match.THUMBS.resolve() or not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@app.post("/api/story/generate")
def story_generate():
    import story
    body = request.get_json(silent=True) or {}
    title = " ".join(str(body.get("title") or "").split())[:120]
    text = str(body.get("text") or "").strip()
    if not title or not text:
        return jsonify({"error": "タイトルと台本を入力してください"}), 400
    preset, s = story_settings(body)
    SCRIPTS.mkdir(exist_ok=True)
    path = SCRIPTS / f"{story.slug(title)}.txt"
    path.write_text(title + "\n\n" + text + "\n")
    try:                                   # bad tags etc. are reported before anything runs
        data = story.load_script(path)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    out = VIDEOS / f"{story.slug(data['title'])}.mp4"
    if out.exists():
        if not body.get("overwrite"):
            return jsonify({"error": "同じタイトルの動画があります(「作り直す」をオンにしてください)"}), 409
        retire_video(out)

    def work(log):
        log(f"台本: {path.name}({len(data['scenes'])} 段落, プリセット: {STORY_PRESETS[preset]})")
        name = render_script(path, s, log)
        log("完成しました")
        return name
    return jsonify({"job": start_job(work, kind="story", script=path.name), "script": path.name})


def render_script(path, s, log):
    """One script -> VIDEOS/<slug>.mp4, then the posting folder. Returns the video name.
    story.make_story holds tmp/heavy.lock, so renders never overlap."""
    import story
    result = story.make_story(path, VIDEOS, s, log=log)
    try:
        mv.refresh_posting(VIDEOS, log=log)
    except Exception as e:
        log(f"注意: 投稿用フォルダを更新できませんでした({e})")
    return Path(result).name


@app.post("/api/story/batch")
def story_batch():
    """Several saved scripts, one after another, all with the same 仕上がり settings."""
    import story
    body = request.get_json(silent=True) or {}
    names = [str(n) for n in (body.get("names") or [])][:100]
    if not names:
        return jsonify({"error": "台本を選んでください"}), 400
    preset, s = story_settings(body)
    items, bad = [], []
    for n in dict.fromkeys(names):                 # keep order, drop duplicates
        path = script_file(n)
        if not path:
            bad.append({"name": n, "error": "台本が見つかりません"})
            continue
        try:                                       # bad tags are reported before anything runs
            data = story.load_script(path)
        except ValueError as e:
            bad.append({"name": n, "error": str(e)})
            continue
        items.append({"name": path.name, "title": data["title"], "scenes": len(data["scenes"]),
                      "video": f"{story.slug(data['title'])}.mp4", "status": "waiting", "error": None,
                      "log_from": None})
    if bad:
        return jsonify({"error": " / ".join(f"{b['name']}: {b['error']}" for b in bad), "bad": bad}), 400
    overwrite = bool(body.get("overwrite"))

    def work(log, job):
        total = len(items)
        for i, it in enumerate(items, 1):
            if job.get("cancel"):
                for rest in items[i - 1:]:
                    rest["status"] = "skipped"
                log("残りを中止しました")
                break
            job["current"] = i - 1
            it["log_from"] = len(job["log"])
            log(f"== [{i}/{total}] {it['title']}")
            out = VIDEOS / it["video"]
            if out.exists() and not overwrite:
                it["status"] = "skipped"
                log("スキップ(同じタイトルの動画があります)")
                continue
            it["status"] = "running"
            try:
                retire_video(out)
                render_script(SCRIPTS / it["name"], s, log)
                it["status"] = "done"
                log("完成しました")
            except Exception as e:                 # one bad script doesn't stop the rest
                it["status"], it["error"] = "error", str(e)
                log(f"エラー: {e}")
        job["current"] = None
        n = {k: sum(x["status"] == k for x in items) for k in ("done", "error", "skipped")}
        log(f"一括作成: {n['done']} 本完成 / {n['error']} 本失敗 / {n['skipped']} 本スキップ")
        return [x["video"] for x in items if x["status"] == "done"]
    jid = start_job(work, pass_job=True, kind="story-batch", items=items, current=None, cancel=False)
    return jsonify({"job": jid, "items": items})


@app.post("/api/story/batch/<jid>/cancel")
def story_batch_cancel(jid):
    job = jobs.get(jid)
    if not job or job.get("kind") != "story-batch":
        abort(404)
    job["cancel"] = True
    return jsonify({"ok": True})


@app.post("/api/story/scripts/upload")
def story_scripts_upload():
    """Drop .txt scripts into scripts/ (first line = title). Bad ones are reported, not saved."""
    import story
    added, rejected = [], []
    SCRIPTS.mkdir(exist_ok=True)
    for f in request.files.getlist("files"):
        name = Path(f.filename or "").name
        if Path(name).suffix.lower() != ".txt":
            rejected.append({"name": name, "error": "対応していない形式です"})
            continue
        raw = f.read(2_000_000).decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
        title = " ".join((raw.splitlines() or [""])[0].split())[:120]
        if not title:
            rejected.append({"name": name, "error": "1 行目にタイトルを書いてください"})
            continue
        path = SCRIPTS / f"{story.slug(title)}.txt"
        tmp = mv.TMP / f"upload_{uuid.uuid4().hex[:8]}.txt"
        tmp.write_text(raw + "\n")
        try:
            story.load_script(tmp)
        except ValueError as e:
            tmp.unlink(missing_ok=True)
            rejected.append({"name": name, "error": str(e).replace(tmp.name, name)})
            continue
        os.replace(tmp, path)
        added.append(path.name)
    return jsonify({"added": added, "rejected": rejected})


# ------------------------------------------------------------------ caption fonts
# kinetic-captions' font registry (built-in + Google Fonts the user added). kc/fonts.py is
# stdlib-only, so it is imported straight from kinetic_captions/ (no second venv needed).
def kc_fonts():
    import importlib.util
    spec = importlib.util.spec_from_file_location("kc_fonts", HERE / "kinetic_captions" / "kc" / "fonts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def font_info(f):
    return {"id": f["id"], "label": f["label"], "category": f.get("category", ""),
            "user": f.get("source") == "google", "italic": bool(f.get("italic")),
            "url": f"/api/fonts/file/{f['id']}"}


@app.get("/api/fonts")
def fonts_list():
    return jsonify([font_info(f) for f in kc_fonts().list_fonts()])


@app.post("/api/fonts")
def fonts_add():
    """Add a Google Font by name, specimen URL or embed code (fonts.google.com)."""
    body = request.get_json(silent=True) or {}
    query = str(body.get("query") or "").strip()[:500]
    if not query:
        return jsonify({"error": "フォント名か Google Fonts の URL を入れてください"}), 400
    weight = str(body.get("weight") or "").strip()
    if weight and weight not in {"400", "500", "600", "700", "800", "900"}:
        weight = ""
    kf = kc_fonts()
    try:
        entry = kf.add_google_font(query, weight or None, bool(body.get("italic")))
    except kf.FontError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Google Fonts から取得できませんでした(ネット接続を確認してください): {e}"}), 502
    return jsonify(font_info(entry))


@app.post("/api/fonts/remove")
def fonts_remove():
    fid = str((request.get_json(silent=True) or {}).get("id") or "")
    if not fid.startswith("g-"):
        return jsonify({"error": "追加したフォントだけ削除できます"}), 400
    return jsonify({"ok": kc_fonts().remove_font(fid)})


@app.get("/api/fonts/file/<fid>")
def fonts_file(fid):
    kf = kc_fonts()
    path = kf.font_path(fid)
    if not path or not path.is_file() or kf.FONT_DIR.resolve() not in path.resolve().parents:
        abort(404)
    return send_file(path, mimetype="font/ttf", max_age=86400)


# ------------------------------------------------------------------ auto
@app.get("/api/auto")
def auto_status():
    folder = Path(request.args.get("folder") or DEFAULT_SRC).expanduser()
    mp3s = sorted(folder.glob("*.mp3")) if folder.is_dir() else []
    done = sum((VIDEOS / (f.stem + ".mp4")).exists() for f in mp3s)
    logs = []
    for log in sorted(LOGS.glob("worker_*.log")):
        lines = [l for l in log.read_text(errors="ignore").splitlines() if l.strip()][-4:]
        logs.append({"name": log.stem.replace("worker_", "ワーカー "), "lines": lines})
    return jsonify({"running": workers_running(), "done": done, "total": len(mp3s),
                    "folder": str(folder), "logs": logs})


@app.post("/api/auto/start")
def auto_start():
    if workers_running():
        return jsonify({"error": "すでに実行中です"}), 409
    body = request.json or {}
    folder = str(Path(body.get("folder") or DEFAULT_SRC).expanduser())
    n = max(1, min(4, int(body.get("workers", 3))))
    for f in mv.LOCKS.glob("*.lock"):
        f.unlink()
    for i in range(n):
        subprocess.Popen([sys.executable, "-u", str(HERE / "make_video.py"), "--watch", folder],
                         cwd=HERE, stdout=open(LOGS / f"worker_{i + 1}.log", "w"),
                         stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(0.5)
    return jsonify({"running": n})


@app.post("/api/auto/stop")
def auto_stop():
    subprocess.run(["pkill", "-f", WATCH_PATTERN])
    subprocess.run(["pkill", "-f", "ffmpeg.*" + str(mv.TMP)])
    time.sleep(0.5)
    for f in mv.LOCKS.glob("*.lock"):
        f.unlink()
    return jsonify({"running": workers_running()})


# ------------------------------------------------------------------ library
@app.get("/api/library")
def library():
    fresh, used = mv.library_pool(), mv.used_pool()
    cats = {}
    for c in fresh:
        name = c.parent.name.replace("_", " ")
        cats[name] = cats.get(name, 0) + 1
    size = sum(c.stat().st_size for c in fresh + used)
    return jsonify({"fresh": len(fresh), "used": len(used), "size": size,
                    "categories": sorted(cats.items(), key=lambda x: -x[1]),
                    "queries": mv.load_settings()["queries"]})


@app.post("/api/library/download")
def library_download():
    """Pre-download new clips now (otherwise they are fetched when a video needs them)."""
    body = request.json
    qs = [q.strip() for q in body.get("queries", []) if q.strip()]
    per = max(1, min(15, int(body.get("per", 6))))
    s = mv.load_settings(); s["queries"] = qs
    mv.save_settings(s)

    def work(log):
        key = mv.pexels_key()
        for i, q in enumerate(qs, 1):
            n = mv.fetch_new(q, key, per, log)
            log(f"[{i}/{len(qs)}] {q}: +{n} 本")
        log(f"完了: 未使用の素材 {len(mv.library_pool())} 本")
        return True
    return jsonify({"job": start_job(work)})


@app.get("/api/pexels-key")
def pexels_key_status():
    return jsonify({"has_key": mv.has_pexels_key()})       # never send the key itself


@app.post("/api/pexels-key")
def pexels_key_save():
    key = str((request.json or {}).get("key", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9]{20,100}", key):
        return jsonify({"error": "キーの形式が正しくありません"}), 400
    env = HERE / ".env"
    lines = [l for l in (env.read_text().splitlines() if env.exists() else []) if not l.startswith("PEXELS_API_KEY=")]
    env.write_text("\n".join(lines + [f"PEXELS_API_KEY={key}"]) + "\n")
    os.chmod(env, 0o600)
    return jsonify({"has_key": True})


# ------------------------------------------------------------------ music (BGM)
@app.get("/api/music")
def music_list():
    import music as mus
    tracks = [t for t in mus.load_tracks() if (mus.DIR / t["file"]).exists()]
    return jsonify([{**t, "url": f"/music-media/{quote(t['file'])}"} for t in tracks])


@app.get("/music-media/<name>")
def music_media(name):
    import music as mus
    path = (mus.DIR / name).resolve()
    if path.parent != mus.DIR.resolve() or path.suffix.lower() != ".mp3" or not path.is_file():
        abort(404)
    return send_file(path, conditional=True)


@app.post("/api/music/<name>/enabled")
def music_enable(name):
    import music as mus
    flag = bool((request.get_json(silent=True) or {}).get("enabled"))
    tracks = mus.load_tracks()
    hit = [t for t in tracks if t["file"] == name]
    if not hit:
        abort(404)
    hit[0]["enabled"] = flag
    mus.save_tracks(tracks)
    return jsonify(hit[0])


@app.post("/api/music/fetch")
def music_fetch():
    import music as mus
    n = max(1, min(20, int((request.get_json(silent=True) or {}).get("n", 6))))
    return jsonify({"job": start_job(lambda log: mus.fetch(n, log=log) >= 0)})


@app.post("/api/library/recycle")
def recycle_used():
    """Move every used clip back to the fresh library (allows reuse)."""
    with mv.file_lock("library"):
        n = 0
        for c in mv.used_pool():
            dest = mv.LIB / c.parent.name / c.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(c, dest); n += 1
    return jsonify({"moved": n})


# ------------------------------------------------------------------ videos
@app.get("/api/videos")
def videos():
    vids = sorted(VIDEOS.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify([{"name": v.name, "size": v.stat().st_size,
                     "mtime": v.stat().st_mtime} for v in vids])


@app.post("/api/reveal")
def reveal():
    path = (VIDEOS / request.json["name"]).resolve()
    if path.parent == VIDEOS.resolve() and path.exists():
        subprocess.run(["open", "-R", str(path)])
    return jsonify({"ok": True})


# ------------------------------------------------------------------ posts
# Finished voice videos + their YouTube metadata (<stem>.publish.json), shown
# on the 投稿 page so titles/descriptions/tags can be copied into YouTube Studio.
POSTS = VIDEOS          # legacy name; the 投稿 page reads the same folder as 完成動画
posts_lock = threading.Lock()
_durations = {}   # (path, mtime) -> seconds


def posted_file():
    return VIDEOS / "_posted.json"


def post_number(stem):
    m = re.match(r"(\d+)", stem)
    return int(m.group(1)) if m else 10**9


def media_url(path):
    return f"/posts-media/{quote(path.name)}?v={int(path.stat().st_mtime)}"


def video_duration(path):
    key = (str(path), path.stat().st_mtime)
    if key not in _durations:
        try:
            _durations[key] = round(mv.duration(path), 1)
        except Exception:
            _durations[key] = None
    return _durations[key]


def post_path(name):
    """A file directly inside POSTS, or None (blocks path traversal)."""
    path = (VIDEOS / name).resolve()
    return path if path.parent == VIDEOS.resolve() and path.is_file() else None


def qa_summary(stem):
    q = mv.read_json(VIDEOS / f"{stem}.qa.json", None)
    if not isinstance(q, dict):
        return None
    sync = q.get("sync") or {}
    return {"status": q.get("status"), "reasons": q.get("reasons", []), "checked_at": q.get("checked_at"),
            "checks": [{k: c.get(k) for k in ("label", "status", "detail")} for c in q.get("checks", [])],
            "sync_median": sync.get("median"), "sync_max": sync.get("max")}


def publishing_jobs():
    return {j["stem"]: jid for jid, j in jobs.items() if j.get("stem") and j["status"] == "running"}


@app.get("/api/posts")
def posts_list():
    posted = mv.read_json(posted_file(), {})
    running = publishing_jobs()
    out, seen = [], set()
    entries = []
    for meta_path in VIDEOS.glob("*.publish.json"):
        meta = mv.read_json(meta_path, None)
        if isinstance(meta, dict):
            entries.append((meta_path.name[: -len(".publish.json")], meta))
    # finished videos without a publishing kit yet (failed / in progress) are shown too
    kit_videos = {(m.get("video") or s + ".mp4") for s, m in entries}
    entries += [(v.stem, None) for v in VIDEOS.glob("*.mp4") if v.name not in kit_videos]
    for stem, meta in entries:
        has_kit = meta is not None
        meta = meta or {}
        video = VIDEOS / (meta.get("video") or stem + ".mp4")
        if not video.is_file() or video.parent != VIDEOS or stem in seen:
            continue
        seen.add(stem)
        thumb = VIDEOS / (meta.get("thumbnail") or stem + ".thumb.jpg")
        has_thumb = thumb.is_file() and thumb.parent == VIDEOS
        p = posted.get(stem) or {}
        out.append({
            "has_kit": has_kit, "qa": qa_summary(stem), "republish_job": running.get(stem),
            "stem": stem, "number": post_number(stem),
            "name": re.sub(r"^\d+_", "", stem),
            "video": video.name, "video_url": media_url(video),
            "thumbnail": thumb.name if has_thumb else None,
            "thumbnail_url": media_url(thumb) if has_thumb else None,
            "duration": video_duration(video), "size": video.stat().st_size,
            "posted": bool(p.get("posted")), "posted_at": p.get("at"),
            **{k: meta.get(k, d) for k, d in (
                ("title_en", ""), ("title_ja", ""), ("description_en_full", ""),
                ("description_ja_full", ""), ("tags", []), ("hashtags", []),
                ("thumbnail_text", ""), ("speech_source", ""), ("key_lines", []))},
        })
    out.sort(key=lambda x: (x["number"], x["stem"]))
    return jsonify(out)


# ------------------------------------------------------------------ rights (権利確認)
def rights_item(video):
    import rights
    stem = video.stem
    meta = mv.read_json(VIDEOS / f"{stem}.publish.json", {}) or {}
    r = rights.load(video)
    return {"stem": stem, "number": post_number(stem), "name": re.sub(r"^\d+_", "", stem),
            "title_en": meta.get("title_en", ""), "speech_source": meta.get("speech_source", ""),
            "has_kit": bool(meta), "thumbnail_url": media_url(VIDEOS / meta["thumbnail"])
            if meta.get("thumbnail") and (VIDEOS / meta["thumbnail"]).is_file() else None,
            "detected_source": rights.detected_source(r), "shazam": r["shazam"], "youtube": r["youtube"],
            "risk": rights.risk(r), "check_job": rights_jobs().get(stem)}


def rights_jobs():
    return {j["rights_stem"]: jid for jid, j in jobs.items() if j.get("rights_stem") and j["status"] == "running"}


@app.get("/api/rights")
def rights_list():
    items = [rights_item(v) for v in VIDEOS.glob("*.mp4") if v.parent == VIDEOS]
    items.sort(key=lambda x: (x["number"], x["stem"]))
    batch = next((jid for jid, j in jobs.items() if j.get("rights_batch") and j["status"] == "running"), None)
    return jsonify({"items": items, "batch_job": batch,
                    "statuses": __import__("rights").YOUTUBE_STATUSES})


@app.post("/api/rights/<stem>/check")
def rights_check(stem):
    import rights
    video = post_path(stem + ".mp4")
    if not video:
        abort(404)
    if stem in rights_jobs():
        return jsonify({"job": rights_jobs()[stem]})
    jid = start_job(lambda log: bool(rights.recognize(video, log=log)), rights_stem=stem)
    return jsonify({"job": jid})


@app.post("/api/rights/check-all")
def rights_check_all():
    """Shazam every video (only unchecked ones unless {"all": true}) one after another."""
    import rights
    redo = bool((request.get_json(silent=True) or {}).get("all"))
    videos = sorted((v for v in VIDEOS.glob("*.mp4") if v.parent == VIDEOS), key=lambda v: (post_number(v.stem), v.stem))
    todo = [v for v in videos if redo or rights.load(v)["shazam"].get("status") in (None, "unchecked", "error")]

    def work(log):
        for i, v in enumerate(todo, 1):
            log(f"[{i}/{len(todo)}] {v.stem}")
            rights.recognize(v, log=log)
        log(f"完了: {len(todo)} 本をチェックしました")
        return True
    return jsonify({"job": start_job(work, rights_batch=True), "count": len(todo)})


@app.post("/api/rights/<stem>/youtube")
def rights_set_youtube(stem):
    import rights
    video = post_path(stem + ".mp4")
    if not video:
        abort(404)
    body = request.get_json(silent=True) or {}
    try:
        r = rights.set_youtube(video, body.get("status", "unchecked"), body.get("note", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(rights_item(video))


@app.post("/api/rights/<stem>/source")
def rights_set_source(stem):
    """Edit the speech source (written to publish.json, .publish.md and the posting folder)."""
    import publish_meta
    meta_path = post_path(stem + ".publish.json")
    if not meta_path:
        abort(404)
    source = re.sub(r"\s+", " ", str((request.get_json(silent=True) or {}).get("source", ""))).strip()[:120]
    meta = mv.read_json(meta_path, {})
    meta["speech_source"] = source or "要確認"
    mv.write_json(meta_path, meta)
    try:
        (VIDEOS / f"{stem}.publish.md").write_text(publish_meta.markdown(meta))
    except KeyError:          # incomplete kit: the .md is rebuilt on the next republish
        pass
    try:
        mv.refresh_posting(VIDEOS, log=lambda m: None)
    except Exception:
        pass
    return jsonify(rights_item(VIDEOS / (meta.get("video") or stem + ".mp4")))


STUDIO_ORIGIN = "https://studio.youtube.com"   # lets YouTube Studio (built-in browser) read the files to upload


def _studio_cors(resp):
    if request.headers.get("Origin") == STUDIO_ORIGIN:
        resp.headers["Access-Control-Allow-Origin"] = STUDIO_ORIGIN
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        resp.headers["Vary"] = "Origin"
    return resp


@app.route("/posts-media/<name>", methods=["GET", "OPTIONS"])
def posts_media(name):
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Range"
        return _studio_cors(resp)
    path = post_path(name)
    if not path or path.suffix.lower() not in {".mp4", ".jpg", ".jpeg", ".png"}:
        abort(404)
    return _studio_cors(send_file(path, conditional=True))


@app.post("/api/posts/<stem>/posted")
def posts_set_posted(stem):
    if not post_path(stem + ".publish.json") and not post_path(stem + ".mp4"):
        abort(404)
    flag = bool((request.get_json(silent=True) or {}).get("posted"))
    with posts_lock:
        posted = mv.read_json(posted_file(), {})
        if flag:
            posted[stem] = {"posted": True, "at": time.time()}
        else:
            posted.pop(stem, None)
        mv.write_json(posted_file(), posted)
    return jsonify({"stem": stem, "posted": flag, "posted_at": posted.get(stem, {}).get("at")})


@app.get("/api/posts/<stem>/download/<kind>")
def posts_download(stem, kind):
    if kind not in ("thumbnail", "video"):
        abort(404)
    meta_path = post_path(stem + ".publish.json")
    if not meta_path:
        abort(404)
    meta = mv.read_json(meta_path, {})
    name = (meta.get("thumbnail") or stem + ".thumb.jpg") if kind == "thumbnail" else (meta.get("video") or stem + ".mp4")
    path = post_path(name)
    if not path:
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name, conditional=True)


def find_audio(stem):
    """Source audio of a finished video (its transcript lives in mv.work_dir(audio),
    which only depends on the file name)."""
    q = mv.read_json(VIDEOS / f"{stem}.qa.json", None)
    if isinstance(q, dict) and q.get("audio") and Path(q["audio"]).exists():
        return Path(q["audio"])
    for p in meta_dir().glob("*.json"):
        m = mv.read_json(p, None)
        if m and m.get("video") == stem + ".mp4" and Path(m["path"]).exists():
            return Path(m["path"])
    for folder in (UPLOADS, DEFAULT_SRC / "voice_only", DEFAULT_SRC):
        for f in sorted(folder.glob(glob.escape(stem) + ".*")) if folder.is_dir() else []:
            if f.suffix.lower() in AUDIO_EXT:
                return f
    return DEFAULT_SRC / (stem + ".mp3")     # may not exist: the cached transcript is still found


@app.post("/api/posts/<stem>/republish")
def posts_republish(stem):
    """Rebuild the publishing kit (text + thumbnail) in the background, then refresh
    the posting folder. Returns a job id for /api/jobs/<id>."""
    video = post_path(stem + ".mp4")
    if not video:
        abort(404)
    if stem in publishing_jobs():
        return jsonify({"job": publishing_jobs()[stem], "running": True})
    audio = find_audio(stem)

    def work(log):
        log(f"公開キットを作り直しています: {video.name}")
        ok = mv.run_publish(video, audio, log=log, force=True)
        if not ok:
            log("公開キットを作れませんでした")
            return False
        try:
            mv.refresh_posting(VIDEOS, log=log)
        except Exception as e:
            log(f"投稿用フォルダの更新エラー: {e}")
        log("完了")
        return video.name
    return jsonify({"job": start_job(work, stem=stem)})


@app.post("/api/posts/open-folder")
def posts_open_folder():
    """Open the ready-to-post folder (output/posting) in Finder."""
    mv.POSTING.mkdir(exist_ok=True)
    subprocess.run(["open", str(mv.POSTING)])
    return jsonify({"path": str(mv.POSTING)})


@app.post("/api/posts/reveal")
def posts_reveal():
    path = post_path((request.get_json(silent=True) or {}).get("name") or "")
    if path:
        subprocess.run(["open", "-R", str(path)])
    return jsonify({"ok": bool(path)})


if __name__ == "__main__":
    recover_inbox()
    app.run(host="127.0.0.1", port=8510, threaded=True)
