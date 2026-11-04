"""Real photos from Wikimedia Commons, with their licence and credit line.

Only freely reusable licences are accepted (public domain, CC0, CC BY, CC BY-SA);
every photo keeps the author / licence / file page so the video can credit it.
Downloads are cached in 素材/commons/ (one JPEG + one .json per file).
"""
import html, json, re, time, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE / "media" / "commons"
API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks API clients for a descriptive User-Agent
UA = "motiversity-auto/1.0 (local video tool; https://commons.wikimedia.org/wiki/Commons:API)"
ALLOWED = re.compile(r"^(public domain|pd\b|cc0|cc by(-sa)? \d)", re.I)


def _get(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            print(f"  ! commons (try {attempt + 1}): {e}", flush=True)
            time.sleep(2 * (attempt + 1))
    return {}


def _text(value):
    """Commons metadata is HTML: keep the plain text."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", value or ""))).strip()


def allowed(licence):
    return bool(ALLOWED.match(licence.strip()))


def search(query, limit=20, min_width=1200):
    """Photos for query with a reusable licence, best (largest) first."""
    data = _get({"action": "query", "generator": "search", "gsrnamespace": 6, "gsrlimit": limit,
                 "gsrsearch": f"{query} filetype:bitmap", "prop": "imageinfo",
                 "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920,
                 "iiextmetadatafilter": "LicenseShortName|Artist|Credit|LicenseUrl|ObjectName"})
    out = []
    for p in sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = _text(meta.get("LicenseShortName", {}).get("value"))
        if info.get("mime") != "image/jpeg" or info.get("width", 0) < min_width or not allowed(lic):
            continue
        out.append({"title": p["title"].removeprefix("File:"), "page": info.get("descriptionurl", ""),
                    "url": info.get("thumburl") or info["url"], "license": lic,
                    "license_url": _text(meta.get("LicenseUrl", {}).get("value")),
                    "author": _text(meta.get("Artist", {}).get("value")) or "Unknown author",
                    "width": info["width"], "height": info["height"]})
    return out


def credit(photo):
    """One credit line, e.g. 'Store front' by Jane Doe, CC BY 2.0, via Wikimedia Commons."""
    name = re.sub(r"\.\w+$", "", photo["title"])
    return f"\"{name}\" by {photo['author']}, {photo['license']}, via Wikimedia Commons ({photo['page']})"


def fetch(query, n, used=(), log=print):
    """Up to n downloaded photos for query (cached), skipping titles in `used`."""
    LIB.mkdir(exist_ok=True)
    picks = []
    for ph in search(query):
        if len(picks) >= n:
            break
        if ph["title"] in used:
            continue
        stem = re.sub(r"[^A-Za-z0-9]+", "_", ph["title"])[:80]
        jpg, meta = LIB / f"{stem}.jpg", LIB / f"{stem}.json"
        if not jpg.exists():
            try:
                req = urllib.request.Request(ph["url"], headers={"User-Agent": UA})
                part = jpg.with_suffix(".part")
                part.write_bytes(urllib.request.urlopen(req, timeout=60).read())
                part.rename(jpg)
            except Exception as e:
                log(f"  ! photo download {ph['title']}: {e}")
                continue
            meta.write_text(json.dumps(ph, ensure_ascii=False, indent=1))
        picks.append({**ph, "path": str(jpg)})
    return picks
