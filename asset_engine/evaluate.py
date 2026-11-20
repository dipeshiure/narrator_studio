"""Score a preview image against the narration with the local CLIP model. Filenames are not used."""
import hashlib
import json
from pathlib import Path


def _clip_score(text, image_path):
    from PIL import Image
    import media_match
    text_vec = media_match.embed_text([text[:300]])
    image_vec = media_match.embed_images([Image.open(image_path).convert("RGB")])
    return float((text_vec * image_vec).sum())


def score_candidate(text, cand, cache_dir, log=print, score_image=None):
    """Return a similarity score. The same text and preview are not scored twice."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(f"{cand['id']}|{text[:120]}".encode()).hexdigest()[:16]
    saved = cache_dir / f"{key}.json"
    if saved.exists():
        try:
            return float(json.loads(saved.read_text())["score"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if score_image is not None:
        score = float(score_image(text, cand))
    else:
        import urllib.request
        img = cache_dir / f"{cand['id']}.img"
        try:
            if not img.exists():
                req = urllib.request.Request(cand["preview"], headers={"User-Agent": "Mozilla/5.0"})
                img.write_bytes(urllib.request.urlopen(req, timeout=20).read())
            score = _clip_score(text, img)
        except Exception as e:
            log(f"  ! プレビューを採点できませんでした({e})")
            score = 0.0
    saved.write_text(json.dumps({"score": score, "id": cand["id"]}))
    return score


def ranked_above(text, candidates, min_score, cache_dir, log=print, score_image=None):
    """Candidates at or above min_score, highest score first."""
    if score_image is None:
        _prefetch_previews(candidates, cache_dir)
    ranked = [(score_candidate(text, cand, cache_dir, log, score_image), cand) for cand in candidates]
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [(score, cand) for score, cand in ranked if score >= min_score]


def _prefetch_previews(candidates, cache_dir):
    """Download the preview images together, before CLIP starts scoring."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    todo = []
    for cand in candidates:
        img = cache_dir / f"{cand['id']}.img"
        if cand.get("preview") and not img.exists():
            todo.append((img, cand["preview"]))

    def grab(item):
        img, url = item
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            img.write_bytes(urllib.request.urlopen(req, timeout=20).read())
        except Exception:
            pass

    if len(todo) > 1:
        with ThreadPoolExecutor(min(4, len(todo))) as ex:
            list(ex.map(grab, todo))
    elif todo:
        grab(todo[0])


def best_above(text, candidates, min_score, cache_dir, log=print, score_image=None):
    """(score, candidate) for the highest preview at or above min_score, or None."""
    ranked = ranked_above(text, candidates, min_score, cache_dir, log, score_image)
    return ranked[0] if ranked else None
