"""Quality check for a finished video (what used to be checked by hand).

  venv/bin/python qa.py <video.mp4> <source audio> [--no-sync]

check_video() runs these checks and writes <video_dir>/<stem>.qa.json:
  duration    |video - final audio| <= 0.5 s                      (else fail)
  decode      full decode (ffmpeg -v error -f null) has no errors   (else fail)
  audio       the file has an audio stream                          (else fail)
  resolution  matches the aspect used for the render                (else fail)
  subtitles   subs.ass exists and has captions when subs are on     (missing: fail, empty: warn)
  ads         no ad / subscribe phrases (prep.AD_RE) left in the captions (else warn)
  sync        Whisper base on a 60 s excerpt of the video; 3-word sequences are
              matched to the caption word times. median |offset| <= 0.4 s pass,
              <= 1.0 s warn, more: fail. Skipped when settings "qa_sync" is off.
Overall status: fail > warn > pass (skipped checks do not count).
"""
import json, re, statistics, subprocess, sys, time
from pathlib import Path

import make_video as mv
import prep

DURATION_TOL = 0.5      # s
SYNC_PASS = 0.4         # s, median |offset|
SYNC_WARN = 1.0
SYNC_EXCERPT = 60.0     # s of video re-transcribed
SYNC_MODEL = "base"
SYNC_MIN_MATCHES = 3
RANK = {"pass": 0, "skip": 0, "warn": 1, "fail": 2}


def probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,width,height", "-of", "json", str(video)],
                         capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {"duration": float(d.get("format", {}).get("duration") or 0),
            "width": v.get("width"), "height": v.get("height"),
            "audio": any(s.get("codec_type") == "audio" for s in streams)}


def decode_errors(video):
    """Lines ffmpeg prints at -v error while decoding the whole file ([] = clean)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(video),
                        "-f", "null", "-"], capture_output=True, text=True)
    lines = [l for l in r.stderr.splitlines() if l.strip()]
    if r.returncode and not lines:
        lines = [f"ffmpeg exit {r.returncode}"]
    return lines


def expected_audio(audio, work, s):
    """Length of the audio the video was built on (after ad cuts)."""
    rec = mv.read_json(work / "render.json", None)
    if rec and rec.get("total"):
        return float(rec["total"])
    a = mv.read_json(work / "analysis.json", None)
    if a and s.get("trim_ads", True):
        return sum(b - c for c, b in prep.keep_ranges(a["sentences"], a["total"]))
    voice = work / "voice.wav"
    return mv.duration(voice if voice.exists() else audio)


def caption_words(work, s=None):
    """Words the captions were timed with: words_render.json (written by every render),
    else rebuilt from the analysis cache for videos rendered before that existed."""
    words = mv.read_json(work / "words_render.json", None)
    if words is not None:
        return words
    a = mv.read_json(work / "analysis.json", None)
    if not a:
        return None
    words = mv.read_json(work / f"words_{a.get('source', 'voice')}.json", None)
    if not words:
        return None
    if (s or {}).get("trim_ads", True):
        words = prep.remap_words(words, prep.keep_ranges(a["sentences"], a["total"]))
    return words


def ass_lines(path):
    """Caption text of every Dialogue line (override tags removed)."""
    out = []
    for line in Path(path).read_text(errors="ignore").splitlines():
        if line.startswith("Dialogue:"):
            text = line.split(",", 9)[-1]
            text = re.sub(r"\{[^}]*\}", "", text).replace("\\N", " ").replace("\\n", " ")
            out.append(re.sub(r"\s+", " ", text).strip())
    return [t for t in out if t]


def ad_phrases(work, s=None):
    """Ad phrases still present in the captions / the words they were built from."""
    texts = []
    ass = work / "subs.ass"
    if ass.exists():
        lines = ass_lines(ass)
        texts.append(" ".join(l for i, l in enumerate(lines) if i == 0 or l != lines[i - 1]))
    words = caption_words(work, s)
    if words:
        texts.append(" ".join(w["w"] for w in words))
    found = []
    for t in texts:
        found += [m.group(0).strip() for m in prep.AD_RE.finditer(t)]
    return sorted(set(f.lower() for f in found))


def _norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def match_offsets(heard, ref, window=3.0):
    """heard/ref: [{"w","s"}]. For every 3-word sequence heard in the video, the
    nearest identical sequence in the caption words; returns heard - caption (s)."""
    ref = [(_norm(w["w"]), w["s"]) for w in ref]
    ref = [r for r in ref if r[0]]
    idx = {}
    for i in range(len(ref) - 2):
        idx.setdefault((ref[i][0], ref[i + 1][0], ref[i + 2][0]), []).append(ref[i][1])
    heard = [(_norm(w["w"]), w["s"]) for w in heard]
    heard = [h for h in heard if h[0]]
    offsets = []
    for i in range(len(heard) - 2):
        cands = idx.get((heard[i][0], heard[i + 1][0], heard[i + 2][0]))
        if cands:
            best = min(cands, key=lambda c: abs(heard[i][1] - c))
            if abs(heard[i][1] - best) <= window:
                offsets.append(round(heard[i][1] - best, 3))
    return offsets


def sync_check(video, work, total, log=print, s=None):
    """Re-transcribe a 60 s excerpt of the finished video and compare word times."""
    ref = caption_words(work, s)
    if not ref:
        return {"status": "skip", "detail": "字幕の単語データがありません"}
    start = max(0.0, min(total * 0.3, total - SYNC_EXCERPT))
    length = min(SYNC_EXCERPT, total - start)
    clip = work / "qa_excerpt.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", "16000", str(clip)], check=True)
    try:
        heard = mv.whisper_words(clip, SYNC_MODEL, offset=start)
    finally:
        clip.unlink(missing_ok=True)
    near = [w for w in ref if start - 5 <= w["s"] <= start + length + 5]
    offs = match_offsets(heard, near)
    res = {"start": round(start, 1), "length": round(length, 1), "matched": len(offs)}
    if len(offs) < SYNC_MIN_MATCHES:
        return {**res, "status": "warn", "detail": f"照合できた言葉が少なすぎます({len(offs)} 箇所)"}
    absd = [abs(o) for o in offs]
    med, mx = statistics.median(absd), max(absd)
    within = sum(a <= SYNC_PASS for a in absd) / len(absd)
    res.update(median=round(med, 2), max=round(mx, 2), within=round(within, 2),
               bias=round(statistics.median(offs), 2))
    status = "pass" if med <= SYNC_PASS else "warn" if med <= SYNC_WARN else "fail"
    return {**res, "status": status,
            "detail": f"ずれ 中央値 {med:.2f} 秒 / 最大 {mx:.2f} 秒({len(offs)} 箇所, ±{SYNC_PASS} 秒以内 {within:.0%})"}


def qa_path(video):
    video = Path(video)
    return video.parent / (video.stem + ".qa.json")


def check_video(video, audio, work, log=print, s=None):
    """Run every check, save <stem>.qa.json next to the video and return it."""
    video, audio, work = Path(video), Path(audio), Path(work)
    s = s or mv.load_settings()
    rec = mv.read_json(work / "render.json", {}) or {}
    checks = []

    def add(name, label, status, detail=""):
        checks.append({"name": name, "label": label, "status": status, "detail": detail})

    info = probe(video)
    # duration
    try:
        want = expected_audio(audio, work, s)
        diff = abs(info["duration"] - want)
        add("duration", "長さ", "pass" if diff <= DURATION_TOL else "fail",
            f"動画 {info['duration']:.2f} 秒 / 音声 {want:.2f} 秒(差 {diff:.2f} 秒)")
    except Exception as e:
        add("duration", "長さ", "warn", f"音声の長さを確認できません: {e}")
    # decode
    log("  QA: 全体をデコードしてエラーを確認中…")
    errs = decode_errors(video)
    add("decode", "デコード", "fail" if errs else "pass",
        ("エラー: " + " | ".join(errs[:3])) if errs else "エラーなし")
    # audio stream
    add("audio", "音声トラック", "pass" if info["audio"] else "fail", "" if info["audio"] else "音声がありません")
    # resolution
    size = tuple(rec.get("size") or mv.frame_size({"aspect": rec.get("aspect") or s["aspect"]}))
    got = (info["width"], info["height"])
    add("resolution", "解像度", "pass" if got == size else "fail", f"{got[0]}x{got[1]}(期待 {size[0]}x{size[1]})")
    # subtitles
    subs_on = rec.get("subs", s.get("subs", True))
    ass = work / "subs.ass"
    words = caption_words(work, s)
    if subs_on:
        if not ass.exists():
            add("subtitles", "字幕", "fail", "字幕ファイルがありません")
        else:
            n = len(ass_lines(ass))
            add("subtitles", "字幕", "pass" if n or not words else "warn",
                f"{n} 行" if n else "字幕が 0 行です")
    else:
        add("subtitles", "字幕", "skip", "字幕はオフ")
    # ads left
    found = ad_phrases(work, s) if subs_on else []
    add("ads", "宣伝の消し残し", "warn" if found else "pass",
        ("残っている言葉: " + ", ".join(found[:5])) if found else "なし")
    # sync
    sync = {"status": "skip", "detail": "オフ"}
    if subs_on and s.get("qa_sync", True) and info["audio"]:
        log("  QA: 字幕と声のずれを確認中(60 秒・Whisper base)…")
        try:
            sync = sync_check(video, work, info["duration"], log, s)
        except Exception as e:
            sync = {"status": "warn", "detail": f"確認できませんでした: {e}"}
    add("sync", "字幕と声のずれ", sync["status"], sync.get("detail", ""))

    status = max((c["status"] for c in checks), key=lambda x: RANK[x])
    status = "pass" if status == "skip" else status
    reasons = [f"{c['label']}: {c['detail']}" for c in checks if c["status"] in ("warn", "fail")]
    result = {"video": video.name, "audio": str(audio), "status": status, "reasons": reasons,
              "checks": checks, "sync": sync, "duration": round(info["duration"], 2), "checked_at": time.time()}
    mv.write_json(qa_path(video), result)
    log(f"  QA: {status}" + (" — " + " / ".join(reasons) if reasons else ""))
    return result


if __name__ == "__main__":
    v, a = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    st = mv.load_settings()
    if "--no-sync" in sys.argv:
        st["qa_sync"] = False
    r = check_video(v, a, mv.work_dir(a), s=st)
    print(json.dumps(r, indent=2, ensure_ascii=False))
