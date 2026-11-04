"""Original black-and-white ink illustrations for script videos (local FLUX.1-schnell).

The look of long-form motivational channels (monochrome manga / gekiga panels with a
slow zoom) without using anyone's anime or manga: every image is generated from our own
prompt with an original recurring character. Existing character, series and artist names
are removed from prompts. FLUX.1-schnell is Apache-2.0 (fine for monetised videos).

Runs mflux in its own venv (tools/mflux_venv) through tools/flux_batch.py, so the model
is loaded once per video. Images are cached by prompt + seed, so a re-run after editing
the script only draws the changed scenes.
"""
import hashlib, json, re, subprocess, urllib.request
from pathlib import Path

import make_video as mv

HERE = Path(__file__).resolve().parent
FLUX_PY = HERE / "tools" / "mflux_venv" / "bin" / "python"
BATCH = HERE / "tools" / "flux_batch.py"
MODEL = "dhairyashil/FLUX.1-schnell-mflux-4bit"
MODEL_DIR = HERE / "tools" / "models" / "flux1-schnell-mflux-4bit"   # tools/fetch_flux.py
# FLUX.2 [klein] 4B (Apache-2.0): the default. 2-4x faster than FLUX.1-schnell here (~10 s an
# image), half the disk, cleaner screentone ink and no stray corner text in tests.
KLEIN_DIR = HERE / "tools" / "models" / "flux2-klein-4b-mflux-4bit"
KLEIN = "Runpod/FLUX.2-klein-4B-mflux-4bit"
CACHE = mv.MEDIA / "ai_illustrations"
SETUP = ("uv venv --python 3.11 tools/mflux_venv && uv pip install --python tools/mflux_venv/bin/python mflux"
         " && venv/bin/python tools/fetch_flux.py")

STYLE = ("single full-bleed illustration filling the entire frame edge to edge, no panels, no borders, no frames, "
         "black and white ink manga illustration, seinen gekiga style, heavy ink hatching, "
         "strong chiaroscuro shadows, high contrast monochrome, cinematic composition, "
         "dramatic lighting, detailed background, clean empty margins, no text, no letters, no writing, "
         "no handwriting, no signature, no page number, no logo, no caption, no speech bubbles, no watermark")
HERO = "a stoic young man with short black hair and a long dark coat"
SHOTS = ["wide shot", "close-up", "low angle shot", "silhouette", "over the shoulder shot", "medium shot"]

# names of existing works / characters / artists never go into a prompt
BANNED = re.compile(r"\b(" + "|".join([
    "goku", "vegeta", "naruto", "sasuke", "itachi", "luffy", "zoro", "gojo", "sukuna", "itadori",
    "guts", "griffith", "berserk", "vagabond", "miura", "inoue", "toriyama", "oda", "kishimoto",
    "eren", "levi", "mikasa", "attack on titan", "isayama", "baki", "hanma", "yujiro", "jojo",
    "araki", "dragon ball", "one piece", "bleach", "ichigo", "tanjiro", "demon slayer", "saitama",
    "one punch", "light yagami", "death note", "ghibli", "miyazaki", "batman", "spider-man",
    "superman", "marvel", "disney", "pixar", "anime screenshot",
]) + r")\b", re.I)


def engine(s=None):
    """('flux2', dir, steps) for klein when it is downloaded (and not turned off), else FLUX.1."""
    want = (s or {}).get("image_model", "klein")
    klein, schnell = (KLEIN_DIR / ".complete").exists(), (MODEL_DIR / ".complete").exists()
    if klein and (want != "schnell" or not schnell):     # schnell asked for but not on disk: klein
        return "flux2", KLEIN_DIR, 4
    return "flux1", MODEL_DIR, int((s or {}).get("image_steps", 2))


def model_ready():
    """Weights fully downloaded (fetch_flux.py writes .complete after the last file)."""
    return (KLEIN_DIR / ".complete").exists() or (MODEL_DIR / ".complete").exists()


def available():
    return FLUX_PY.exists() and BATCH.exists() and model_ready()


def trim_bands(png, out):
    """Cut flat black/white bands off the sides (klein likes to draw a narrow panel with bars
    around it). Writes the trimmed image to `out` and returns it, or the original if fine."""
    from PIL import Image
    import numpy as np
    im = Image.open(png).convert("L")
    a = np.asarray(im, np.float32)
    h, w = a.shape

    MIN_RUN = 12                                 # the drawing starts where texture lasts this long

    def inner_edge(texture):
        """First index, walking in from the edge, where a long textured stretch begins (bars and
        margins have no texture; the thin seams between them are too short to count)."""
        n = 0
        for i, on in enumerate(texture):
            n = n + 1 if on else 0
            if n >= MIN_RUN:
                return i - MIN_RUN + 1
        return 0

    def plain(strip, axis=0):
        """What we'd cut: made of flat black or white lines (a white margin then a black bar is
        fine), allowing a few seam lines between them? Lines run along `axis`."""
        if strip.size == 0:
            return True
        # a line is flat when 97 % of it is one black or white tone (thin frame lines crossing a
        # bar are fine); a seam or two between margin and bar is allowed
        dark = (strip < 35).mean(axis=axis) >= 0.97
        light = (strip > 220).mean(axis=axis) >= 0.97
        return (~(dark | light)).sum() <= 4
    # texture = mean absolute difference to the neighbour, measured in the middle of the image
    cols = np.abs(np.diff(a[h // 5: h - h // 5], axis=1)).mean(axis=0) > 2.0
    rows = np.abs(np.diff(a[:, w // 5: w - w // 5], axis=0)).mean(axis=1) > 2.0
    left, right = inner_edge(cols), w - 1 - inner_edge(cols[::-1])
    top, bottom = inner_edge(rows), h - 1 - inner_edge(rows[::-1])
    # a side is cut only when everything outside the drawing there is a flat field (a dark night
    # sky or a black coat at the edge is part of the picture and stays)
    mid = a[top:bottom + 1]
    if not plain(mid[:, :left], axis=0):         # columns of the left strip
        left = 0
    if not plain(mid[:, right + 1:], axis=0):
        right = w - 1
    if not plain(a[:top, left:right + 1], axis=1):   # rows of the top strip
        top = 0
    if not plain(a[bottom + 1:, left:right + 1], axis=1):
        bottom = h - 1
    # a wide flat field on ONE side is negative space in the picture (a dark void beside a
    # face), not a bar: wide cuts only come in pairs (pillar/letterbox), single sides only
    # lose a thin margin
    def limit(a_cut, b_cut, size):
        thin = 0.08 * size
        if a_cut > thin and b_cut > thin:
            return a_cut, b_cut
        return min(a_cut, thin) if a_cut <= thin else 0, min(b_cut, thin) if b_cut <= thin else 0
    lc, rc = limit(left, w - 1 - right, w)
    tc, bc = limit(top, h - 1 - bottom, h)
    left, right, top, bottom = int(lc), int(w - 1 - rc), int(tc), int(h - 1 - bc)
    if (right - left + 1) > 0.95 * w and (bottom - top + 1) > 0.95 * h:
        return png
    if (right - left + 1) < 0.3 * w or (bottom - top + 1) < 0.3 * h:
        return png                               # nearly blank: leave it, don't zoom into a sliver
    Image.open(png).crop((left, top, right + 1, bottom + 1)).save(out)
    return out


def clean(prompt):
    p = BANNED.sub("", str(prompt))
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.])", r"\1", p)).strip(" ,.")


def llm_prompts(scenes, hero, model, log=print):
    """One short visual description per scene from the local LLM, or None."""
    import prep
    try:
        prep.ensure_ollama(log)
        numbered = "\n".join(f"{i + 1}. {sc['text'][:500]}" for i, sc in enumerate(scenes))
        prompt = (
            "You direct the illustrations of a black-and-white motivational video. For each numbered "
            "narration paragraph write ONE visual scene (12-25 English words) for an ink illustration. "
            f"Use the recurring main character, '{hero}', or a strong symbolic image (storm, lion, "
            "chains, mountain, empty road, burning candle, church light, chessboard, battlefield). "
            "Describe only what is visible: pose, place, light, weather. No names of real people, "
            "no existing anime/manga/film characters, no text in the image. "
            f"Answer as a JSON list of exactly {len(scenes)} strings.\n\n{numbered}")
        body = json.dumps({"model": model, "stream": False, "keep_alive": 0, "format": "json",
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(prep.OLLAMA, body, {"Content-Type": "application/json"})
        out = json.loads(json.load(urllib.request.urlopen(req, timeout=300))["message"]["content"])
        if isinstance(out, dict):
            out = next((v for v in out.values() if isinstance(v, list)), [])
        out = [clean(x) for x in out]
        return out if len(out) == len(scenes) and all(out) else None
    except Exception as e:
        log(f"  ! AI で絵の指示文を作れませんでした({e})。キーワードで代用します")
        return None


def keyword_prompt(sc, hero):
    import story
    q = sc.get("query") or story.keyword_query(sc["text"], ["lonely road at night"])
    return f"{hero}, {q}, moody atmosphere"


def plan(scenes, s, log=print):
    """Fill sc['image_prompt'] for every scene (script value > LLM > keywords)."""
    hero = clean(s.get("hero") or HERO)
    need = [i for i, sc in enumerate(scenes) if not sc.get("image_prompt")]
    got = llm_prompts([scenes[i] for i in need], hero, s["llm_model"], log) if need else None
    for j, i in enumerate(need):
        scenes[i]["image_prompt"] = got[j] if got else keyword_prompt(scenes[i], hero)
    for sc in scenes:
        sc["image_prompt"] = clean(sc["image_prompt"])


def size_for(s):
    """FLUX works in multiples of 16. On the 24 GB M5: 960x544 at 2 steps ~22 s an image,
    1024x576 ~44 s, 1280x720 ~105 s (bigger frames push the model into swap). The ink look
    is the same once zoomed, grained and graded."""
    return (544, 960) if s["aspect"] == "9:16" else (960, 544)


def illustrate(scenes, s, log=print):
    """Draw every scene's images. Returns {scene index: [png, ...]} (missing on failure).
    One image per ~s['image_seconds'] of narration, each a different camera shot."""
    plan(scenes, s, log)
    w, h = size_for(s)
    every = float(s.get("image_seconds", 9.0))
    family, _, _ = engine(s)
    name = KLEIN if family == "flux2" else MODEL     # cache per model: never reuse the other's images
    images, per_scene = [], {}
    for i, sc in enumerate(scenes):
        n = max(1, round(sc["dur"] / every))
        for k in range(n):
            shot = SHOTS[(i + k) % len(SHOTS)] if n > 1 or i % 2 else SHOTS[0]
            prompt = f"{sc['image_prompt']}, {shot}, {STYLE}"
            seed = int(hashlib.md5(f"{prompt}|{k}".encode()).hexdigest()[:7], 16)
            key = hashlib.md5(f"{name}|{w}x{h}|{prompt}|{seed}".encode()).hexdigest()[:16]
            out = CACHE / f"{key}.png"
            images.append({"prompt": prompt, "out": str(out), "seed": seed})
            per_scene.setdefault(i, []).append(out)
    CACHE.mkdir(parents=True, exist_ok=True)
    job = CACHE / f"job_{hashlib.md5(json.dumps(images).encode()).hexdigest()[:10]}.json"
    _, model_dir, steps = engine(s)
    mv.write_json(job, {"family": family, "model_path": str(model_dir), "width": w, "height": h, "steps": steps,
                        "images": images})
    log(f"  イラストを生成中: {len(images)} 枚({'FLUX.2 klein 4B' if family == 'flux2' else 'FLUX.1-schnell'}, 作成済みは再利用)")
    proc = subprocess.Popen([str(FLUX_PY), str(BATCH), str(job)], cwd=HERE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail, shown = [], -1
    for line in proc.stdout:
        if line.startswith("MEMWAIT"):
            log("  メモリの空きが少ないので一時停止しています(スワップを防ぐため)。ほかのアプリを閉じると再開します")
        elif line.startswith("GPUWAIT"):
            log("  ほかの重い処理(声または絵)が終わるのを待っています(同時には動かしません)")
        elif line.startswith("MEMOK"):
            log("  メモリが空いたので再開しました")
        elif line.startswith("IMG "):
            done, total = map(int, line.split()[1:3])
            step = max(1, total // 10)
            if done // step > shown or done == total:
                shown = done // step
                log(f"  イラスト {done}/{total}")
        elif line.strip():
            tail = (tail + [line.strip()])[-5:]
    proc.wait()
    job.unlink(missing_ok=True)
    ok = {i: [p for p in ps if p.exists()] for i, ps in per_scene.items()}
    missing = sum(len(ps) for ps in per_scene.values()) - sum(len(ps) for ps in ok.values())
    if missing:
        log(f"  ! イラスト {missing} 枚を作れませんでした({' / '.join(tail[-2:])})。その場面は素材映像を使います")
    return {i: ps for i, ps in ok.items() if ps}
