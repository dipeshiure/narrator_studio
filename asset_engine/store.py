"""Download only the chosen file, skip ids we already have, and keep the licence next to it."""
import json
from pathlib import Path

import make_video as mv

MIN_IMAGE = 10_000


def valid(path, kind):
    path = Path(path)
    if not path.is_file():
        return False
    if kind == "video":
        return mv._is_clip(path)
    return path.suffix.lower() in (".jpg", ".jpeg", ".png") and path.stat().st_size >= MIN_IMAGE


def find_existing(cand):
    """A file already in the library for this Pexels id, including the older unprefixed video names."""
    names = [cand["id"]]
    raw = str(cand.get("pexels_id") or "")
    if cand["kind"] == "video" and raw:
        names.append(raw)
    suffix = ".mp4" if cand["kind"] == "video" else ".jpg"
    for root in (mv.LIB, mv.USED):
        if not root.exists():
            continue
        for name in names:
            for hit in root.glob(f"**/{name}{suffix}"):
                if valid(hit, cand["kind"]):
                    return hit
            if cand["kind"] == "image":
                for hit in root.glob(f"**/{name}.jpeg"):
                    if valid(hit, "image"):
                        return hit
                for hit in root.glob(f"**/{name}.png"):
                    if valid(hit, "image"):
                        return hit
    return None


def _meta(path, cand, score):
    Path(str(path) + ".json").write_text(json.dumps({
        "id": cand["id"], "source": cand.get("source") or "Pexels", "author": cand.get("author") or "",
        "license": cand.get("license") or "Pexels License", "page": cand.get("page") or "",
        "query": cand.get("query") or "", "score": score, "kind": cand["kind"],
    }, ensure_ascii=False, indent=2))


def download(cand, score, log=print, fetch_bytes=None):
    """Save the winner once. Returns the path, or None when the file is missing or too small."""
    found = find_existing(cand)
    if found:
        _meta(found, cand, score)
        return found
    folder = mv.LIB / str(cand.get("query") or "asset").replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    suffix = ".mp4" if cand["kind"] == "video" else ".jpg"
    dest = folder / f"{cand['id']}{suffix}"
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        if fetch_bytes is None:
            import urllib.request
            req = urllib.request.Request(cand["download"], headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=90).read()
        else:
            data = fetch_bytes(cand)
        part.write_bytes(data)
        part.replace(dest)
    except Exception as e:
        part.unlink(missing_ok=True)
        log(f"  ! 素材を保存できませんでした({e})")
        return None
    if not valid(dest, cand["kind"]):
        dest.unlink(missing_ok=True)
        log(f"  ! 素材ファイルが壊れているので捨てました({cand['id']})")
        return None
    _meta(dest, cand, score)
    return dest
