"""Motiversity-style YouTube thumbnails (1280x720 JPG) from Pexels photos.

    make_thumbnail(meta, dest)            # meta = <stem>.publish.json dict
    venv/bin/python thumbs.py <publish.json> [...] [--out DIR] [--seed N]

Look: dark, cinematic, high-contrast black & white (or heavily desaturated)
photos of strong subjects - boxers, athletes, soldiers, statues, determined
faces - as a 3-panel / 2-panel collage or a single hero portrait, with one
short huge title in either
  (a) elegant white serif, Title Case with a period   ("Stay Disciplined.")
  (b) heavy condensed white caps stacked on 2 lines    ("STAY / DISCIPLINED")
Photos are cached in 素材/photos/ and never reused on two thumbnails
(素材/photos/used.json). Attribution goes to meta["thumbnail_photos"].
"""
import fcntl, json, os, random, re, sys, zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
PHOTOS = HERE / "media" / "photos"
USED = PHOTOS / "used.json"
SEARCH_CACHE = PHOTOS / "search_cache.json"
API = "https://api.pexels.com/v1/search"
W, H = 1280, 720
GAP = 6                      # dark gutter between collage panels
MARGIN = 56                  # safe area for text

SYS = "/System/Library/Fonts/"
SUP = SYS + "Supplemental/"
# (path, ttc index) - only fonts that ship with macOS
SERIF_FONTS = [(SUP + "Didot.ttc", 2), (SUP + "Bodoni 72.ttc", 2), (SUP + "Baskerville.ttc", 4),
               (SUP + "Times New Roman Bold.ttf", 0)]
CAPS_FONTS = [(SUP + "Futura.ttc", 4), (SYS + "Avenir Next Condensed.ttc", 8),
              (SUP + "DIN Condensed Bold.ttf", 0), (SUP + "Impact.ttf", 0)]
SUB_FONTS = [(SYS + "Avenir Next Condensed.ttc", 2), (SUP + "Futura.ttc", 0), (SUP + "Impact.ttf", 0)]

# topic keywords -> strong subjects
TOPICS = [
    (r"fight|war|punch|battle|ali|champion|boxer|sting|tough", ["boxer black and white", "boxing training dark", "mma fighter portrait"]),
    (r"disciplin|effort|work|muscle|grind|harder|train|strength|strong", ["bodybuilder dark gym", "athlete training dark", "man workout black and white"]),
    (r"storm|warrior|spartan|legacy|remember|unbroken|unyielding", ["greek statue dark", "warrior statue", "gladiator"]),
    (r"lion|courage|beast|freak", ["lion portrait dark", "lion black and white"]),
    (r"run|lap|forward|going|further|moving|unstoppable", ["runner night", "sprinter black and white", "running athlete dark"]),
    (r"star|dream|vision|believe|shine|genius|potential", ["man looking up night", "man silhouette night sky", "man portrait dramatic light"]),
    (r"climb|higher|rise|mountain|made it|obstacle", ["mountain climber", "man on mountain summit", "rock climbing black and white"]),
    (r"mind|control|meaning|responsib|mental|think|why", ["man thinking dark portrait", "man face shadow", "man portrait black and white"]),
    (r"fear|alone|dark", ["man in the dark portrait", "man face shadow dramatic"]),
    (r"surviv|soldier|born ready|ready", ["soldier training", "military training", "soldier portrait"]),
    (r"build|brick|foundation", ["construction worker portrait", "man lifting weights dark"]),
    (r"happi|bless|grant|true to yourself|unique|odd", ["man portrait black and white", "man sunrise silhouette"]),
    (r"success|win|best|great|achieve", ["athlete portrait dark", "sprinter starting blocks", "boxer black and white"]),
]
POOL = ["boxer black and white", "athlete portrait dark", "soldier training", "greek statue dark",
        "man determined face", "bodybuilder dark gym", "runner night", "man portrait black and white",
        "mma fighter portrait", "boxing training dark", "sprinter black and white", "man face shadow dramatic",
        "basketball player dark", "man hoodie dark portrait"]
PEOPLE = re.compile(r"\b(man|men|woman|women|person|people|boy|guy|athlete|boxer|fighter|soldier|runner|"
                    r"sprinter|player|bodybuilder|statue|sculpture|warrior|climber|lion|face|portrait|his|her|"
                    r"he|she|male|female|gladiator|swimmer|lifter|someone)\b", re.I)
# alt texts that do not fit the dark, determined look (smiling volleyball players, office stock...)
OFF_THEME = re.compile(r"\b(smil\w*|laugh\w*|happy|cheerful|joy\w*|fun|funny|playful|selfie|child|children|kids?|"
                       r"baby|toddler|girl|wedding|bride|couple|romantic|kiss\w*|party|vacation|holiday|"
                       r"family|office|laptop|business\w*|meeting|christmas|toy|cute|puppy|kitten|cat|food|"
                       r"flowers?|makeup|fashion|model posing|bikini|yoga|meditat\w*|casual|shopping)\b", re.I)
ALT_STOP = {"a", "an", "the", "of", "in", "on", "at", "with", "and", "photo", "black", "white", "man", "woman",
            "person", "portrait", "dark", "close", "up", "his", "her", "while", "wearing", "background"}
SMALL = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "and", "or", "by", "with", "as"}
DANGLING = {"a", "an", "the", "to", "of", "for", "and", "or", "by", "with", "your", "my"}  # never end a title


# ---------------------------------------------------------------- Pexels
def _key():
    import make_video as mv
    return mv.pexels_key()


@contextmanager
def _locked(path):
    PHOTOS.mkdir(exist_ok=True)
    with open(str(path) + ".lock", "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def _write(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def search(query, orientation, page=1):
    """Pexels photo search (cached on disk)."""
    ck = f"{query}|{orientation}|{page}"
    with _locked(SEARCH_CACHE):
        cache = _read(SEARCH_CACHE, {})
        if ck in cache:
            return cache[ck]
    import requests
    r = requests.get(API, headers={"Authorization": _key()}, timeout=20,
                     params={"query": query, "orientation": orientation, "per_page": 40, "page": page})
    r.raise_for_status()
    photos = [{"id": p["id"], "alt": p.get("alt") or "", "photographer": p.get("photographer", ""),
               "url": p.get("url", ""), "avg_color": p.get("avg_color") or "#808080",
               "src": p["src"].get("large2x") or p["src"]["large"], "w": p["width"], "h": p["height"]}
              for p in r.json().get("photos", [])]
    with _locked(SEARCH_CACHE):
        cache = _read(SEARCH_CACHE, {})
        cache[ck] = photos
        _write(SEARCH_CACHE, cache)
    return photos


def download(photo):
    path = PHOTOS / f"{photo['id']}.jpg"
    if not path.exists():
        import requests
        r = requests.get(photo["src"], timeout=60)
        r.raise_for_status()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(r.content)
        os.replace(tmp, path)
    return path


def _score(p, rng):
    lum = int(p["avg_color"][1:3], 16) * .3 + int(p["avg_color"][3:5], 16) * .59 + int(p["avg_color"][5:7], 16) * .11
    return (3 if PEOPLE.search(p["alt"]) else 0) + (1 - lum / 255) * 2 + rng.random() * 1.5


QUALITY = PHOTOS / "quality.json"


def quality(photo):
    """Downloaded-photo check (cached): faces, face size, brightness, contrast."""
    key = str(photo["id"])
    with _locked(QUALITY):
        q = _read(QUALITY, {}).get(key)
    if q:
        return q
    img = Image.open(download(photo)).convert("RGB")
    f = faces(img)
    g = np.asarray(img.convert("L").resize((160, int(160 * img.height / img.width))), dtype=float) / 255
    q = {"faces": len(f), "face": max((w * h for _, _, w, h in f), default=0) / (img.width * img.height),
         "mean": round(float(g.mean()), 3), "std": round(float(g.std()), 3)}
    with _locked(QUALITY):
        allq = _read(QUALITY, {})
        allq[key] = q
        _write(QUALITY, allq)
    return q


def _try_download(photo):
    try:
        return download(photo)
    except Exception:
        return None


def _rate(p, q, rng):
    """Higher = better thumbnail material: a visible face, moody but not black, contrasty."""
    s = _score(p, rng)
    if q["faces"]:
        s += 3 + min(q["face"], 0.08) * 40
    s -= abs(q["mean"] - 0.3) * 6          # too bright or nearly black
    s += min(q["std"], 0.3) * 6
    return s


def pick_photos(queries, n, orientation, owner, rng, max_pages=3, check=5):
    """n unused photos, one per query, the best of the `check` top-ranked candidates."""
    picked, ids = [], set()
    with _locked(USED):
        used = _read(USED, {})
    taken = {int(k) for k, v in used.items() if v != owner}
    try:
        taken_info = _used_info(taken)
    except Exception:
        taken_info = []
    for q in queries:
        if len(picked) >= n:
            break
        for page in range(1, max_pages + 1):
            cands = [p for p in search(q, orientation, page) if p["id"] not in taken and p["id"] not in ids
                     and not OFF_THEME.search(p.get("alt") or "")]
            # never the same photographer twice on one thumbnail; no repeat of a used photo session
            cands = [p for p in cands if not any(p.get("photographer") and p.get("photographer") == o.get("photographer")
                                                 for o in picked) and not _same_shoot(p, taken_info)]
            if orientation == "portrait":
                cands = [p for p in cands if p["h"] >= p["w"]]
            elif orientation == "landscape":
                cands = [p for p in cands if p["w"] > p["h"] * 1.2]
            if cands:
                top = sorted(cands, key=lambda p: -_score(p, rng))[:check]
                with ThreadPoolExecutor(check) as ex:      # fetch candidates in parallel
                    list(ex.map(_try_download, top))
                rated = []
                for p in top:
                    try:
                        rated.append((_rate(p, quality(p), rng), p))
                    except Exception:
                        continue
                if rated:
                    best = max(rated, key=lambda t: t[0])[1]
                    picked.append(dict(best, query=q)); ids.add(best["id"])
                    break
    return picked


def _alt_words(alt):
    return {w for w in re.findall(r"[a-z]+", alt.lower()) if w not in ALT_STOP and len(w) > 2}


def _same_shoot(p, others):
    """Same photographer and a similar description: very likely the same model / photo session."""
    a = _alt_words(p.get("alt", ""))
    for o in others:
        if o.get("photographer") and o.get("photographer") == p.get("photographer"):
            b = _alt_words(o.get("alt", ""))
            if not a or not b or len(a & b) / len(a | b) >= 0.4:
                return True
    return False


def _used_info(taken):
    """photographer + alt of photos already used by other thumbnails (looked up in the search cache)."""
    with _locked(SEARCH_CACHE):
        cache = _read(SEARCH_CACHE, {})
    info = {}
    for res in cache.values():
        for p in res if isinstance(res, list) else []:
            if p.get("id") in taken:
                info[p["id"]] = p
    return list(info.values())


def claim(photos, owner):
    with _locked(USED):
        used = {k: v for k, v in _read(USED, {}).items() if v != owner}
        for p in photos:
            used[str(p["id"])] = owner
        _write(USED, used)


def queries_for(meta, rng):
    text = f"{meta.get('title_en', '')} {meta.get('thumbnail_text', '')} {meta.get('source_title', '')}".lower()
    topic = [q for rx, qs in TOPICS if re.search(rx, text) for q in qs]
    rng.shuffle(topic)
    pool = [q for q in POOL if q not in topic]
    rng.shuffle(pool)
    # topic subject first, then a different strong subject for collage variety
    out = []
    for q in topic[:2] + pool[:1] + topic[2:] + pool[1:]:
        if q not in out:
            out.append(q)
    return out


# ---------------------------------------------------------------- image
_CASCADES = None


def faces(img):
    """Face boxes (x, y, w, h) in img pixels; [] if OpenCV is unavailable."""
    global _CASCADES
    try:
        import cv2
        cv2.CascadeClassifier
    except (ImportError, AttributeError):
        return []
    if _CASCADES is None:
        _CASCADES = [cv2.CascadeClassifier(cv2.data.haarcascades + n)
                     for n in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")]
    small = img.convert("L")
    s = 640 / max(small.size)
    small = small.resize((int(small.width * s), int(small.height * s)))
    a = np.asarray(small)
    found = []
    for c in _CASCADES:
        for (x, y, w, h) in c.detectMultiScale(a, 1.1, 6, minSize=(36, 36)):
            found.append((x / s, y / s, w / s, h / s))
        if found:
            break
    return sorted(found, key=lambda f: -f[2] * f[3])[:3]


def focus_point(img):
    """(fx, fy) in 0..1: biggest face, else centre of detail (edge energy), biased upwards."""
    f = faces(img)
    if f:
        x, y, w, h = f[0]
        return (x + w / 2) / img.width, (y + h / 2) / img.height, True
    g = img.convert("L").resize((96, int(96 * img.height / img.width)))
    e = np.asarray(g.filter(ImageFilter.FIND_EDGES), dtype=float)
    e[:2, :] = e[-2:, :] = 0; e[:, :2] = e[:, -2:] = 0
    tot = e.sum() or 1
    ys, xs = np.mgrid[0:e.shape[0], 0:e.shape[1]]
    fx = (e * xs).sum() / tot / e.shape[1]
    fy = (e * ys).sum() / tot / e.shape[0]
    return float(fx), float(fy) * 0.85, False


def cover(img, size, focus, place=(0.5, 0.4)):
    """Scale to cover `size` and crop so the focus lands at `place` (fractions of the panel)."""
    tw, th = size
    s = max(tw / img.width, th / img.height)
    if focus[2]:      # a face: zoom in a little so it reads at small size
        s *= 1.12
    im = img.resize((max(tw, round(img.width * s)), max(th, round(img.height * s))), Image.LANCZOS)
    x = int(focus[0] * im.width - place[0] * tw)
    y = int(focus[1] * im.height - place[1] * th)
    x = min(max(0, x), im.width - tw)
    y = min(max(0, y), im.height - th)
    return im.crop((x, y, x + tw, y + th))


def grade(img, look, rng):
    """Desaturate, strong contrast, crushed blacks; optional subtle tint."""
    g = ImageOps.autocontrast(img.convert("L"), cutoff=(1.5, 0.6))
    a = np.asarray(g, dtype=np.float32) / 255
    m = float(a.mean())
    if 0.02 < m < 0.98:                             # normalise exposure towards a moody mid-dark
        a = a ** float(np.clip(np.log(0.34) / np.log(m), 0.6, 1.5))
    a = np.clip((a - 0.07) / 0.93, 0, 1)            # crush blacks
    a = a ** 1.18                                   # darker mids
    a = a * a * (3 - 2 * a) * 0.55 + a * 0.45       # S-curve
    rgb = np.stack([a, a, a], -1)
    if look == "cold":
        rgb = rgb * np.array([0.92, 0.98, 1.06]) + (1 - a[..., None]) * np.array([0, 0.012, 0.03])
    elif look == "warm":
        rgb = rgb * np.array([1.06, 0.99, 0.88])
    elif look == "bleach":                         # a hint of the original colour
        col = np.asarray(img.convert("RGB"), dtype=np.float32) / 255
        rgb = rgb * 0.8 + col * a[..., None] * 0.2
    return Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))


def finish(canvas, rng, text_box=None):
    """Vignette, darken behind the text, film grain."""
    a = np.asarray(canvas, dtype=np.float32) / 255
    ys, xs = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xs - W / 2) / (W / 2)) ** 2 + ((ys - H / 2) / (H / 2)) ** 2)
    shade = 1 - 0.55 * np.clip(d - 0.45, 0, 1) ** 1.5
    if text_box:
        x0, y0, x1, y1 = text_box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = max(1, (x1 - x0) * 0.75), max(1, (y1 - y0) * 0.95)
        t = np.sqrt(((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2)
        shade *= 1 - 0.42 * np.clip(1.15 - t, 0, 1) ** 1.2
    a = a * shade[..., None]
    a += np.random.default_rng(rng.randrange(1 << 30)).normal(0, 0.018, (H, W, 1))
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


# ---------------------------------------------------------------- text
def phrase(meta):
    t = re.sub(r"[^A-Za-z' ]", " ", str(meta.get("thumbnail_text") or "")).split()
    if not t:   # fall back to the main clause of the title
        head = re.split(r"[:|\-–—►!?]", str(meta.get("title_en") or ""))[0]
        t = re.sub(r"[^A-Za-z' ]", " ", head).split() or ["Never", "Give", "Up"]
    t = t[:4]
    while len(t) > 1 and t[-1].lower() in DANGLING:    # "HEAD STRAIGHT TO THE" -> "HEAD STRAIGHT"
        t.pop()
    return " ".join(t)


def title_case(words):
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        out.append(lw if i and lw in SMALL else lw[:1].upper() + lw[1:])
    return out


def split_lines(words, max_lines=2):
    """Split words into up to 2 lines with the most even character length."""
    if len(words) < 2 or max_lines < 2:
        return [" ".join(words)]
    best = min(range(1, len(words)),
               key=lambda i: (max(len(" ".join(words[:i])), len(" ".join(words[i:]))), -i))
    return [" ".join(words[:best]), " ".join(words[best:])]


def font(spec, size):
    return ImageFont.truetype(spec[0], size, index=spec[1])


def ink(f, s):
    b = f.getbbox(s)
    return b, b[2] - b[0], b[3] - b[1]


def layout_text(lines, spec, box_w, box_h, mode, max_size=230, min_size=40):
    """Choose sizes so the block fits box_w x box_h. Returns [(line, size)], block w, block h.
    mode 'caps': each line scaled to the block width (justified block, capped at 1.55x of the
    smallest line); 'serif': one common size."""
    def measure(sizes):
        dims = [ink(font(spec, s), ln)[1:] for ln, s in zip(lines, sizes)]
        gap = [int(s * (0.10 if mode == "caps" else 0.16)) for s in sizes]
        return max(d[0] for d in dims), sum(d[1] for d in dims) + sum(gap[1:])
    size = max_size
    while size >= min_size:
        if mode == "caps" and len(lines) > 1:
            widths = [ink(font(spec, 100), ln)[1] for ln in lines]
            target = min(box_w, max(widths) * size / 100)
            sizes = [int(min(size * 1.35, 100 * target / w)) for w in widths]
            sizes = [min(s, int(min(sizes) * 1.35)) for s in sizes]
        else:
            sizes = [size] * len(lines)
        bw, bh = measure(sizes)
        if bw <= box_w and bh <= box_h:
            return list(zip(lines, sizes)), bw, bh
        size -= 4
    sizes = [min_size] * len(lines)
    return list(zip(lines, sizes)), *measure(sizes)


def draw_text(canvas, placed, spec, mode, anchor_x, top, align, sub=None):
    """Render lines (+ optional spaced subtitle) with a soft shadow. Returns the ink box."""
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    y = top
    x0s, x1s = [], []
    for ln, s in placed:
        f = font(spec, s)
        b, w, h = ink(f, ln)
        x = anchor_x - w / 2 if align == "center" else (anchor_x - w if align == "right" else anchor_x)
        d.text((x - b[0], y - b[1]), ln, font=f, fill=255)
        x0s.append(x); x1s.append(x + w)
        y += h + int(s * (0.10 if mode == "caps" else 0.16))
    y -= int(placed[-1][1] * (0.10 if mode == "caps" else 0.16))
    if sub:
        sf = font(SUB_FONTS[0], max(22, min(34, int(placed[-1][1] * 0.2))))
        track = int(sf.size * 0.42)
        widths = [ink(sf, c)[1] if c != " " else sf.size * 0.3 for c in sub]
        tw = sum(widths) + track * (len(sub) - 1)
        y += int(sf.size * 1.0)
        sx = anchor_x - tw / 2 if align == "center" else (anchor_x - tw if align == "right" else anchor_x)
        cap = ink(sf, "H")
        for c, cw in zip(sub, widths):
            if c != " ":
                b = sf.getbbox(c)
                d.text((sx - b[0], y - cap[0][1]), c, font=sf, fill=235)
            sx += cw + track
        if align == "center":   # thin rules either side of the subtitle
            ly = y + cap[2] // 2
            for lx0, lx1 in ((anchor_x - tw / 2 - 70, anchor_x - tw / 2 - 18),
                             (anchor_x + tw / 2 + 18, anchor_x + tw / 2 + 70)):
                d.line((lx0, ly, lx1, ly), fill=200, width=2)
        x0s.append(anchor_x - tw / 2 if align == "center" else sx - tw); x1s.append(sx)
        y += cap[2]
    size = placed[0][1]
    shadow = mask.filter(ImageFilter.GaussianBlur(max(6, size // 9)))
    off = max(3, size // 28)
    shadow = Image.fromarray((np.clip(np.asarray(shadow, dtype=np.float32) * 1.6, 0, 255)).astype(np.uint8))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(black, (0, off), shadow.point(lambda v: int(v * 0.9)))
    if mode == "caps":    # crisp thin dark edge keeps letters readable on bright spots
        edge = mask.filter(ImageFilter.MaxFilter(5))
        canvas.paste(black, (0, 0), edge.point(lambda v: int(v * 0.55)))
    canvas.paste(Image.new("RGB", (W, H), (246, 246, 244)), (0, 0), mask)
    return int(min(x0s)), int(top), int(max(x1s)), int(y)


# ---------------------------------------------------------------- compose
LAYOUTS = ("trio", "hero", "duo")


def variant(meta, seed=None):
    """(layout, text style, look) - cycles through all combos over consecutive videos."""
    m = re.match(r"(\d+)_", str(meta.get("video") or meta.get("thumbnail") or ""))
    n = int(m.group(1)) if m else zlib.crc32(str(meta.get("title_en", "")).encode())
    if seed is not None:
        n += seed
    layout = LAYOUTS[n % 3]
    style = "serif" if n % 2 else "caps"      # with n % 3 above: all 6 combos every 6 videos
    look = ("mono", "cold", "mono", "warm", "bleach")[n % 5]
    return layout, style, look, n


def panels(layout):
    if layout == "trio":
        w = (W - 2 * GAP) // 3
        return [(0, w), (w + GAP, w), (2 * (w + GAP), W - 2 * (w + GAP))]
    if layout == "duo":
        w = (W - GAP) // 2
        return [(0, w), (w + GAP, W - w - GAP)]
    return [(0, W)]


def _overlap(box, face_boxes):
    """Fraction of total face area covered by box (faces shrunk to their core)."""
    x0, y0, x1, y1 = box
    tot = cov = 0.0
    for fx, fy, fw, fh in face_boxes:
        cx0, cy0, cx1, cy1 = fx + fw * .15, fy + fh * .1, fx + fw * .85, fy + fh * .95
        tot += (cx1 - cx0) * (cy1 - cy0)
        cov += max(0, min(x1, cx1) - max(x0, cx0)) * max(0, min(y1, cy1) - max(y0, cy0))
    return cov / tot if tot else 0.0


def text_lines(words, style):
    """Line options for the title: one line and a balanced two-line split."""
    if style == "serif":
        words = title_case(words)
        words[-1] += "."
        opts = [[" ".join(words)]]
    else:
        words = [w.upper() for w in words]
        opts = []
    if len(words) > 1:
        opts.append(split_lines(words))
    return opts or [[" ".join(words)]]


def plan_text(options, spec, mode, layout, side, face_boxes, sub):
    """Pick size + position for the title so it stays inside the frame and off faces.
    Returns dict(placed, anchor, top, align, box)."""
    sub_h = 60 if sub else 0
    cands = []    # (align, anchor, box_w, box_h, vertical centre as fraction of H)
    big_h = int(H * (0.46 if mode == "caps" else 0.42)) - sub_h
    if layout == "hero":
        other = "left" if side == "right" else "right"
        for al in (side, other):
            anc = W - MARGIN if al == "right" else MARGIN
            for cy in (0.5, 0.3, 0.7):
                cands.append((al, anc, int(W * (0.52 if mode == "caps" else 0.46)), big_h, cy))
        cands.append(("center", W // 2, W - 2 * MARGIN, int(H * 0.30) - sub_h // 2, 0.80))
    else:
        for cy in ((0.60, 0.70, 0.50) if mode == "caps" else (0.62, 0.74, 0.50)):
            cands.append(("center", W // 2, W - 2 * MARGIN - 40, big_h, cy))
        cands.append(("center", W // 2, W - 2 * MARGIN - 40, int(H * 0.30) - sub_h // 2, 0.80))
    best = None
    trials = [(i, c, lines) for i, c in enumerate(cands) for lines in options]
    for i, (al, anc, bw_max, bh_max, cy), lines in trials:
        placed, bw, bh = layout_text(lines, spec, bw_max, bh_max, mode,
                                     max_size=210 if mode == "caps" else 180)
        total = bh + (int(placed[-1][1] * 0.2) * 2 + 34 if sub else 0)
        top = int(H * cy - total / 2) + (10 if layout == "hero" and cy == 0.5 else 0)
        top = max(MARGIN - 20, min(top, H - MARGIN - total))
        x0 = anc - bw / 2 if al == "center" else (anc - bw if al == "right" else anc)
        box = (x0, top, x0 + bw, top + total)
        # small text is a cost too: prefer early (designed) slots, big size, no face overlap
        cost = _overlap(box, face_boxes) * 10 + i * 0.15 + (1 - min(s for _, s in placed) / 210) * 4 \
            + max(0, 95 - min(s for _, s in placed)) / 8           # tiny text is worse than a grazed face
        if best is None or cost < best[0]:
            best = (cost, dict(placed=placed, anchor=anc, top=top, align=al, box=box))
    return best[1]


def compose(images, layout, style, look, words, sub, rng):
    """images: list of PIL images (len == number of panels). Returns the final RGB thumbnail."""
    canvas = Image.new("RGB", (W, H), (8, 8, 8))
    side, face_boxes = "right", []
    for (x, w), img in zip(panels(layout), images):
        fp = focus_point(img)
        if layout == "hero":
            # subject on one third, text on the other side
            side = "right" if fp[0] < 0.55 else "left"
            place = (0.33 if side == "right" else 0.67, 0.42)
        else:
            place = (0.5, 0.36)
        panel = cover(img, (w, H), fp, place)
        face_boxes += [(fx + x, fy, fw, fh) for fx, fy, fw, fh in faces(panel)]
        canvas.paste(grade(panel, look, rng), (x, 0))
    if layout == "hero" and face_boxes:
        fx, _, fw, _ = max(face_boxes, key=lambda f: f[2] * f[3])
        side = "right" if fx + fw / 2 < W / 2 else "left"
    if style == "serif":
        mode, spec = "serif", SERIF_FONTS[rng.randrange(3)]
    else:
        mode, spec = "caps", CAPS_FONTS[rng.randrange(2)]
    plan = plan_text(text_lines(words, style), spec, mode, layout, side, face_boxes, sub)
    canvas = finish(canvas, rng, plan["box"])
    draw_text(canvas, plan["placed"], spec, mode, plan["anchor"], plan["top"], plan["align"], sub)
    return canvas


def make_thumbnail(meta, dest, seed=None, claim_photos=True):
    """Build the thumbnail for a publish meta dict and write it atomically to dest.
    Uses meta["thumbnail_text"] as the title (already validated by publish_meta).
    Adds meta["thumbnail_photos"] (Pexels ids + photographer credits). Raises on failure.
    claim_photos=False (previews): do not record the photos in used.json."""
    dest = Path(dest)
    layout, style, look, n = variant(meta, seed)
    rng = random.Random(n * 7919 + (seed or 0))
    owner = str(meta.get("video") or dest.stem)
    queries = queries_for(meta, rng)
    count = len(panels(layout))
    orient = "landscape" if layout == "hero" else "portrait"
    photos = pick_photos(queries, count, orient, owner, rng)
    while len(photos) < count:          # not enough distinct photos: simpler layout
        layout = {"trio": "duo", "duo": "hero"}.get(layout)
        if not layout:
            raise RuntimeError("no Pexels photos found")
        count = len(panels(layout))
        orient = "landscape" if layout == "hero" else "portrait"
        photos = pick_photos(queries, count, orient, owner, rng)
    images = [Image.open(download(p)).convert("RGB") for p in photos]
    src = str(meta.get("speech_source") or "").strip()
    sub = None
    if rng.random() < 0.6:     # the Latin fonts have no Japanese glyphs ("要確認" = unknown source)
        ok = src and len(src) <= 24 and src.isascii()
        sub = src.upper() if ok else "MOTIVATIONAL SPEECH"
    img = compose(images, layout, style, look, phrase(meta).split(), sub, rng)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".tmp.jpg")
    img.save(tmp, "JPEG", quality=92, optimize=True, progressive=True)
    os.replace(tmp, dest)
    if claim_photos:
        claim(photos, owner)
    meta["thumbnail_photos"] = [{"id": p["id"], "photographer": p["photographer"], "url": p["url"]} for p in photos]
    meta["thumbnail_style"] = f"{layout}/{style}/{look}"
    return dest


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("meta", nargs="+")
    ap.add_argument("--out", help="write here instead of next to the json (preview)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--save-meta", action="store_true", help="store thumbnail_photos in the json")
    a = ap.parse_args()
    for mp in a.meta:
        mp = Path(mp)
        meta = json.loads(mp.read_text())
        dest = (Path(a.out) / meta["thumbnail"]) if a.out else mp.parent / meta["thumbnail"]
        try:
            make_thumbnail(meta, dest, a.seed)
            if a.save_meta and not a.out:
                tmp = mp.with_suffix(".tmp")
                tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
                os.replace(tmp, mp)
            print(f"+ {dest.name}  [{meta['thumbnail_style']}]", flush=True)
        except Exception as e:
            print(f"! {mp.name}: {e.__class__.__name__}: {e}", file=sys.stderr, flush=True)
