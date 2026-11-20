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


def test_script_words_drops_words_the_script_does_not_say():
    bounds = [("1.", 1.52, 2.04), ("When", 3.14, 3.2), ("applause", 3.2, 3.5),
              ("pause", 3.5, 3.8), ("is", 3.8, 4.0), ("asleep.", 4.0, 4.4)]
    words = story.script_words("When applause is asleep.", bounds)
    assert [w["w"] for w in words] == ["When", "applause", "is", "asleep."]
    assert words[0]["s"] == 3.14


def test_script_words_keeps_script_spelling_when_one_letter_off():
    bounds = [("Here", 0.0, 0.3), ("this", 0.3, 0.5), ("clearly.", 0.5, 0.9)]
    words = story.script_words("Hear this clearly.", bounds)
    assert [w["w"] for w in words] == ["Hear", "this", "clearly."]


def test_write_words_listens_to_every_scene_with_one_model_load(tmp_path, monkeypatch):
    scenes = [{"text": "When applause is asleep."}, {"text": "He is performing."}]
    wavs = [tmp_path / "a.wav", tmp_path / "b.wav"]
    calls = []

    def many(audios, model):
        calls.append(list(audios))
        return [
            [{"w": "When", "s": 0, "e": 0.2}, {"w": "applause", "s": 0.2, "e": 0.4},
             {"w": "is", "s": 0.4, "e": 0.5}, {"w": "asleep.", "s": 0.5, "e": 0.8}],
            [{"w": "He", "s": 0, "e": 0.2}, {"w": "is", "s": 0.2, "e": 0.3}, {"w": "performing.", "s": 0.3, "e": 0.6}],
        ]

    monkeypatch.setattr(story.mv, "whisper_words_many", many)
    monkeypatch.setattr(story, "free_models", lambda: None)
    story.write_words(scenes, wavs)
    story.write_words(scenes, wavs)
    assert len(calls) == 1 and calls[0] == wavs
    saved = json.loads(wavs[0].with_suffix(".words.json").read_text())
    assert [w["w"] for w in saved] == ["When", "applause", "is", "asleep."]


def test_load_script_rejects_empty_and_bad_shape(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    try:
        story.load_script(empty)
    except ValueError as e:
        assert "title" in str(e)
    else:
        raise AssertionError("empty file should be rejected")
    listed = tmp_path / "list.json"
    listed.write_text("[]")
    try:
        story.load_script(listed)
    except ValueError:
        pass
    else:
        raise AssertionError("a JSON list is not a script")
    bad = tmp_path / "str.json"
    bad.write_text(json.dumps({"title": "T", "scenes": ["hello"]}))
    try:
        story.load_script(bad)
    except ValueError as e:
        assert "text" in str(e)
    else:
        raise AssertionError("a scene string should be rejected")


def test_slug_keeps_english_and_separates_titles_without_letters():
    assert story.slug("My Title") == "My_Title"
    assert story.slug("孤独な男") != story.slug("沈黙")
    assert story.slug("!!!") != story.slug("???")
    assert story.slug("孤独な男").startswith("story_")


def test_script_words_skips_missing_times():
    words = story.script_words("Hello there friend", [("Hello", None, 0.2), ("friend", 0.4, 0.8)])
    assert [w["w"] for w in words] == ["Hello", "there", "friend"]
    assert words[2]["s"] == 0.4


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


def test_kinetic_stop_words_do_not_hold_alone():
    """Inflated 'It'/'The' timings (Cosy/Whisper parking silence) must not linger alone."""
    import sys
    sys.path.insert(0, str(story.HERE / "kinetic_captions"))
    from kc.style import build_layout
    words = [
        {"text": "It", "start": 0.0, "end": 0.46, "loudness": 0.3, "pitch": 0.5, "pause_after": 0.0},
        {"text": "leaves.", "start": 0.46, "end": 0.70, "loudness": 0.8, "pitch": 0.5, "pause_after": 0.5},
        {"text": "The", "start": 1.2, "end": 1.55, "loudness": 0.2, "pitch": 0.4, "pause_after": 0.0},
        {"text": "decision", "start": 1.55, "end": 2.0, "loudness": 0.7, "pitch": 0.5, "pause_after": 0.05},
        {"text": "stays.", "start": 2.05, "end": 2.4, "loudness": 0.7, "pitch": 0.5, "pause_after": 0.0},
    ]
    lay = build_layout({"words": words}, 1080, 1920, preset="bhatt")
    flat = [w for ph in lay["phrases"] for ln in ph["lines"] for w in ln["words"]]
    it = next(w for w in flat if w["text"] == "it")
    the = next(w for w in flat if w["text"] == "the")
    assert it["end"] - it["start"] <= 0.20
    assert the["end"] - the["start"] <= 0.20
    # glued into a meaningful phrase, not a lone THE/IT card
    texts = [" ".join(w["text"] for ln in ph["lines"] for w in ln["words"]) for ph in lay["phrases"]]
    assert any("it leaves" in t for t in texts)
    assert any("the decision" in t for t in texts)
    assert not any(t.strip() in ("it", "the") for t in texts)


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


def test_zoom_settings_and_cross_zoom_join(sandbox, tmp_path):
    import make_video as mv
    png = tmp_path / "pic.png"
    mv.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=960x544:d=0.04", "-frames:v", "1", str(png)])
    base = story.load_story_settings("bhatt")
    segs = []
    for i, (amount, focus) in enumerate([("none", "face"), ("slow", "left"), ("normal", "auto"), ("strong", "bottom"), ("normal", "right")]):
        s = {**base, "zoom_amount": amount, "zoom_focus": focus, "cross": 0.9}
        d = tmp_path / f"c{i}.mp4"
        story.render_photo(png, 2.0 + 0.9, d, s, n=i)
        assert abs(mv.duration(d) - 2.9) < 0.1
        segs.append(d)
    out = story.join_crosszoom(segs, {**base, "cross": 0.9}, tmp_path, group=3)
    assert abs(mv.duration(out) - (5 * 2.0 + 0.9)) < 0.15            # overlapping keeps the narration timeline
    f = mv.load_finish()
    assert f["zoom_amount"] == "normal" and f["zoom_focus"] == "face" and f["transition_speed"] == "normal"
    mv.save_finish({**f, "zoom_amount": "bogus", "zoom_focus": "nowhere", "transition_speed": "x"})
    g = mv.load_finish()
    assert (g["zoom_amount"], g["zoom_focus"], g["transition_speed"]) == ("normal", "face", "normal")   # bad values fall back
    s = story.load_story_settings("bhatt")
    mv.save_finish({**f, "zoom_amount": "slow", "zoom_focus": "right", "transition_speed": "slow"})
    s = story.load_story_settings("bhatt")
    assert (s["zoom_amount"], s["zoom_focus"], s["cross"]) == ("slow", "right", 1.5)


def test_presets_motivation_and_auto_load(sandbox):
    import server
    assert set(server.STORY_PRESETS) == {"auto", "bhatt"}
    a, b = story.load_story_settings("auto"), story.load_story_settings("bhatt")
    assert b["hero"] == "" and b["art"] == "mono" and a["hero"] == "none" and a["art"] == "color"
    assert server.story_settings({"preset": "auto", "options": {"hero": ""}})[1]["hero"] == "none"   # empty = keep the preset's


def test_planner_reads_small_model_answers(monkeypatch):
    import illustrate, prep, json as _json, urllib.request as ur
    monkeypatch.setattr(prep, "ensure_ollama", lambda log=print: None)

    def fake(reply):
        class R:
            def read(self): return _json.dumps({"message": {"content": _json.dumps(reply)}}).encode()
        monkeypatch.setattr(ur, "urlopen", lambda *a, **k: R())
        return illustrate._ask_json("p", "m")
    assert fake({"pictures": ["a", "b"]}) == ["a", "b"]
    assert fake({"image1": "a", "image2": "b"}) == ["a", "b"]       # what qwen2.5 really answers
    assert fake(["a", "b"]) == ["a", "b"]


def test_auto_genre_asks_the_ai_for_the_look_and_remembers_it(sandbox, tmp_path, monkeypatch):
    import json as _json
    calls = []

    def fake(prompt, model, as_json=False, timeout=600):
        calls.append(prompt)
        return _json.dumps({"genre": "History", "look": "gritty oil painting with muted blues and grey, dramatic light and heavy shadows",
                            "art": "color", "hero": "a tired ship's officer in a dark uniform", "subjects": "Show the ship, the sea and the crew.",
                            "mood": "dark"})
    monkeypatch.setattr(story, "_ollama", fake)
    data = {"title": "The Night the Ship Sank", "scenes": [{"text": "The bell rang three times."}]}
    s = story.load_story_settings("auto")
    s["hero"] = "none"
    story.art_direction(data, s, tmp_path, log=lambda *a: None)
    assert "oil painting" in s["style_color"] and "no text" in s["style_color"] and "black and white" in s["style_mono"]
    assert s["hero"].startswith("a tired ship") and s["planner_hint"] == "Show the ship, the sea and the crew." and s["bgm_mood"] == "dark"
    story.art_direction(data, story.load_story_settings("auto"), tmp_path, log=lambda *a: None)
    assert len(calls) == 1                                        # same script: the saved direction is reused
    monkeypatch.setattr(story, "_ollama", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    s2 = story.load_story_settings("auto")
    story.art_direction({"title": "T2", "scenes": [{"text": "Other."}]}, s2, tmp_path / "x", log=lambda *a: None)
    assert "cinematic illustration" in s2["style_color"]           # AI down: a sensible default, never a crash


def test_shot_plan_is_json_timed_to_the_sentences(monkeypatch):
    import illustrate, prep, json as _json, urllib.request as ur
    monkeypatch.setattr(prep, "ensure_ollama", lambda log=print: None)
    monkeypatch.setattr(illustrate.mv, "unload_llm", lambda: None)
    text = "One was said here. Two was said here. Three was said here. Four was said here."
    words = [{"w": w, "s": round(i * 0.5, 2), "e": round(i * 0.5 + 0.4, 2)} for i, w in enumerate(text.split())]   # 4 words/sentence = 2 s each
    sc = {"text": text, "dur": 8.35, "words": words}
    assert illustrate.sentence_starts(sc) == [0.0, 2.0, 4.0, 6.0]
    sent = []

    def reply(shots):
        class R:
            def read(self): return _json.dumps({"message": {"content": _json.dumps({"shots": shots})}}).encode()
        def fake(req, timeout=0):
            sent.append(_json.loads(req.data))
            return R()
        monkeypatch.setattr(ur, "urlopen", fake)
    s = {"llm_model": "m", "hero": "none"}
    reply([{"prompt": "a lighthouse", "sentence": 1}, {"prompt": "an iceberg", "sentence": 3}, {"prompt": "a ship", "sentence": 4}])
    plan = illustrate.plan_shots([sc], s, lambda *a: None, {0: 3})
    assert sent[0]["format"] == illustrate.SHOT_SCHEMA                       # structured output: the answer is schema-valid JSON
    sh = sc["shots"]
    assert [x["start"] for x in sh] == [0.0, 4.0, 6.0] and [x["end"] for x in sh] == [4.0, 6.0, 8.35]
    assert sh[1]["prompt"] == "an iceberg" and plan[0]["scene"] == 1
    reply([{"prompt": "a", "sentence": 1}, {"prompt": "b", "sentence": 2}, {"prompt": "c", "sentence": 2}])    # too close: dropped
    sc2 = {"text": text, "dur": 8.35, "words": words}
    illustrate.plan_shots([sc2], s, lambda *a: None, {0: 3})
    assert [x["prompt"] for x in sc2["shots"]] == ["a", "b"]
    monkeypatch.setattr(ur, "urlopen", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    sc3 = {"text": text, "dur": 8.35, "words": words}
    illustrate.plan_shots([sc3], s, lambda *a: None, {0: 3})
    assert "shots" not in sc3                                               # LLM down: the old even split still works


def test_timeline_sends_the_whole_script_once(monkeypatch):
    import illustrate, prep, json as _json, urllib.request as ur
    monkeypatch.setattr(prep, "ensure_ollama", lambda log=print: True)
    monkeypatch.setattr(illustrate.mv, "unload_llm", lambda: None)
    sent = []
    script = "The bell rang three times.\n\nIce lay ahead of the ship."
    replies = [
        _json.dumps([
            {"n": 1, "image_prompt": "a brass ship's bell in fog, cold light", "extra": "no"},
            {"n": 2, "image_prompt": "an iceberg ahead of the same ship, wide and still"},
        ]),
    ]

    class R:
        def __init__(self, content):
            self._b = _json.dumps({"message": {"content": content}}).encode()
        def read(self):
            return self._b

    def fake(req, timeout=0):
        body = _json.loads(req.data)
        sent.append(body)
        return R(replies[len(sent) - 1])

    monkeypatch.setattr(ur, "urlopen", fake)
    made = illustrate.video_timeline(script, "ink, high contrast", "a tired officer", "FLUX.2 klein 4B", "m", log=lambda *a: None)
    assert len(sent) == 1
    call = sent[0]
    assert call["format"]["properties"]["timeline"]["minItems"] == 2
    assert call["options"]["num_ctx"] == 8192
    assert call["messages"][0]["role"] == "system" and "WHOLE script" in call["messages"][0]["content"]
    assert "visual progression" in call["messages"][0]["content"]
    assert "ink, high contrast" in call["messages"][0]["content"]
    asked = call["messages"][1]["content"]
    assert "exactly 2" in asked and "1. The bell rang three times." in asked and "2. Ice lay ahead of the ship." in asked
    assert made["timeline"] == [
        {"script_segment": "The bell rang three times.", "image_prompt": "a brass ship's bell in fog, cold light"},
        {"script_segment": "Ice lay ahead of the ship.", "image_prompt": "an iceberg ahead of the same ship, wide and still"},
    ]
    wrapped = illustrate.parse_timeline('{"pictures": [{"script_segment": "A.", "image_prompt": "a door"}]}')
    assert wrapped == [{"script_segment": "A.", "image_prompt": "a door"}]


def test_timeline_retries_the_whole_script_not_one_paragraph(monkeypatch):
    import illustrate, prep, json as _json, urllib.request as ur
    monkeypatch.setattr(prep, "ensure_ollama", lambda log=print: True)
    monkeypatch.setattr(illustrate.mv, "unload_llm", lambda: None)
    replies = [
        _json.dumps([{"n": 1, "image_prompt": "a brass ship's bell in fog"}]),
        _json.dumps({"timeline": [
            {"n": 1, "image_prompt": "a brass ship's bell in fog"},
            {"n": 2, "image_prompt": "ice ahead of that ship at night"},
        ]}),
    ]
    sent = []

    class R:
        def __init__(self, content):
            self._b = _json.dumps({"message": {"content": content}}).encode()
        def read(self):
            return self._b

    def fake(req, timeout=0):
        sent.append(_json.loads(req.data))
        return R(replies[len(sent) - 1])

    monkeypatch.setattr(ur, "urlopen", fake)
    script = "The bell rang three times.\n\nIce lay ahead of the ship."
    made = illustrate.video_timeline(script, "ink", "an officer", "FLUX.2 klein 4B", "m", log=lambda *a: None)
    assert len(sent) == 2
    for call in sent:
        text = call["messages"][1]["content"]
        assert "1. The bell rang three times." in text and "2. Ice lay ahead of the ship." in text
        assert call["format"]["properties"]["timeline"]["minItems"] == 2
    assert [row["script_segment"] for row in made["timeline"]] == [
        "The bell rang three times.", "Ice lay ahead of the ship."]
    assert made["timeline"][1]["image_prompt"] == "ice ahead of that ship at night"


def test_plan_uses_the_timeline_prompts(monkeypatch, tmp_path):
    import illustrate
    scenes = [{"text": "The bell rang."}, {"text": "Ice ahead.", "image_prompt": "kept prompt"}]

    def fake_timeline(script, art, character, target, model, log=print, beats=None):
        assert script == "The bell rang."
        assert beats and beats[0]["text"] == "The bell rang."
        assert "officer" in character and target == "FLUX.2 klein 4B"
        return {"system_prompt": "direction for this video", "art_style": art, "character_setting": character,
                "target_ai": target, "timeline": [{"script_segment": "The bell rang.", "image_prompt": "a bell in fog"}]}

    monkeypatch.setattr(illustrate, "video_timeline", fake_timeline)
    called = []
    monkeypatch.setattr(illustrate, "llm_prompts", lambda *a, **k: called.append(1) or ["nope"])
    s = {"llm_model": "m", "hero": "a tired officer", "art": "mono", "image_model": "klein", "_work": str(tmp_path)}
    illustrate.plan(scenes, s, log=lambda *a: None)
    assert scenes[0]["image_prompt"] == "a bell in fog" and scenes[1]["image_prompt"] == "kept prompt"
    assert called == [] and s["_from_timeline"] is True
    saved = __import__("json").loads((tmp_path / "timeline.json").read_text())
    assert saved["system_prompt"] == "direction for this video" and saved["timeline"][0]["image_prompt"] == "a bell in fog"
    monkeypatch.setattr(illustrate, "video_timeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    again = [{"text": "A quiet road."}]
    s2 = {"llm_model": "m", "hero": "none", "art": "mono", "image_model": "klein"}
    monkeypatch.setattr(illustrate, "llm_prompts", lambda *a, **k: ["keyword road"])
    illustrate.plan(again, s2, log=lambda *a: None)
    assert again[0]["image_prompt"] == "keyword road" and "_from_timeline" not in s2


def test_picture_count_does_not_copy_one_prompt():
    import illustrate
    shots = [{"prompt": "a library", "start": 0, "end": 8}, {"prompt": "a lighthouse", "start": 8, "end": 20}]
    assert illustrate.picture_count({"shots": shots}, {"_from_timeline": True}, 7) == 2
    assert illustrate.picture_count({"dur": 20}, {"_from_timeline": True}, 7) == 1
    assert illustrate.picture_count({"dur": 20}, {}, 7) == 7


def test_beats_follow_the_spoken_length_and_change_picture(monkeypatch):
    import illustrate, prep, json as _json, urllib.request as ur
    monkeypatch.setattr(prep, "ensure_ollama", lambda log=print: True)
    monkeypatch.setattr(illustrate.mv, "unload_llm", lambda: None)
    text = "One was said here. Two was said here. Three was said here. Four was said here."
    words = [{"w": w, "s": round(i * 0.5, 2), "e": round(i * 0.5 + 0.4, 2)} for i, w in enumerate(text.split())]
    sc = {"text": text, "dur": 8.35, "words": words}
    s = {"image_seconds": 3.0, "hook_seconds": 3.0, "hook_until": 45.0, "hook_ramp": 120.0,
         "llm_model": "m", "hero": "none", "art": "mono", "image_model": "klein"}
    beats = illustrate.narration_beats([sc], s)
    assert [(b["start"], b["end"]) for b in beats] == [(0.0, 4.0), (4.0, 8.35)]
    assert "One was said here." in beats[0]["text"] and "Three was said here." in beats[1]["text"]
    sent = []

    class R:
        def __init__(self, content):
            self._b = _json.dumps({"message": {"content": content}}).encode()
        def read(self):
            return self._b

    def fake(req, timeout=0):
        sent.append(_json.loads(req.data))
        return R(_json.dumps({"timeline": [
            {"n": 1, "image_prompt": "a man at a crossroads at dusk"},
            {"n": 2, "image_prompt": "the same man watching a distant lighthouse"},
        ]}))

    monkeypatch.setattr(ur, "urlopen", fake)
    illustrate.plan([sc], s, log=lambda *a: None)
    asked = sent[0]["messages"][1]["content"]
    assert "exactly 2" in asked and "(0.0-4.0s)" in asked and "(4.0-8.3s)" in asked
    assert "visual progression" in sent[0]["messages"][0]["content"]
    assert "One was said here." in asked and "Four was said here." in asked
    assert [(x["prompt"], x["start"], x["end"]) for x in sc["shots"]] == [
        ("a man at a crossroads at dusk", 0.0, 4.0),
        ("the same man watching a distant lighthouse", 4.0, 8.35),
    ]
    assert illustrate.picture_count(sc, s, 7) == 2


def test_emit_ready_reports_finished_pictures_in_order(tmp_path):
    import illustrate
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    first.write_bytes(b"x")
    order = [
        {"scene": 0, "shot": 0, "shots": 2, "path": first},
        {"scene": 0, "shot": 1, "shots": 2, "path": second},
    ]
    got = []
    seen = illustrate._emit_ready(order, 0, 2, got.append)
    assert seen == 2 and [item["shot"] for item in got] == [0]
    second.write_bytes(b"y")
    seen = illustrate._emit_ready(order, 1, 2, got.append)
    assert seen == 2 and [item["shot"] for item in got] == [0, 1]


def test_mux_audio_copies_the_picture_when_captions_will_redraw_it(monkeypatch):
    import make_video as mv
    seen = []
    monkeypatch.setattr(mv, "run", lambda cmd: seen.append(cmd))
    s = {"bitrate": "8M", "bgm_volume": 0.22, "fade_out": 1.0}
    story.mux_audio("list.txt", "voice.wav", None, 8.0, s, "out.mp4")
    assert seen[0][seen[0].index("-c:v") + 1] == "copy"
    assert "-vf" not in seen[0]
    story.mux_audio("list.txt", "voice.wav", None, 8.0, s, "out.mp4", vf="fade=t=in:d=0.4")
    assert seen[1][seen[1].index("-c:v") + 1] == "h264_videotoolbox"
    assert "fade=t=in:d=0.4" in seen[1]
