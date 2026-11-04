"""Fast tests: settings persistence, clip bookkeeping, Pexels fetching, subtitles."""
import json, multiprocessing, os, re
from pathlib import Path

import pytest

import make_video as mv
from conftest import add_clips


# ---------------------------------------------------------------- settings / json
def test_first_run_uses_defaults(sandbox):
    assert not mv.SETTINGS.exists()
    assert mv.load_settings() == mv.DEFAULTS


def test_settings_roundtrip_and_unknown_keys(sandbox):
    s = mv.load_settings()
    s.update(cut=5.0, font="Futura", bogus="x")
    mv.save_settings(s)
    saved = json.loads(mv.SETTINGS.read_text())
    assert "bogus" not in saved
    loaded = mv.load_settings()
    assert loaded["cut"] == 5.0 and loaded["font"] == "Futura"


def test_new_default_key_appears_for_old_settings_file(sandbox):
    mv.SETTINGS.write_text(json.dumps({"cut": 2.0}))        # file from an older version
    s = mv.load_settings()
    assert s["cut"] == 2.0 and s["remove_music"] == mv.DEFAULTS["remove_music"]


def test_corrupt_settings_fall_back_to_defaults(sandbox):
    mv.SETTINGS.write_text("{broken json")
    assert mv.load_settings() == mv.DEFAULTS


def test_write_json_is_atomic(sandbox, tmp_path):
    p = tmp_path / "x.json"
    mv.write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert not list(tmp_path.glob("x.json.*.tmp"))          # no temp file left behind


def test_frame_size_and_grade(sandbox):
    s = mv.load_settings()
    assert mv.frame_size({**s, "aspect": "16:9"}) == (1920, 1080)
    assert mv.frame_size({**s, "aspect": "9:16"}) == (1080, 1920)
    assert "vignette" in mv.grade_filter({**s, "vignette": True})
    assert "vignette" not in mv.grade_filter({**s, "vignette": False})


# ---------------------------------------------------------------- clips
def test_take_clips_moves_to_used_and_never_repeats(sandbox, assets):
    add_clips(mv.LIB, assets["clip"], 10)
    s = {**mv.load_settings(), "offline": True}
    a = mv.take_clips(4, "x", s, log=lambda m: None)
    b = mv.take_clips(4, "x", s, log=lambda m: None)
    assert len(a) == len(set(a)) == 4 and len(b) == 4
    assert not set(a) & set(b)                              # next video gets other clips
    assert all(p.parent.parent == mv.USED and p.exists() for p in a + b)
    assert len(mv.library_pool()) == 2 and len(mv.used_pool()) == 8


def test_offline_falls_back_to_used_clips(sandbox, assets):
    add_clips(mv.LIB, assets["clip"], 2)
    add_clips(mv.USED, assets["clip"], 5, prefix="2000")
    s = {**mv.load_settings(), "offline": True}
    picks = mv.take_clips(6, "x", s, log=lambda m: None)
    assert len(picks) == len(set(picks)) == 6                # still distinct inside one video
    assert mv.library_pool() == []


def test_online_tops_up_before_taking(sandbox, assets, monkeypatch):
    calls = []

    def fake_top_up(n, title, queries, log=print):
        calls.append(n)
        add_clips(mv.LIB, assets["clip"], n - len(mv.library_pool()), prefix="3000")
    monkeypatch.setattr(mv, "top_up", fake_top_up)
    monkeypatch.setattr(mv, "has_pexels_key", lambda: True)
    s = {**mv.load_settings(), "offline": False}
    picks = mv.take_clips(5, "x", s, log=lambda m: None)
    assert calls == [5] and len(picks) == 5


def test_no_api_key_falls_back_to_library_least_reused_first(sandbox, assets, monkeypatch):
    monkeypatch.setattr(mv, "has_pexels_key", lambda: False)
    add_clips(mv.USED, assets["clip"], 6, prefix="5000")
    s = {**mv.load_settings(), "offline": False}
    logs = []
    first = mv.take_clips(3, "x", s, log=logs.append)
    second = mv.take_clips(3, "x", s, log=logs.append)
    assert len(first) == len(second) == 3
    assert not {p.name for p in first} & {p.name for p in second}     # least re-used go first
    assert any("API キーがない" in l for l in logs)


def _worker(args):
    lib, used, tmp, n = args
    mv.LIB, mv.USED, mv.TMP = Path(lib), Path(used), Path(tmp)
    s = {**mv.DEFAULTS, "offline": True}
    return [str(p) for p in mv.take_clips(n, "x", s, log=lambda m: None)]


def test_parallel_workers_never_share_a_clip(sandbox, assets):
    add_clips(mv.LIB, assets["clip"], 40)
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(4) as pool:
        results = pool.map(_worker, [(mv.LIB, mv.USED, mv.TMP, 10)] * 4)
    flat = [p for r in results for p in r]
    assert len(flat) == 40 and len(set(flat)) == 40


def test_fetch_new_skips_known_ids_and_advances_page(sandbox, assets, monkeypatch):
    add_clips(mv.USED, assets["clip"], 1, prefix="111")            # id 111000 already used
    pages = []

    def fake_search(query, key, page, per_page=15):
        pages.append(page)
        vid = lambda i: {"id": i, "duration": 10, "video_files": [
            {"width": 1920, "file_type": "video/mp4", "link": f"http://x/{i}-1920"},
            {"width": 1280, "file_type": "video/mp4", "link": f"http://x/{i}-1280"},
            {"width": 640, "file_type": "video/mp4", "link": f"http://x/{i}-640"}]}
        return {"videos": [vid(111000), vid(222), {"id": 333, "duration": 2, "video_files": []}, vid(444)]}

    fetched = []

    class Resp:
        def __init__(self, url): self.url = url
        def read(self):
            fetched.append(self.url)
            return assets["clip"].read_bytes()

    monkeypatch.setattr(mv, "pexels_search", fake_search)
    monkeypatch.setattr(mv.urllib.request, "urlopen", lambda req, timeout=0: Resp(req.full_url))
    got = mv.fetch_new("test query", "KEY", 2, log=lambda m: None)
    assert got == 2
    ids = sorted(p.stem for p in mv.library_pool())
    assert ids == ["222", "444"]                                    # 111000 skipped, 333 too short
    assert all(u.endswith("-1280") for u in fetched)                # smallest HD file
    assert json.loads(mv.PAGES.read_text())["test query"] == 1      # wanted count met on page 1
    assert not list(mv.LIB.glob("*/*.part"))


# ---------------------------------------------------------------- subtitles
WORDS = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35}
         for i, w in enumerate("never give up on yourself. keep going {braces} now".split())]


@pytest.mark.parametrize("aspect,res", [("16:9", "1920\nPlayResY: 1080"), ("9:16", "1080\nPlayResY: 1920")])
def test_simple_ass(sandbox, tmp_path, aspect, res):
    s = {**mv.load_settings(), "aspect": aspect, "uppercase": True, "alignment": 8}
    p = tmp_path / "s.ass"
    mv.write_simple_ass(WORDS, p, s)
    t = p.read_text()
    assert f"PlayResX: {res}" in t
    dialogues = [l for l in t.splitlines() if l.startswith("Dialogue")]
    assert dialogues and "NEVER GIVE UP ON YOURSELF." in dialogues[0]
    assert "{" not in "".join(d.split(",,", 1)[1] for d in dialogues)   # braces stripped
    assert ",8,120,120," in t                                           # alignment applied


@pytest.mark.parametrize("sub_type", ["one_word", "joined", "sentence"])
@pytest.mark.parametrize("highlight", [True, False])
def test_subtitlefx_ass(sandbox, tmp_path, sub_type, highlight):
    s = {**mv.load_settings(), "sub_type": sub_type, "highlight": highlight, "margin_v": 222}
    p = tmp_path / "fx.ass"
    mv.write_subtitlefx_ass(WORDS, p, s, total=10.0)
    t = p.read_text()
    dialogues = [l for l in t.splitlines() if l.startswith("Dialogue")]
    assert dialogues
    assert ("Highlight" in t) == highlight
    assert all(l.split(",")[-2] == "222" for l in t.splitlines() if l.startswith("Style:"))
    visible = [re.sub(r"\{[^}]*\}", "", d.split(",,", 1)[1]) for d in dialogues]
    assert all(v.strip() for v in visible)                  # no caption swallowed by markup
    assert any("BRACES" in v for v in visible)              # '{braces}' word kept as text
    if sub_type == "one_word":
        assert all(len(v.split()) == 1 for v in visible)


def test_subtitlefx_handles_empty_transcript(sandbox, tmp_path):
    p = tmp_path / "empty.ass"
    mv.write_subtitlefx_ass([], p, mv.load_settings(), total=5.0)
    assert p.exists()


# ---------------------------------------------------------------- posting folders
def kit(vdir, stem, title):
    (vdir / f"{stem}.mp4").write_bytes(b"video " + stem.encode())
    (vdir / f"{stem}.thumb.jpg").write_bytes(b"jpg")
    mv.write_json(vdir / f"{stem}.publish.json", {
        "video": f"{stem}.mp4", "title_en": title, "title_ja": "タ", "description_en_full": "d",
        "description_ja_full": "説", "tags": ["a"], "hashtags": ["#b"], "thumbnail": f"{stem}.thumb.jpg"})


def test_posting_folders_incremental_and_unnumbered(sandbox, tmp_path):
    import make_posting
    vdir, dest = tmp_path / "v", tmp_path / "post"; vdir.mkdir()
    kit(vdir, "01_A", "Title A"); kit(vdir, "plain", "Plain One"); kit(vdir, "other", "Other One")
    (vdir / "nokit.mp4").write_bytes(b"x")
    msgs = []
    # a video without its kit yet gets a folder too (named after the file)
    assert make_posting.main(vdir, dest, log=msgs.append) == 4
    assert sorted(p.name for p in dest.iterdir() if p.is_dir()) == ["01_Title A", "Other One", "Plain One", "nokit"]
    assert os.path.samefile(dest / "01_Title A" / "video.mp4", vdir / "01_A.mp4")   # hard link
    assert "(4 updated)" in msgs[-1]
    make_posting.main(vdir, dest, log=msgs.append)
    assert "(0 updated)" in msgs[-1]                                              # nothing rewritten
    # title changes: old folder removed, user files elsewhere untouched
    (dest / "keep.txt").write_text("mine")
    kit(vdir, "plain", "Plain Two")
    make_posting.main(vdir, dest, log=msgs.append)
    assert not (dest / "Plain One").exists() and (dest / "Plain Two" / "post.txt").exists()
    assert (dest / "keep.txt").read_text() == "mine"


# ---------------------------------------------------------------- after render
def test_after_render_order_and_warnings(sandbox, tmp_path, monkeypatch):
    import qa
    video = tmp_path / "x.mp4"; video.write_bytes(b"v")
    order = []
    monkeypatch.setattr(mv, "run_publish", lambda v, a, log=print, force=False: order.append("publish") or True)
    monkeypatch.setattr(qa, "check_video", lambda v, a, w, log=print, s=None:
                        order.append("qa") or {"status": "warn", "reasons": ["字幕: 0 行"]})
    monkeypatch.setattr(mv, "refresh_posting", lambda d, log=print: order.append("posting"))
    stages = []
    r = mv.after_render(video, tmp_path / "x.mp3", mv.load_settings(), log=lambda m: None,
                        step=lambda st, msg: stages.append(st))
    assert order == ["publish", "qa", "posting"] and stages == ["publishing", "checking", "posting"]
    assert r == {"publish": "ok", "qa": "warn", "warnings": ["品質チェック: 字幕: 0 行"]}

    def boom(*a, **k):
        raise RuntimeError("no llm")
    monkeypatch.setattr(mv, "run_publish", boom)
    monkeypatch.setattr(mv, "refresh_posting", boom)
    r = mv.after_render(video, tmp_path / "x.mp3", mv.load_settings(), log=lambda m: None)
    assert r["publish"] == "error" and len(r["warnings"]) == 3 and video.exists()


def test_run_publish_prefers_publish_kit_and_falls_back(sandbox, tmp_path, monkeypatch):
    import publish_meta
    video, audio = tmp_path / "v.mp4", tmp_path / "a" / "v.mp3"
    calls = []
    monkeypatch.setattr(publish_meta, "publish_kit",
                        lambda v, a, log=print, force=False: calls.append(("kit", force)) or {"ok": 1}, raising=False)
    assert mv.run_publish(video, audio, force=True) is True and calls == [("kit", True)]
    monkeypatch.delattr(publish_meta, "publish_kit")
    monkeypatch.setattr(publish_meta, "process", lambda v, adir, model, log: calls.append(("process", adir)) or True)
    assert mv.run_publish(video, audio) is True and calls[-1] == ("process", audio.parent)
