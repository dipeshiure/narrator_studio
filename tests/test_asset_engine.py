"""Asset selection without the network, CLIP, or FLUX."""
import json
from pathlib import Path

import pytest

import make_video as mv
from asset_engine import analyze, evaluate, search, select, store


def _cand(cid="v9", kind="video", query="lighthouse at night"):
    return {"id": cid, "pexels_id": "9", "kind": kind, "preview": "http://preview",
            "download": "http://file", "author": "Ada", "page": "https://www.pexels.com/video/9",
            "query": query, "license": "Pexels License", "width": 1280}


def test_missing_key_asks_for_an_ai_picture(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "has_pexels_key", lambda: False)
    scenes = [{"text": "A bell on the ship.", "dur": 4.0}]
    picks = select.choose_all(scenes, {"offline": False}, tmp_path, log=lambda *a: None)
    assert picks[0]["kind"] == "ai"


def test_search_failure_and_empty_results_fall_through(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "has_pexels_key", lambda: True)
    monkeypatch.setattr(mv, "pexels_key", lambda: "k")
    monkeypatch.setattr(mv, "unload_llm", lambda: None)
    calls = []

    def videos(query, key, limit, log=None):
        calls.append(query)
        if query == "first":
            raise RuntimeError("500")
        return []

    def photos(query, key, limit, log=None):
        return []

    scenes = [{"text": "Ice ahead of the ship.", "dur": 5.0}]
    picks = select.choose_all(
        scenes, {"llm_model": "m"}, tmp_path, log=lambda *a: None,
        search_videos=videos, search_photos=photos,
        analyze=lambda scenes, model, log=None: [{"queries": ["first", "second"], "prompt": "an iceberg"}])
    assert calls == ["first", "second"]
    assert picks[0]["kind"] == "ai" and picks[0]["prompt"] == "an iceberg"


def test_low_score_retries_once_then_uses_ai(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "has_pexels_key", lambda: True)
    monkeypatch.setattr(mv, "pexels_key", lambda: "k")
    monkeypatch.setattr(mv, "unload_llm", lambda: None)
    seen = []

    def videos(query, key, limit, log=None):
        seen.append(query)
        return [_cand(query=query)]

    def score(text, cands, min_score, cache, log=None):
        return [(0.1, cands[0])] if cands and cands[0]["query"] == "second" else []

    scenes = [{"text": "The bell rang.", "dur": 3.0}]
    picks = select.choose_all(
        scenes, {}, tmp_path, log=lambda *a: None,
        search_videos=videos, search_photos=lambda *a, **k: [],
        score_best=score, save=lambda *a, **k: None,
        analyze=lambda scenes, model, log=None: [{"queries": ["first", "second"], "prompt": "a ship's bell"}])
    assert seen == ["first", "second"]
    assert picks[0]["kind"] == "ai"


def test_high_score_downloads_once_and_records_the_licence(sandbox, monkeypatch):
    monkeypatch.setattr(mv, "has_pexels_key", lambda: True)
    monkeypatch.setattr(mv, "pexels_key", lambda: "k")
    monkeypatch.setattr(mv, "unload_llm", lambda: None)
    body = b"x" * (store.MIN_IMAGE + 50)
    downloads = []

    def save(cand, score, log=None):
        downloads.append(cand["id"])
        return store.download(cand, score, log or (lambda *a: None), fetch_bytes=lambda c: body)

    cand = _cand(kind="image", query="ship bell")
    cand["id"] = "p9"
    scenes = [{"text": "The bell rang.", "dur": 3.0}, {"text": "The bell rang again.", "dur": 3.0}]

    def videos(query, key, limit, log=None):
        return []

    def photos(query, key, limit, log=None):
        return [cand]

    def score(text, cands, min_score, cache, log=None):
        return [(0.9, cands[0])] if cands else []

    picks = select.choose_all(
        scenes, {}, sandbox["TMP"], log=lambda *a: None,
        search_videos=videos, search_photos=photos, score_best=score, save=save,
        analyze=lambda scenes, model, log=None: [
            {"queries": ["ship bell"], "prompt": "a bell"},
            {"queries": ["ship bell"], "prompt": "a bell"},
        ])
    assert downloads == ["p9"]
    assert picks[0]["kind"] == "image" and picks[1]["kind"] == "ai"
    meta = json.loads(Path(picks[0]["path"] + ".json").read_text())
    assert meta["source"] == "Pexels" and meta["license"] == "Pexels License" and meta["author"] == "Ada"
    assert meta["page"].startswith("https://") and "page" not in picks[0]["credit"]
    again = store.download(cand, 0.9, lambda *a: None, fetch_bytes=lambda c: (_ for _ in ()).throw(AssertionError("again")))
    assert again == Path(picks[0]["path"])


def test_broken_download_is_rejected(sandbox):
    cand = _cand()
    path = store.download(cand, 0.5, lambda *a: None, fetch_bytes=lambda c: b"tiny")
    assert path is None
    assert list(mv.LIB.rglob("*.mp4")) == []


def test_rate_limit_and_server_error_do_not_raise(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def raise_code(code):
        def boom(req, timeout=0):
            raise urllib.error.HTTPError("http://x", code, "no", {}, io.BytesIO(b""))
        return boom

    logs = []
    monkeypatch.setattr(urllib.request, "urlopen", raise_code(429))
    assert search.fetch_json("http://x", "k", logs.append) is None
    assert any("回数制限" in line for line in logs)
    logs.clear()
    monkeypatch.setattr(urllib.request, "urlopen", raise_code(500))
    assert search.fetch_json("http://x", "k", logs.append) is None
    assert any("失敗" in line for line in logs)
    assert search.search_videos("bell", "k", 4, log=lambda *a: None,
                                 fetch=lambda *a, **k: {"videos": []}) == []


def test_score_cache_skips_a_second_clip_call(tmp_path):
    calls = []

    def fake(text, cand):
        calls.append(cand["id"])
        return 0.5

    cand = _cand()
    assert evaluate.score_candidate("a ship", cand, tmp_path, score_image=fake) == 0.5
    assert evaluate.score_candidate("a ship", cand, tmp_path, score_image=fake) == 0.5
    assert calls == ["v9"]


def test_analyze_keeps_two_queries():
    def ask(prompt, schema):
        assert schema["required"] == ["scenes"]
        return json.dumps({"scenes": [{"n": 1, "subject": "bell", "environment": "ship",
                                       "actions": "ringing", "mood": "cold", "style": "ink",
                                       "queries": ["ship bell", "iceberg bow"]}]})

    rows = analyze.briefs([{"text": "The bell rang."}], "m", ask=ask)
    assert rows[0]["queries"] == ["ship bell", "iceberg bow"]
    assert "bell" in rows[0]["prompt"] and "ship" in rows[0]["prompt"]


def test_chart_and_photo_scenes_are_not_searched(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "has_pexels_key", lambda: True)
    monkeypatch.setattr(mv, "pexels_key", lambda: "k")
    called = []
    scenes = [{"text": "A chart.", "chart": {"type": "bar"}}, {"text": "A photo.", "photo": "Osaka"}]
    picks = select.choose_all(scenes, {}, tmp_path, log=lambda *a: None,
                              search_videos=lambda *a, **k: called.append(1) or [],
                              search_photos=lambda *a, **k: [])
    assert picks == {} and called == []


def test_t5_query_and_the_highest_of_the_top_three(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "has_pexels_key", lambda: True)
    monkeypatch.setattr(mv, "pexels_key", lambda: "k")
    monkeypatch.setattr(mv, "unload_llm", lambda: None)
    searched = []

    def videos(query, key, limit, log=None):
        searched.append(query)
        return [_cand("v1", query=query), _cand("v2", query=query), _cand("v3", query=query), _cand("v4", query=query)]

    def score(text, cands, min_score, cache, log=None):
        weight = {"v1": 0.40, "v2": 0.55, "v3": 0.91, "p9": 0.80}
        return sorted(((weight[c["id"]], c) for c in cands if c["id"] in weight), key=lambda row: -row[0])

    saved = []

    def save(cand, score, log=None):
        saved.append((cand["id"], cand["kind"], score))
        return tmp_path / ("win.jpg" if cand["kind"] == "image" else "win.mp4")

    photo = _cand("p9", kind="image", query="cliff waves")
    scenes = [{"text": "A lighthouse stands above a rough sea.", "dur": 4.0}]
    picks = select.choose_all(
        scenes, {}, tmp_path, log=lambda *a: None,
        search_videos=videos,
        search_photos=lambda *a, **k: [photo],
        score_best=score, save=save,
        rewrite=lambda query, text: "cliff waves",
        analyze=lambda scenes, model, log=None: [{"queries": ["lighthouse sunset"], "prompt": "a lighthouse"}])
    assert searched == ["cliff waves"]
    assert saved == [("v3", "video", 0.91)]
    assert picks[0]["kind"] == "video" and picks[0]["score"] == 0.91


def test_an_image_winner_stays_an_image(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "has_pexels_key", lambda: True)
    monkeypatch.setattr(mv, "pexels_key", lambda: "k")
    monkeypatch.setattr(mv, "unload_llm", lambda: None)
    photo = _cand("p9", kind="image")

    def score(text, cands, min_score, cache, log=None):
        return [(0.2, cands[0]), (0.7, cands[1])]

    picks = select.choose_all(
        scenes=[{"text": "A bell.", "dur": 3.0}], s={}, work=tmp_path, log=lambda *a: None,
        search_videos=lambda *a, **k: [_cand("v1")],
        search_photos=lambda *a, **k: [photo],
        score_best=score, save=lambda cand, score, log=None: tmp_path / "pic.jpg",
        analyze=lambda scenes, model, log=None: [{"queries": ["bell"], "prompt": "a bell"}])
    assert picks[0]["kind"] == "image"


def test_the_script_line_becomes_the_search_without_ollama(monkeypatch):
    import asset_engine.query_t5 as t5
    monkeypatch.setattr(t5, "rewrite", lambda query, text: "lonely lighthouse dusk")
    rows = t5.script_queries([{"text": "A lighthouse stands alone at dusk."}], "m", log=lambda *a: None)
    assert rows[0]["queries"] == ["lonely lighthouse dusk"]
    assert "mood" not in rows[0]


def test_scenes_follow_spoken_sentences():
    words = [
        {"w": "Hello", "s": 0.0, "e": 0.4},
        {"w": "there.", "s": 0.5, "e": 1.0},
        {"w": "Next", "s": 1.2, "e": 1.6},
        {"w": "line.", "s": 1.7, "e": 2.2},
    ]
    scenes = mv.scenes_from_words(words, 2.2)
    assert [s["text"] for s in scenes] == ["Hello there.", "Next line."]
    assert [s["dur"] for s in scenes] == [1.2, 1.0]      # 0 -> next start -> end: adds up to the audio
    assert sum(s["dur"] for s in scenes) == 2.2


def test_render_asset_shot_uses_the_existing_encoders(monkeypatch, tmp_path):
    import story
    seen = []
    monkeypatch.setattr(story, "render_photo", lambda *a, **k: seen.append(("photo", a[0])))
    monkeypatch.setattr(story.mv, "run", lambda cmd: seen.append(("ffmpeg", cmd)))
    sc = {"dur": 4.5, "start": 1.0}
    seg = story.render_asset_shot(0, sc, {"kind": "image", "path": "/tmp/a.jpg"}, tmp_path, {"bitrate": "1"}, "eq")
    assert seg.name == "s000_asset.mp4" and seen == [("photo", "/tmp/a.jpg")]
    story.render_asset_shot(1, sc, {"kind": "video", "path": "/tmp/a.mp4", "start": 0.5}, tmp_path, {"bitrate": "1"}, "eq")
    cmd = seen[-1][1]
    assert cmd[0] == "ffmpeg" and "/tmp/a.mp4" in cmd and "4.500" in cmd
