"""thumbs.py: text fitting, layout and the full pipeline with Pexels mocked (no network)."""
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

import thumbs


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the photo cache at tmp_path and fake Pexels search + download."""
    monkeypatch.setattr(thumbs, "PHOTOS", tmp_path / "photos")
    monkeypatch.setattr(thumbs, "USED", tmp_path / "photos" / "used.json")
    monkeypatch.setattr(thumbs, "SEARCH_CACHE", tmp_path / "photos" / "search.json")
    monkeypatch.setattr(thumbs, "QUALITY", tmp_path / "photos" / "quality.json")
    (tmp_path / "photos").mkdir()
    calls = {"search": 0}

    def fake_search(query, orientation, page=1):
        calls["search"] += 1
        base = (abs(hash(query)) % 1000) * 100 + page * 10
        w, h = (1800, 1200) if orientation == "landscape" else (1000, 1500)
        return [{"id": base + i, "alt": "Man boxer portrait", "photographer": "P", "url": "u",
                 "avg_color": "#202020", "src": "x", "w": w, "h": h} for i in range(6)]

    def fake_download(photo):
        path = tmp_path / "photos" / f"{photo['id']}.jpg"
        if not path.exists():
            rng = np.random.default_rng(photo["id"])
            a = (rng.random((photo["h"] // 4, photo["w"] // 4, 3)) * 120).astype(np.uint8)
            img = Image.fromarray(a).resize((photo["w"], photo["h"]))
            ImageDraw.Draw(img).ellipse((photo["w"] * .3, photo["h"] * .2, photo["w"] * .7, photo["h"] * .6),
                                        fill=(200, 180, 160))
            img.save(path)
        return path

    def no_network(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(thumbs, "search", fake_search)
    monkeypatch.setattr(thumbs, "download", fake_download)
    monkeypatch.setattr(thumbs, "_key", no_network)
    return tmp_path, calls


def meta(n, text="STAY DISCIPLINED", title="Discipline: The Key to Success"):
    return {"video": f"{n:02d}_Test.mp4", "title_en": title, "thumbnail_text": text,
            "thumbnail": f"{n:02d}_Test.thumb.jpg", "speech_source": ""}


@pytest.mark.parametrize("words", [["GO"], ["STAY", "DISCIPLINED"], ["STAY", "TRUE", "TO", "YOURSELF"],
                                   ["BELIEVE", "IN", "YOUR", "VISION"]])
@pytest.mark.parametrize("mode,spec", [("caps", thumbs.CAPS_FONTS[0]), ("caps", thumbs.CAPS_FONTS[1]),
                                       ("serif", thumbs.SERIF_FONTS[0]), ("serif", thumbs.SERIF_FONTS[2])])
def test_layout_text_fits_box(words, mode, spec):
    lines = thumbs.split_lines(words)
    placed, bw, bh = thumbs.layout_text(lines, spec, 600, 300, mode)
    assert bw <= 600 and bh <= 300
    assert min(s for _, s in placed) >= 60          # still big enough for mobile


def test_split_lines_balanced():
    assert thumbs.split_lines(["STAY", "TRUE", "TO", "YOURSELF"]) == ["STAY TRUE", "TO YOURSELF"]
    assert thumbs.split_lines(["GO", "FURTHER"]) == ["GO", "FURTHER"]
    assert thumbs.split_lines(["ONE"]) == ["ONE"]


def test_title_case_and_phrase():
    assert thumbs.title_case(["BE", "A", "LION"]) == ["Be", "a", "Lion"]
    assert thumbs.text_lines(["BE", "A", "LION"], "serif")[0] == ["Be a Lion."]
    assert thumbs.phrase({"thumbnail_text": "", "title_en": "Never Quit: Fight for Your Dreams"}) == "Never Quit"
    assert thumbs.phrase({"thumbnail_text": "KEEP GOING!!"}) == "KEEP GOING"


@pytest.mark.parametrize("layout", thumbs.LAYOUTS)
@pytest.mark.parametrize("style", ["caps", "serif"])
def test_plan_text_inside_frame_and_off_faces(layout, style):
    mode = style
    spec = (thumbs.CAPS_FONTS if mode == "caps" else thumbs.SERIF_FONTS)[0]
    face = [(500, 330, 200, 220)] if layout != "hero" else [(140, 120, 260, 300)]
    for words in (["GO"], ["FIGHT", "FOR", "YOUR", "DREAMS"]):
        plan = thumbs.plan_text(thumbs.text_lines(words, style), spec, mode, layout, "right", face,
                                "MOTIVATIONAL SPEECH")
        x0, y0, x1, y1 = plan["box"]
        assert 0 <= x0 and x1 <= thumbs.W and 0 <= y0 and y1 <= thumbs.H
        if layout == "hero":
            assert thumbs._overlap(plan["box"], face) < 0.2


def test_variant_cycles_all_combos():
    combos = {thumbs.variant(meta(n))[:2] for n in range(1, 7)}
    assert len(combos) == 6


def test_make_thumbnail_offline(sandbox):
    tmp, calls = sandbox
    m1, m2 = meta(1), meta(2, "GO FURTHER", "Keep Moving Forward")
    d1, d2 = tmp / "a.thumb.jpg", tmp / "b.thumb.jpg"
    thumbs.make_thumbnail(m1, d1)
    thumbs.make_thumbnail(m2, d2)
    for d in (d1, d2):
        img = Image.open(d)
        assert img.size == (1280, 720) and img.format == "JPEG"
        assert np.asarray(img.convert("L")).mean() < 140      # dark look
    ids1 = {p["id"] for p in m1["thumbnail_photos"]}
    ids2 = {p["id"] for p in m2["thumbnail_photos"]}
    assert ids1 and ids2 and not ids1 & ids2                 # no photo used twice
    used = json.loads((tmp / "photos" / "used.json").read_text())
    assert {int(k) for k in used} == ids1 | ids2
    # regenerating the same video may reuse its own photos and keeps the used list tidy
    thumbs.make_thumbnail(m1, d1)
    used = json.loads((tmp / "photos" / "used.json").read_text())
    assert sum(1 for v in used.values() if v == m1["video"]) == len(m1["thumbnail_photos"])
    assert not list(tmp.glob("*.tmp.jpg"))


def test_make_thumbnail_raises_without_photos(sandbox, monkeypatch):
    monkeypatch.setattr(thumbs, "search", lambda *a, **k: [])
    with pytest.raises(RuntimeError):
        thumbs.make_thumbnail(meta(3), sandbox[0] / "c.jpg")


def test_phrase_drops_dangling_small_words():
    assert thumbs.phrase({"thumbnail_text": "HEAD STRAIGHT TO THE"}) == "HEAD STRAIGHT"
    assert thumbs.phrase({"thumbnail_text": "GO ALL IN"}) == "GO ALL IN"


def test_pick_photos_skips_off_theme_and_same_shoot(sandbox, monkeypatch):
    import random
    tmp, _ = sandbox
    res = [
        {"id": 1, "alt": "Smiling female volleyball player portrait", "photographer": "A"},
        {"id": 2, "alt": "Boxer in dark gym portrait", "photographer": "B"},
        {"id": 3, "alt": "Boxer training in dark gym", "photographer": "B"},       # same shoot as 2
        {"id": 4, "alt": "Runner at night", "photographer": "C"},
        {"id": 5, "alt": "Boxer gym dark close portrait", "photographer": "D"},
    ]
    res = [dict(p, url="u", avg_color="#202020", src="x", w=1000, h=1500) for p in res]
    monkeypatch.setattr(thumbs, "search", lambda q, o, page=1: res if page == 1 else [])
    picked = thumbs.pick_photos(["q1", "q2", "q3", "q4"], 3, "portrait", "own", random.Random(1), check=5)
    ids = [p["id"] for p in picked]
    assert 1 not in ids                                  # smiling: off-theme
    assert len({p["photographer"] for p in picked}) == len(picked)   # one photo per photographer
    # photo 2 used by another video -> its photographer's similar shot 3 is skipped too
    thumbs._write(thumbs.USED, {"2": "other"})
    thumbs._write(thumbs.SEARCH_CACHE, {"k": res})
    picked = thumbs.pick_photos(["q1", "q2", "q3"], 3, "portrait", "own", random.Random(1), check=5)
    assert not {2, 3} & {p["id"] for p in picked}


def test_make_thumbnail_preview_does_not_claim(sandbox):
    tmp, _ = sandbox
    m = meta(5, "BE MENTALLY TOUGH")
    m["speech_source"] = "要確認"
    thumbs.make_thumbnail(m, tmp / "p.jpg", claim_photos=False)
    assert (tmp / "p.jpg").exists() and not (tmp / "photos" / "used.json").exists()
