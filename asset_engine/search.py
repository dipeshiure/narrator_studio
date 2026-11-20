"""Pexels search that returns preview URLs. The full file is not downloaded here."""
import json
import urllib.error
import urllib.parse
import urllib.request


def fetch_json(url, key, log=print):
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "Mozilla/5.0"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            log("  ! Pexels の回数制限に当たりました")
        else:
            log(f"  ! Pexels の検索に失敗しました({e.code})")
        return None
    except Exception as e:
        log(f"  ! Pexels の検索に失敗しました({e})")
        return None


def _videos(data, query, limit):
    out = []
    for v in (data or {}).get("videos") or []:
        if len(out) >= limit or v.get("duration", 0) < 4:
            continue
        files = [f for f in v.get("video_files") or [] if f.get("width") and 1280 <= f["width"] <= 1920
                 and f.get("file_type") == "video/mp4" and f.get("link")]
        if not files or not v.get("image"):
            continue
        f = min(files, key=lambda item: item["width"])
        out.append({
            "id": f"v{v['id']}", "pexels_id": str(v["id"]), "kind": "video",
            "preview": v["image"], "download": f["link"], "width": f["width"],
            "author": (v.get("user") or {}).get("name") or "Pexels",
            "page": v.get("url") or "", "query": query, "license": "Pexels License",
            "source": "Pexels",
        })
    return out


def _photos(data, query, limit):
    out = []
    for p in (data or {}).get("photos") or []:
        if len(out) >= limit:
            break
        src = (p.get("src") or {}).get("large") or (p.get("src") or {}).get("original")
        if not src:
            continue
        out.append({
            "id": f"p{p['id']}", "pexels_id": str(p["id"]), "kind": "image",
            "preview": src, "download": src, "width": p.get("width") or 0,
            "author": p.get("photographer") or "Pexels",
            "page": p.get("url") or "", "query": query, "license": "Pexels License",
            "source": "Pexels",
        })
    return out


def search_videos(query, key, limit, log=print, fetch=None):
    fetch = fetch or fetch_json
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
        {"query": query, "orientation": "landscape", "per_page": limit, "page": 1, "size": "medium"})
    return _videos(fetch(url, key, log), query, limit)


def search_photos(query, key, limit, log=print, fetch=None):
    fetch = fetch or fetch_json
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
        {"query": query, "orientation": "landscape", "per_page": limit, "page": 1})
    return _photos(fetch(url, key, log), query, limit)


_mixkit_down = False


def search_mixkit(query, key, limit, log=print):
    """Mixkit has no public search API. One failed reply turns this off for the rest of the run."""
    global _mixkit_down
    if _mixkit_down or not query:
        return []
    url = "https://mixkit.co/api/v1/videos?" + urllib.parse.urlencode({"search": query, "page": 1})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as res:
            ctype = res.headers.get("Content-Type", "")
            raw = res.read()
        if "json" not in ctype.lower():
            raise ValueError("not json")
        data = json.loads(raw)
    except Exception:
        _mixkit_down = True
        log("  Mixkit の検索は使えませんでした。Pexels の結果で比べます")
        return []
    rows = data if isinstance(data, list) else (data.get("videos") or data.get("data") or [])
    out = []
    for v in rows:
        if len(out) >= limit or not isinstance(v, dict):
            continue
        preview = v.get("thumbnail") or v.get("image") or v.get("preview")
        download = v.get("download") or v.get("url") or v.get("mp4")
        vid = v.get("id")
        if not preview or not download or vid is None:
            continue
        out.append({
            "id": f"m{vid}", "pexels_id": str(vid), "kind": "video",
            "preview": preview, "download": download, "width": v.get("width") or 1280,
            "author": v.get("author") or "Mixkit", "page": v.get("page") or "",
            "query": query, "license": "Mixkit License", "source": "Mixkit",
        })
    return out
