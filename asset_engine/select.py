"""Search stock first. Ask for an AI picture only when every search stays under the score."""
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "config" / "asset_engine.json"
DEFAULTS = {"min_score": 0.28, "max_candidates": 3, "allow_ai_fallback": True}


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text()))
        except (OSError, ValueError):
            pass
    return cfg


def _shot(path, cand, score, dur):
    kind_word = "Video" if cand["kind"] == "video" else "Photo"
    source = cand.get("source") or "Pexels"
    return {
        "kind": "image" if cand["kind"] == "image" else "video",
        "path": str(path),
        "start": 0.0,
        "dur": dur,
        "score": score,
        "credit": f"{kind_word}: {cand.get('author') or source} / {source}",
        "label": cand.get("query") or cand["id"],
    }


def _ai(brief, dur):
    return {"kind": "ai", "prompt": brief.get("prompt") or brief["queries"][0], "dur": dur, "label": "AI"}


def _try_query(query, text, key, cfg, cache, seen, log, search_videos, search_photos, score_best, save,
               rewrite=None, search_mixkit=None):
    """T5 rewrites the phrase. CLIP scores the first results. The highest of the top 3 is kept."""
    if not query and not text:
        return None
    if rewrite:
        query = rewrite(query, text) or query
        log(f"  T5 の検索語: {query}")
    if not query:
        return None
    limit = int(cfg["max_candidates"])
    pool = []
    if key:
        try:
            pool += [c for c in search_videos(query, key, limit, log) if c["id"] not in seen]
        except Exception as e:
            log(f"  ! Pexels の検索に失敗しました({e})")
        try:
            pool += [c for c in search_photos(query, key, limit, log) if c["id"] not in seen]
        except Exception as e:
            log(f"  ! Pexels の検索に失敗しました({e})")
    if search_mixkit:
        try:
            pool += [c for c in search_mixkit(query, key, limit, log) if c["id"] not in seen]
        except Exception as e:
            log(f"  ! Mixkit の検索に失敗しました({e})")
    ranked = list(score_best(text or query, pool, 0, cache, log) or [])
    top = ranked[:3]
    if top:
        shown = ", ".join(f"{cand['id']} {score:.2f}" for score, cand in top)
        log(f"  上位3件: {shown}")
    floor = float(cfg["min_score"])
    for score, cand in top:
        if score < floor:
            continue
        path = save(cand, score, log)
        if path is None:
            continue
        seen.add(cand["id"])
        return score, cand, path
    return None


def choose_all(scenes, s, work, log=print, search_videos=None, search_photos=None,
               score_best=None, save=None, analyze=None, rewrite=None, search_mixkit=None):
    """{scene index: shot}. Shot kind is video, image, or ai (no file yet)."""
    import make_video as mv
    from . import analyze as analyze_mod
    from . import evaluate, search, store
    cfg = load_config()
    search_videos = search_videos or search.search_videos
    search_photos = search_photos or search.search_photos
    score_best = score_best or evaluate.ranked_above
    save = save or store.download
    analyze = analyze or analyze_mod.briefs
    targets = [i for i, sc in enumerate(scenes) if not sc.get("chart") and not sc.get("photo")]
    picks, seen = {}, set()
    if not targets:
        return picks
    log(f"  ストック素材を探しています: {len(targets)} 場面")
    has_key = (not s.get("offline")) and mv.has_pexels_key()
    if s.get("offline") or (not has_key and search_mixkit is None):
        log("  Pexels の API キーがないため、AI の絵にします")
        for i in targets:
            picks[i] = _ai({"prompt": (scenes[i].get("text") or "")[:180], "queries": [""]}, scenes[i].get("dur"))
        return picks
    rows = analyze([scenes[i] for i in targets], s.get("llm_model"), log)
    cache = Path(work) / "asset_cache"
    key = mv.pexels_key() if has_key else ""
    for local, i in enumerate(targets):
        brief = rows[local] if local < len(rows) else {"queries": [""], "prompt": ""}
        queries = [q for q in brief.get("queries") or [] if q][:2]
        if not queries:
            queries = [brief.get("prompt") or "person walking"]
        scenes[i]["query"] = queries[0]
        chosen = None
        for n, query in enumerate(queries):
            if n == 1:
                log(f"  場面 {i + 1}: 別の検索語でもう一度探します")
            chosen = _try_query(query, scenes[i].get("text") or "", key, cfg, cache, seen, log,
                                search_videos, search_photos, score_best, save, rewrite, search_mixkit)
            if chosen:
                break
        if chosen:
            score, cand, path = chosen
            picks[i] = _shot(path, cand, score, scenes[i].get("dur"))
            log(f"  場面 {i + 1}: 「{cand['query']}」 点数 {score:.2f}")
        elif cfg.get("allow_ai_fallback", True):
            picks[i] = _ai(brief, scenes[i].get("dur"))
            log(f"  場面 {i + 1}: ストックが足りないので AI の絵にします")
        else:
            log(f"  場面 {i + 1}: 合う素材がなく、AI の絵も作りません")
    stock_n = sum(1 for p in picks.values() if p["kind"] != "ai")
    ai_n = sum(1 for p in picks.values() if p["kind"] == "ai")
    log(f"  素材の採点が終わりました: ストック {stock_n}、AI の絵 {ai_n}")
    try:
        import media_match
        media_match.release()
    except Exception:
        pass
    try:
        mv.unload_llm()
    except Exception:
        pass
    return picks
