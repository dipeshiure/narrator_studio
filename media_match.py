"""Match script paragraphs to the user's own footage / photos by meaning (local CLIP).

Why not keywords: narration is abstract ("discipline is choosing what you want most"),
footage is concrete (a boxer at dawn). So each paragraph is first turned into a few short
*visual* descriptions by the local LLM (what a camera would show), then compared with
CLIP (openai/clip-vit-base-patch32, already cached, runs offline on the Apple GPU) against
frames sampled from every clip and every photo. File and folder names are a second signal
(a folder called "rain" is a strong hint). Assignment is greedy in script order with a
penalty for reuse and for repeating the previous shot, so the video does not loop the
same few clips.

The folder index (CLIP vectors + a small thumbnail per frame) is cached in
tmp/media_index/ and updated incrementally when files are added or changed.
"""
import hashlib, json, re, subprocess, urllib.request
from pathlib import Path

import make_video as mv

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}
CLIP_ID = "openai/clip-vit-base-patch32"
INDEX_DIR = mv.TMP / "media_index"
THUMBS = INDEX_DIR / "thumbs"
FRAMES_AT = (0.15, 0.5, 0.85)        # sampled positions in each clip
NAME_WEIGHT = 0.35                   # file/folder name similarity, relative to frames
REUSE_PENALTY = 0.06                 # per earlier use of the same file
REPEAT_PENALTY = 0.10                # same file as the previous shot

_clip = {}


def clip():
    """(model, processor, device), loaded once per process."""
    if not _clip:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        model = CLIPModel.from_pretrained(CLIP_ID, local_files_only=True).to(dev).eval()
        _clip.update(model=model, proc=CLIPProcessor.from_pretrained(CLIP_ID, local_files_only=True), dev=dev)
    return _clip["model"], _clip["proc"], _clip["dev"]


def _norm(t):
    if not hasattr(t, "norm"):          # transformers 5 wraps the projected features
        t = t.pooler_output
    return t / t.norm(dim=-1, keepdim=True)


def embed_text(texts):
    import torch
    model, proc, dev = clip()
    out = []
    for i in range(0, len(texts), 64):
        batch = proc(text=texts[i:i + 64], return_tensors="pt", padding=True, truncation=True, max_length=77)
        with torch.no_grad():
            out.append(_norm(model.get_text_features(**{k: v.to(dev) for k, v in batch.items()})).cpu())
    return torch.cat(out) if out else torch.zeros(0, 512)


def embed_images(images):
    import torch
    model, proc, dev = clip()
    out = []
    for i in range(0, len(images), 32):
        batch = proc(images=images[i:i + 32], return_tensors="pt")
        with torch.no_grad():
            out.append(_norm(model.get_image_features(pixel_values=batch["pixel_values"].to(dev))).cpu())
    return torch.cat(out) if out else torch.zeros(0, 512)


# ---------------------------------------------------------------- folder index

def list_media(folder):
    folder = Path(folder).expanduser()
    files = [p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")
             and p.suffix.lower() in VIDEO_EXT | IMAGE_EXT]
    return sorted(files)


def name_text(path, root):
    """'rain_window/IMG_2041 sad man.mp4' -> 'rain window sad man' (camera numbers dropped)."""
    rel = Path(path).relative_to(root).with_suffix("")
    words = re.split(r"[\W_]+", " ".join(rel.parts))
    camera = {"img", "dsc", "dscf", "dscn", "mov", "vid", "pxl", "gopr", "gp", "mvi", "clip", "video", "copy"}
    words = [w for w in words if w and w.lower() not in camera
             and not re.fullmatch(r"[a-z]{0,4}\d+[a-z]?", w.lower())]
    return " ".join(words).lower()


def _frames(path, kind, dur):
    """PIL frames (and their times) sampled from one file; thumbnails saved for the UI."""
    from PIL import Image
    key = hashlib.md5(f"{path}|{path.stat().st_mtime}".encode()).hexdigest()[:12]
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = []
    times = [0.0] if kind == "image" else [round(dur * f, 2) for f in FRAMES_AT]
    for k, t in enumerate(times):
        thumb = THUMBS / f"{key}_{k}.jpg"
        if not thumb.exists():
            seek = [] if kind == "image" else ["-ss", f"{t:.2f}"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", *seek, "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=336:-2", str(thumb)], check=False)
        if thumb.exists():
            out.append((t, thumb, Image.open(thumb).convert("RGB")))
    return out


def index_folder(folder, log=print):
    """{"root", "items": [{path, kind, dur, name, frames: [{t, thumb, vec}]}]} (cached)."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"フォルダが見つかりません: {root}")
    cache = INDEX_DIR / (hashlib.md5(str(root).encode()).hexdigest()[:12] + ".json")
    old = {it["path"]: it for it in mv.read_json(cache, {}).get("items", [])}
    items, new = [], []
    for p in list_media(root):
        st = p.stat()
        sig = f"{st.st_size}:{int(st.st_mtime)}"
        if str(p) in old and old[str(p)].get("sig") == sig:
            items.append(old[str(p)])
        else:
            new.append((p, sig))
    if new:
        log(f"  素材フォルダを解析しています: 新しいファイル {len(new)} 本(初回だけ時間がかかります)")
    for n, (p, sig) in enumerate(new):
        kind = "video" if p.suffix.lower() in VIDEO_EXT else "image"
        try:
            dur = mv.duration(p) if kind == "video" else 0.0
        except Exception:
            continue
        if kind == "video" and dur < 1.0:
            continue
        frames = _frames(p, kind, dur)
        if not frames:
            continue
        vecs = embed_images([f[2] for f in frames])
        items.append({"path": str(p), "sig": sig, "kind": kind, "dur": round(dur, 2),
                      "name": name_text(p, root),
                      "frames": [{"t": t, "thumb": str(th), "vec": [round(x, 5) for x in v.tolist()]}
                                 for (t, th, _), v in zip(frames, vecs)]})
        if (n + 1) % 25 == 0:
            log(f"  解析 {n + 1}/{len(new)}")
            mv.write_json(cache, {"root": str(root), "items": items})
    data = {"root": str(root), "items": items}
    if new or len(items) != len(old):
        mv.write_json(cache, data)
    return data


# ---------------------------------------------------------------- what each paragraph should show

def _flat(x):
    if isinstance(x, (list, tuple)):
        return [y for v in x for y in _flat(v)]
    if isinstance(x, dict):
        return [y for v in x.values() for y in _flat(v)]
    return [str(x).strip()] if str(x).strip() else []


def _llm_chunk(chunk, model):
    import prep
    numbered = "\n".join(f"{n + 1}. {sc['text'][:500]}" for n, sc in enumerate(chunk))
    prompt = (
        "You pick stock footage for a narrated video. For EACH numbered paragraph separately, write 3 "
        "short descriptions (4-10 English words) of footage that shows its MEANING. The first is the most "
        "direct image of the paragraph; the other two are close variations of the SAME idea (other angle, "
        "place or subject), not unrelated scenes. Use concrete people, "
        "actions, places, objects, weather, light. Turn abstract ideas into images, e.g. loneliness -> "
        "'man walking alone on empty road at night', discipline -> 'athlete training before sunrise', "
        "victory -> 'man raising trophy in stadium', faith -> 'light through church window'. "
        "No text, no names. Answer JSON whose keys are the paragraph numbers: "
        "{\"1\": [\"...\", \"...\", \"...\"], \"2\": [...]}.\n\n" + numbered)
    body = json.dumps({"model": model, "stream": False, "keep_alive": 0, "format": "json", "options": {"temperature": 0.3},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(prep.OLLAMA, body, {"Content-Type": "application/json"})
    out = json.loads(json.load(urllib.request.urlopen(req, timeout=300))["message"]["content"])
    if isinstance(out, dict) and len(out) == 1 and not str(next(iter(out))).strip().isdigit():
        out = next(iter(out.values()))              # {"scenes": {...}} or {"scenes": [...]}
    if isinstance(out, dict):
        res = [_flat(out.get(str(n + 1)) or out.get(n + 1) or [])[:3] for n in range(len(chunk))]
    else:
        res = [_flat(v)[:3] for v in out] if isinstance(out, list) and len(out) == len(chunk) else []
    return res if len(res) == len(chunk) else [[] for _ in chunk]


def llm_visuals(scenes, model, log=print, chunk=10):
    """2-3 concrete camera descriptions per paragraph from the local LLM ([] where it failed)."""
    import prep
    try:
        prep.ensure_ollama(log)
    except Exception as e:
        log(f"  ! AI に接続できません({e})。キーワードで代用します")
        return [[] for _ in scenes]
    out = []
    for i in range(0, len(scenes), chunk):
        try:
            out += _llm_chunk(scenes[i:i + chunk], model)
        except Exception as e:
            log(f"  ! AI で映像の説明を作れませんでした({e})。その部分はキーワードで代用します")
            out += [[] for _ in scenes[i:i + chunk]]
    return out


def plan_visuals(scenes, s, log=print):
    """sc['visuals']: list of short visual descriptions (script tag > LLM > keywords)."""
    import story
    need = [i for i, sc in enumerate(scenes) if not sc.get("visuals")]
    # cached per paragraph text: the 台本 page preview and the render then agree
    cache_path = INDEX_DIR / "visuals.json"
    cache = mv.read_json(cache_path, {})
    key = lambda sc: hashlib.md5(sc["text"].encode()).hexdigest()[:16]
    for i in need[:]:
        if cache.get(key(scenes[i])):
            scenes[i]["visuals"] = list(cache[key(scenes[i])])
            need.remove(i)
    got = llm_visuals([scenes[i] for i in need], s["llm_model"], log) if need else []
    for j, i in enumerate(need):
        sc = scenes[i]
        base = [sc["query"]] if sc.get("query") else []
        vis = got[j] if j < len(got) else []
        sc["visuals"] = base + (vis or [story.keyword_query(sc["text"], ["man walking alone"])])
        if vis:
            cache[key(sc)] = sc["visuals"]
    if need:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        mv.write_json(cache_path, cache)


# ---------------------------------------------------------------- matching

def match(scenes, index, s, log=print):
    """Pick footage for every scene. Returns {i: [shot, ...]} where shot =
    {"path", "kind", "start", "dur", "score", "thumb"}; shot lengths fill the scene.
    Scenes with a [[media:name]] tag use that file first."""
    import torch
    items = index["items"]
    if not items:
        return {}
    plan_visuals(scenes, s, log)
    frame_vecs, frame_item = [], []
    for k, it in enumerate(items):
        for f in it["frames"]:
            frame_vecs.append(f["vec"]); frame_item.append(k)
    F = torch.tensor(frame_vecs)                                   # frames x 512
    names = [it["name"] or "" for it in items]
    N = embed_text([n or "footage" for n in names])                # items x 512
    has_name = torch.tensor([1.0 if n else 0.0 for n in names])
    fi = torch.tensor(frame_item)

    uses, prev, picks = [0] * len(items), None, {}
    cut = float(s.get("cut", 5.0))
    for i, sc in enumerate(scenes):
        # "a photo of ..." is the phrasing CLIP was trained on (better zero-shot matching)
        q = embed_text([f"a photo of {v}" for v in sc["visuals"]])
        D = q.shape[0]
        sims = F @ q.T                                              # frames x descriptions
        per_item = torch.full((len(items), D), -1.0).scatter_reduce(
            0, fi.unsqueeze(1).expand(-1, D), sims, reduce="amax")  # best frame per description
        # one lucky description must not win alone: blend the best with the average
        # the first description is the most direct one (see _llm_chunk)
        frame_score = 0.5 * per_item[:, 0] + 0.3 * per_item.max(dim=1).values + 0.2 * per_item.mean(dim=1)
        name_score = (N @ q.T).max(dim=1).values * has_name
        score = frame_score + NAME_WEIGHT * name_score
        n_shots = max(1, round(sc["dur"] / cut))
        pinned = [k for k, it in enumerate(items) if sc.get("media") and
                  sc["media"].lower() in Path(it["path"]).name.lower()]
        chosen = []
        for _ in range(n_shots):
            adj = score.clone()
            for k in range(len(items)):
                adj[k] -= REUSE_PENALTY * uses[k] + (REPEAT_PENALTY if k == prev else 0.0)
                if k in chosen:
                    adj[k] -= 1.0
            for k in pinned:
                if k not in chosen:
                    adj[k] += 10.0
            k = int(adj.argmax())
            chosen.append(k); uses[k] += 1; prev = k
        each = sc["dur"] / len(chosen)
        shots = []
        for k in chosen:
            it = items[k]
            best = max(it["frames"], key=lambda f: float(torch.tensor(f["vec"]) @ q[0]))
            start = 0.0
            if it["kind"] == "video":                               # centre the cut on the best frame
                start = min(max(0.0, best["t"] - each / 2), max(0.0, it["dur"] - each - 0.05))
            shots.append({"path": it["path"], "kind": it["kind"], "start": round(start, 2),
                          "dur": round(each, 3), "score": round(float(score[k]), 3),
                          "fit": round(float(frame_score[k]), 3), "thumb": best["thumb"]})
        picks[i] = shots
    return picks
