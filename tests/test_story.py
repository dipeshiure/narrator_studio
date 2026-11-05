import json

import story


def test_script_words_keeps_script_spelling():
    bounds = [("Fees", 0.0, 0.3), ("hit", 0.3, 0.5), ("4.8", 0.5, 0.9), ("billion", 0.9, 1.2)]
    words = story.script_words("Fees hit $4.8 billion.", bounds)
    assert [w["w"] for w in words] == ["Fees", "hit", "$4.8", "billion."]
    assert words[2]["s"] == 0.5


def test_script_words_multiword_boundary():
    bounds = [("In 2004", 0.0, 1.0), ("Blockbuster", 1.2, 1.8), ("was", 1.8, 2.0)]
    words = story.script_words("In 2004, Blockbuster was", bounds)
    assert [w["w"] for w in words] == ["In 2004,", "Blockbuster", "was"]


def test_load_text_script(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("My Title\n\n## Part One\nFirst line\ncontinues.\n\nSecond scene.\n")
    data = story.load_script(p)
    assert data["title"] == "My Title"
    assert data["scenes"] == [{"text": "First line continues.", "heading": "Part One"},
                              {"text": "Second scene."}]


def test_load_json_script_drops_empty_scenes(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"title": "T", "scenes": [{"text": "a"}, {"text": " "}]}))
    assert len(story.load_script(p)["scenes"]) == 1


def test_chart_frame_renders():
    chart = {"type": "line", "title": "X", "labels": ["a", "b", "c"], "values": [1, 3, 2], "unit": "%"}
    img = story.chart_frame(chart, 0.5, (640, 360), story.STORY_DEFAULTS["chart_theme"])
    assert img.size == (640, 360)


def test_commons_licence_filter():
    import commons
    for ok in ("Public domain", "CC0", "CC BY 2.0", "CC BY-SA 4.0"):
        assert commons.allowed(ok)
    for bad in ("CC BY-NC 2.0", "CC BY-ND 4.0", "All rights reserved", "Fair use"):
        assert not commons.allowed(bad)


def test_commons_credit_line():
    import commons
    ph = {"title": "Store front.jpg", "author": "Jane Doe", "license": "CC BY 2.0",
          "page": "https://commons.wikimedia.org/wiki/File:Store_front.jpg"}
    assert commons.credit(ph) == ('"Store front" by Jane Doe, CC BY 2.0, via Wikimedia Commons '
                                  '(https://commons.wikimedia.org/wiki/File:Store_front.jpg)')


def test_inline_tags_bind_to_their_paragraph(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("Keyence\n\n## Origins\nIn 1974 Takizaki founded a sensor company. [[entity:Keyence headquarters]]\n\n"
                 "Today its margin is above fifty percent [[chart:bar|Operating margin (%)|Keyence=55, Toyota=10|%|FY2023]].\n\n"
                 "Most factories would kill for ten.\n")
    s = story.load_script(p)["scenes"]
    assert s[0] == {"text": "In 1974 Takizaki founded a sensor company.", "heading": "Origins",
                    "photo": "Keyence headquarters"}
    assert s[1]["text"] == "Today its margin is above fifty percent."
    assert s[1]["chart"] == {"type": "bar", "title": "Operating margin (%)", "labels": ["Keyence", "Toyota"],
                             "values": [55.0, 10.0], "unit": "%", "source": "FY2023"}
    assert s[2] == {"text": "Most factories would kill for ten."}


def test_json_fields_win_over_inline_tags(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"title": "T", "scenes": [{"text": "Hi [[query:office]]", "query": "desk"}]}))
    assert story.load_script(p)["scenes"][0] == {"text": "Hi", "query": "desk"}


def test_bad_tags_are_rejected():
    import pytest
    for bad in ("x [[movie:a]]", "x [[photo:a]] [[entity:b]]", "x [[chart:pie|T|a=1]]",
                "x [[chart:bar|T|a=one]]", "x [[query: ]]"):
        with pytest.raises(ValueError):
            story.parse_tags(bad)


def test_paragraphs_strip_markdown_and_stage_directions():
    out = story._paragraphs("**Hello** there. You are strong! Are you ready? Yes indeed my friend.\n\n"
                            "- One more line here today.\n\n[Pause] Rise up now. (soft music) Go.")
    assert out == ["Hello there. You are strong! Are you ready?", "Yes indeed my friend.",
                   "One more line here today.", "Rise up now. Go."]


def test_chapters_kept_when_headings_hidden(tmp_path, monkeypatch):
    """show_headings=False still gives YouTube chapters in the description."""
    p = tmp_path / "s.txt"
    p.write_text("T\n\n## One\nA b c.\n\n## Two\nD e f.\n")
    data = story.load_script(p)
    meta_json, meta_md, video = tmp_path / "v.publish.json", tmp_path / "v.publish.md", tmp_path / "v.mp4"
    story.write_publish(data, [(0, "One"), (65, "Two")], meta_md, meta_json, video)
    meta = json.loads(meta_json.read_text())
    assert "0:00 One" in meta["description"] and "1:05 Two" in meta["description"]
    assert meta["title_en"] == "T" and meta["video"] == "v.mp4" and "title_ja" in meta


def test_illustrate_prompt_guard():
    import illustrate
    p = illustrate.clean("Guts from Berserk standing like Goku, dark rain")
    assert "guts" not in p.lower() and "berserk" not in p.lower() and "goku" not in p.lower()
    assert "dark rain" in p


def test_bhatt_preset_loads(sandbox):
    s = story.load_story_settings("bhatt")
    assert s["saturation"] == 0.0 and s["subs_engine"] == "kinetic" and s["visuals"] == "ai"
    assert s["tts_pitch"].endswith("Hz")


def test_dedupe_drops_repeated_sentences():
    seen = set()
    first = story._dedupe(["Pain is a teacher. Listen to it."], seen)
    again = story._dedupe(["Pain is a teacher! Silence speaks louder than words."], seen)
    assert first == ["Pain is a teacher. Listen to it."]
    assert again == ["Silence speaks louder than words."]


def test_kinetic_bhatt_preset_layout():
    """BHATT captions: lowercase tight sans, at most one serif-italic key word per phrase."""
    import sys
    sys.path.insert(0, str(story.HERE / "kinetic_captions"))
    from kc.style import build_layout
    text = "Why do you struggle against temptation? Discipline is choosing what you want most."
    words, t = [], 0.0
    for i, w in enumerate(text.split()):
        words.append({"text": w, "start": t, "end": t + 0.3, "prob": 1, "loudness": 0.5 + 0.4 * (i % 3 == 0),
                      "pitch": 0.5, "pause_after": 0.05})
        t += 0.35
    lay = build_layout({"words": words}, 1920, 1080, preset="bhatt")
    for ph in lay["phrases"]:
        ws = [w for ln in ph["lines"] for w in ln["words"]]
        assert all(w["text"] == w["text"].lower() for w in ws)
        assert sum(w["weight"] == "serif_it" for w in ws) <= 1
        assert {w["weight"] for w in ws} <= {"tight", "serif_it"}
        assert 0.35 < ph["pos"]["cx"] < 0.65 and 0.35 < ph["pos"]["cy"] < 0.65
    assert any(w["weight"] == "serif_it" for ph in lay["phrases"] for ln in ph["lines"] for w in ln["words"])


def test_trim_bands_cuts_bars_not_negative_space(tmp_path):
    """klein's pillarboxed panels lose their bars; a dark void on one side of a picture stays."""
    import numpy as np
    from PIL import Image
    import illustrate
    rng = np.random.default_rng(0)
    art = lambda w, h: rng.integers(0, 255, (h, w), dtype=np.uint8)       # textured "drawing"
    # 1) black bars left and right of a narrow panel, inside a thin white margin
    a = np.full((544, 960), 255, np.uint8); a[20:524, 20:940] = 0; a[30:514, 260:700] = art(440, 484)
    Image.fromarray(a).save(tmp_path / "bars.png")
    w, h = Image.open(illustrate.trim_bands(tmp_path / "bars.png", tmp_path / "o1.png")).size
    assert 420 <= w <= 470 and h < 544
    # 2) picture with a flat black void on the right only: kept (at most a thin margin goes)
    b = np.zeros((544, 960), np.uint8); b[:, :600] = art(600, 544)
    Image.fromarray(b).save(tmp_path / "void.png")
    w, _ = Image.open(illustrate.trim_bands(tmp_path / "void.png", tmp_path / "o2.png")).size
    assert w >= 0.9 * 960
    # 3) a full picture is left alone
    Image.fromarray(art(960, 544)).save(tmp_path / "full.png")
    assert illustrate.trim_bands(tmp_path / "full.png", tmp_path / "o3.png") == tmp_path / "full.png"


def test_ai_pictures_cut_faster_at_the_start():
    import illustrate
    s = {"image_seconds": 9.0}
    assert illustrate.shot_seconds(0, s) == illustrate.shot_seconds(40, s) == 3.0       # hook
    assert 3.0 < illustrate.shot_seconds(100, s) < 9.0                                  # relaxing
    assert illustrate.shot_seconds(400, s) == 9.0                                       # steady
    assert illustrate.shot_seconds(0, {"image_seconds": 2.0}) == 2.0                    # never slower than the base


def test_scenes_are_three_to_four_whole_sentences(tmp_path):
    long = " ".join(f"This is sentence number {i} and it keeps going for a little while longer." for i in range(9))
    p = tmp_path / "s.txt"
    p.write_text(f"Title\n\nOne short. Two short.\n\nThree here. Four here.\n\n## Chapter\n\n{long}\n\n"
                 "[[query:storm]] Tagged one. Tagged two.\n")
    sc = story.load_script(p)["scenes"]
    counts = [len(story.sentences_of(x["text"])) for x in sc]
    assert counts == [4, 3, 3, 3, 2]                      # short paragraphs joined, long one cut between sentences
    assert sc[1]["heading"] == "Chapter" and "heading" not in sc[0]
    assert sc[-1]["query"] == "storm" and sc[-1]["text"] == "Tagged one. Tagged two."   # a tag keeps its own scene
    body = " ".join(x["text"] for x in sc[1:4])
    assert body == long                                    # no sentence was cut or lost
    one = "word " * 120 + "end."                           # a single long sentence is never cut
    p.write_text(f"T\n\n{one}\n"); assert len(story.load_script(p)["scenes"]) == 1


def test_image_prompts_are_planned_in_small_batches_with_context(monkeypatch):
    import illustrate, prep
    calls = []
    monkeypatch.setattr(prep, "ensure_ollama", lambda log=print: None)

    def fake_ask(prompt, model):
        n = int(prompt.split("exactly ")[1].split(" ")[0])
        calls.append(prompt)
        return [f"picture {len(calls)}-{i}" for i in range(n)]
    monkeypatch.setattr(illustrate, "_ask_json", fake_ask)
    scenes = [{"text": f"Paragraph number {i} about a door."} for i in range(14)]
    got = illustrate.llm_prompts(scenes, "a man", "m")
    assert len(got) == 14 and len(calls) == 3                      # 6 + 6 + 2
    assert "The paragraph before" not in calls[0] and "Paragraph number 5" in calls[1]
    assert "The paragraph after" in calls[0] and "Paragraph number 12" in calls[1].split("after")[1]
    # a batch that fails twice falls back to keywords for its scenes only
    monkeypatch.setattr(illustrate, "_ask_json", lambda p, m: (_ for _ in ()).throw(RuntimeError("x")) if "Paragraph number 0" in p else ["a"] * int(p.split("exactly ")[1].split(" ")[0]))
    got = illustrate.llm_prompts(scenes, "a man", "m")
    assert got[:6] == [""] * 6 and got[6:] == ["a"] * 8


def test_finish_looks_and_art_colour(sandbox):
    import make_video as mv
    f = mv.load_finish()
    assert f["look"] == "original" and mv.finish_filter(f) == ""                     # nothing saved: no filter
    for look in mv.LOOKS:                                                             # every preset is a valid ffmpeg chain
        flt = mv.finish_filter({**f, "look": look})
        if flt:
            mv.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=64x64:d=0.1", "-vf", flt, "-f", "null", "-"])
    mv.save_finish({**f, "look": "mono", "art": "color"})
    assert "hue=s=0" in mv.finish_filter(mv.load_finish())
    s = story.load_story_settings("bhatt")
    assert s["art"] == "color" and s["saturation"] == 1.0                             # colour art: the ink preset's grey is undone
