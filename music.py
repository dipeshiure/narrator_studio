"""Free background music (BGM) for the videos.

Source: Kevin MacLeod / incompetech.com — free under Creative Commons
Attribution 4.0 (CC BY 4.0). The only condition: credit the music in the
work. We therefore always add the exact credit block to the description of
any video that uses a track (credit_block()).

  素材/music/catalog.json   incompetech's public catalogue (pieces.json, cached)
  素材/music/tracks.json    downloaded tracks: title, file, feel, bpm, length, enabled, uses
  素材/music/<file>.mp3     the audio

  venv/bin/python music.py fetch [N]     # download N more motivational tracks (default 12)
"""
import json, random, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

import make_video as mv

DIR = mv.MEDIA / "music"
CATALOG_URL = "https://incompetech.com/music/royalty-free/pieces.json"
MP3_URL = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
LICENSE = "CC BY 4.0"

# feels that suit a motivational speech bed, and feels that never do
GOOD = {"epic": 3, "uplifting": 3, "driving": 2, "intense": 2, "action": 1, "bright": 1, "somber": 1,
        "dark": 0.5, "suspenseful": 0.5}
BAD = {"humorous", "bouncy", "eerie", "unnerving", "creepy", "ren faire", "medieval", "grooving",
       "silly", "quirky", "funky", "relaxed", "mystical", "mysterious", "holiday", "christmas"}
MOODS = {"auto": None, "epic": {"epic", "intense", "action"}, "uplifting": {"uplifting", "bright"},
         "dark": {"somber", "dark", "driving"}}


def tracks_path():
    return DIR / "tracks.json"


def load_tracks():
    return mv.read_json(tracks_path(), []) or []


def save_tracks(tracks):
    DIR.mkdir(exist_ok=True)
    mv.write_json(tracks_path(), tracks)


def _seconds(length):
    try:
        h, m, s = (int(x) for x in str(length).split(":"))
        return h * 3600 + m * 60 + s
    except Exception:
        return 0


def feels(piece):
    return {f.strip().lower() for f in str(piece.get("feel") or "").split(",") if f.strip()}


def catalog(refresh=False):
    DIR.mkdir(exist_ok=True)
    path = DIR / "catalog.json"
    if refresh or not path.exists() or time.time() - path.stat().st_mtime > 30 * 86400:
        req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "Mozilla/5.0"})
        data = json.load(urllib.request.urlopen(req, timeout=60))
        mv.write_json(path, data)
    return mv.read_json(path, [])


BAD_TITLE = re.compile(r"christmas|xmas|holiday|santa|jingle|merry|cartoon|disco|8 ?bit|dungeon|video game|"
                       r"wedding|bridal|funeral|lullaby|carol|polka|waltz|rap\b|comedy|silly|kids", re.I)


def score(piece):
    f = feels(piece)
    if f & BAD or not f & set(GOOD) or BAD_TITLE.search(str(piece.get("title", ""))):
        return 0
    secs = _seconds(piece.get("length"))
    if secs < 100:                     # too short to sit under a speech
        return 0
    s = sum(GOOD.get(x, 0) for x in f)
    if "piano" in str(piece.get("instruments", "")).lower() or "strings" in str(piece.get("instruments", "")).lower():
        s += 0.5
    return s + min(secs, 300) / 600


def credit_block(titles):
    """The exact credit incompetech asks for (one block for one or more titles)."""
    names = ", ".join(f'"{t}"' for t in titles)
    return (f"{names}\nKevin MacLeod (incompetech.com)\n"
            "Licensed under Creative Commons: By Attribution 4.0\nhttp://creativecommons.org/licenses/by/4.0/")


def fetch(n=12, log=print):
    """Download up to n more good motivational tracks. Returns the number added."""
    have = {t["file"] for t in load_tracks()}
    pieces = sorted((p for p in catalog() if score(p) > 0 and p.get("filename") not in have),
                    key=score, reverse=True)
    tracks, added, fails = load_tracks(), 0, 0
    for p in pieces:
        if added >= n:
            break
        if fails >= 3:
            log("  ! ダウンロードが続けて失敗したので中断しました(ネット接続を確認してください)")
            break
        dest = DIR / p["filename"]
        try:
            req = urllib.request.Request(MP3_URL + urllib.parse.quote(p["filename"]), headers={"User-Agent": "Mozilla/5.0"})
            part = dest.with_suffix(".part")
            part.write_bytes(urllib.request.urlopen(req, timeout=120).read())
            part.replace(dest)
        except Exception as e:
            log(f"  ! {p['title']}: {e}")
            fails += 1
            continue
        fails = 0
        tracks.append({"title": p["title"], "file": p["filename"], "feel": sorted(feels(p)),
                       "bpm": p.get("bpm"), "length": _seconds(p.get("length")), "isrc": p.get("isrc"),
                       "artist": "Kevin MacLeod", "source": "incompetech.com", "license": LICENSE,
                       "enabled": True, "uses": 0})
        added += 1
        log(f"  + {p['title']} ({', '.join(sorted(feels(p)))})")
        save_tracks(tracks)
    return added


def pick(duration, mood="auto", rng=random):
    """Least-used enabled track for this mood (falls back to any enabled track). Marks it used."""
    tracks = load_tracks()
    pool = [t for t in tracks if t.get("enabled", True) and (DIR / t["file"]).exists()]
    want = MOODS.get(mood)
    if want:
        pool = [t for t in pool if set(t.get("feel", [])) & want] or pool
    if not pool:
        return None
    rng.shuffle(pool)
    # prefer tracks long enough to avoid an audible loop, then the least used
    pool.sort(key=lambda t: (t.get("uses", 0), 0 if t.get("length", 0) >= duration else 1))
    t = pool[0]
    for x in tracks:
        if x["file"] == t["file"]:
            x["uses"] = x.get("uses", 0) + 1
    save_tracks(tracks)
    return {**t, "path": str(DIR / t["file"]), "credit": credit_block([t["title"]])}


def add_credit(meta, music):
    """Append the CC BY credit to both descriptions (idempotent). Returns True if changed."""
    if not music:
        return False
    block = credit_block([music["title"]])
    changed = False
    for key, label in (("description_en_full", "Music:"), ("description_ja_full", "音楽:")):
        text = meta.get(key, "")
        text = re.sub(r"\n\n(Music:|音楽:)\n.*?http://creativecommons\.org/licenses/by/4\.0/", "", text, flags=re.S)
        new = f"{text.rstrip()}\n\n{label}\n{block}"
        if new != meta.get(key):
            meta[key] = new; changed = True
    meta["music"] = {k: music.get(k) for k in ("title", "artist", "source", "license", "file")}
    return changed


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":
        print("added", fetch(int(sys.argv[2]) if len(sys.argv) > 2 else 12))
    else:
        print(__doc__)
