"""Rights check for finished videos.

For each video we keep <video_dir>/<stem>.rights.json:
  {
    "shazam": {"status": "match" | "none" | "error", "matches": [{"at", "title", "artist", "label",
               "url"}], "checked_at", "error"},
    "youtube": {"status": "unchecked" | "ok" | "claim" | "blocked" | "deleted", "note", "updated_at"},
  }

The Shazam check sends three short (12 s) clips of the finished video to Shazam
(shazamio). A match means the audio is registered on music/streaming services
(e.g. Fearless Motivation releases its speeches as tracks), which usually also
means it is registered in YouTube Content ID -> high risk of a copyright claim.
It is a risk signal, not a legal answer; YouTube Studio's "Checks" step on a
private upload is the authoritative test.

  venv/bin/python rights.py <video.mp4> [...]      # check from the command line
"""
import asyncio, json, logging, subprocess, sys, tempfile, time, warnings
from pathlib import Path

import make_video as mv

YOUTUBE_STATUSES = {"unchecked": "未確認", "ok": "問題なし", "claim": "申し立てあり",
                    "blocked": "ブロック・制限", "deleted": "削除した"}
SAMPLE_POINTS = (0.15, 0.5, 0.85)     # where in the video to take the clips
CLIP_SECONDS = 12


def rights_path(video):
    video = Path(video)
    return video.parent / (video.stem + ".rights.json")


def load(video):
    r = mv.read_json(rights_path(video), {}) or {}
    r.setdefault("shazam", {"status": "unchecked", "matches": []})
    r.setdefault("youtube", {"status": "unchecked", "note": ""})
    return r


def save(video, data):
    mv.write_json(rights_path(video), data)


def set_youtube(video, status, note=""):
    if status not in YOUTUBE_STATUSES:
        raise ValueError(f"unknown status {status}")
    r = load(video)
    r["youtube"] = {"status": status, "note": str(note)[:500], "updated_at": time.time()}
    save(video, r)
    return r


def _clip(video, start, dest):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", str(CLIP_SECONDS), "-i", str(video),
                    "-vn", "-ac", "1", "-ar", "44100", "-b:a", "128k", str(dest)], check=True)


def _track_info(out, at):
    t = (out or {}).get("track") or {}
    if not t:
        return None
    label = ""
    for sec in t.get("sections", []) or []:
        for md in sec.get("metadata", []) or []:
            if str(md.get("title", "")).lower() == "label":
                label = md.get("text", "")
    return {"at": round(at, 1), "title": t.get("title", ""), "artist": t.get("subtitle", ""),
            "label": label, "url": t.get("url", "")}


async def _recognize_all(clips):
    from shazamio import Shazam
    shazam = Shazam()
    results = []
    for at, path in clips:
        results.append((at, await shazam.recognize(str(path))))
    return results


def recognize(video, log=print, retries=1):
    """Shazam three clips of the video in a short-lived child process (shazamio leaks
    network sockets; a long batch inside the server ran out of local ports - Errno 49).
    Retries once after a pause on errors. Returns the 'shazam' record (also saved)."""
    video = Path(video)
    rec = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(8)
        try:
            out = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(video)],
                                 capture_output=True, text=True, timeout=180, cwd=str(mv.HERE))
            rec = load(video)["shazam"]
            if out.returncode != 0 and rec.get("status") in (None, "unchecked"):
                rec = {"status": "error", "matches": [], "checked_at": time.time(),
                       "error": (out.stderr.strip().splitlines() or ["child failed"])[-1][:200]}
        except subprocess.TimeoutExpired:
            rec = {"status": "error", "matches": [], "checked_at": time.time(), "error": "timeout"}
        if rec.get("status") != "error":
            break
    if rec.get("status") == "error":
        r = load(video); r["shazam"] = rec; save(video, r)
    matches = rec.get("matches") or []
    log(f"  権利チェック: {rec['status']}" + (f" ({matches[0]['title']} / {matches[0]['artist']})" if matches else ""))
    return rec


def _recognize_here(video):
    """The actual Shazam calls (run in the child process)."""
    video = Path(video)
    dur = mv.duration(video)
    with tempfile.TemporaryDirectory() as d:
        clips = []
        for k, f in enumerate(SAMPLE_POINTS):
            at = max(0.0, min(dur - CLIP_SECONDS, dur * f))
            p = Path(d) / f"c{k}.mp3"
            _clip(video, at, p)
            clips.append((at, p))
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                raw = asyncio.run(_recognize_all(clips))
                error = None
            except Exception as e:                       # network / API trouble
                raw, error = [], f"{e.__class__.__name__}: {e}"[:200]
    matches = [m for at, out in raw if (m := _track_info(out, at))]
    rec = {"status": "error" if error else ("match" if matches else "none"),
           "matches": matches, "checked_at": time.time(), "error": error}
    r = load(video)
    r["shazam"] = rec
    save(video, r)
    return rec


def detected_source(r):
    """Best guess of the speech owner from the Shazam matches (artist of the most common match)."""
    artists = [m["artist"] for m in r.get("shazam", {}).get("matches", []) if m.get("artist")]
    return max(set(artists), key=artists.count) if artists else ""


def risk(r):
    """'high' (registered audio), 'claimed' (YouTube said so), 'ok', or 'unknown'."""
    yt = r.get("youtube", {}).get("status")
    if yt in ("claim", "blocked"):
        return "claimed"
    if yt == "ok":
        return "ok"
    sh = r.get("shazam", {}).get("status")
    if sh == "match":
        return "high"
    if sh == "none":
        return "low"
    return "unknown"


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        _recognize_here(sys.argv[2])
        sys.exit(0)
    for v in sys.argv[1:]:
        print(Path(v).name, json.dumps(recognize(v, log=lambda m: None), ensure_ascii=False))
