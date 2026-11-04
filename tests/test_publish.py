"""publish_meta.py: quality gates and publish_kit() with the LLM (HTTP) and thumbnails mocked."""
import json

import pytest

import publish_meta as pm

QUIT_TEXT = ("When you feel like quitting, remember what you're doing it all for. Push through the pain. "
             "Don't let it stop you. Nothing great is going to come if you quit. Give me ten more. "
             "They said you were never going to make it. They were wrong. Prove them wrong every single day.")


def ctx(title, text="", used=(), key_lines=()):
    return pm.Context(title, text or title, key_lines=key_lines, used_thumbs=used)


# ---------------------------------------------------------------- thumbnail gate
LIFE = ("The Quality Of Your Life Is The Quality Of Your Emotions",
        "The quality of your life is the quality of your emotions. Build your emotional muscle. "
        "Raise your standards and change your life.")
DISCIPLINE = ("Discipline - Motivational Speech (Powerful)",
              "Discipline is doing what you hate to do but doing it like you love it. Stay disciplined every day.")


@pytest.mark.parametrize("phrase,speech", [
    ("SHOW STRONGNESS", LIFE),            # not a real word
    ("STRAP YOUR POTENTIAL", LIFE),       # broken English
    ("FLOAT STING WHIP", LIFE),           # word salad
    ("FIGHT STING WHIP", ("Fight", "fight sting whip")),
    ("BUILD YOUR MUSCLE", LIFE),          # literal misreading of "emotional muscle"
    ("HEAD STRAIGHT TO THE", LIFE),       # dangling ending
    ("DISCIPLINE GROWTH", DISCIPLINE),    # two nouns
    ("BE A LION", DISCIPLINE),            # copied prompt example, off-topic
    ("STAY", DISCIPLINE),                 # too short
    ("NEVER EVER GIVE UP NOW", DISCIPLINE),  # too long
    ("BE CHAMPION", DISCIPLINE),          # BE + bare noun
    ("DON'T DISCIPLINE", DISCIPLINE),     # DON'T + non-verb
    ("UNLEASH YOUR POTENTIAL", DISCIPLINE),  # stock phrase, nothing from the speech
])
def test_bad_thumbnail_phrases_rejected(phrase, speech):
    assert pm.thumb_problem(phrase, ctx(*speech)) is not None


@pytest.mark.parametrize("phrase,title,text", [
    ("DON'T QUIT NOW", "When You Feel Like Quitting (Greatness Remix)", QUIT_TEXT),
    ("BE MENTALLY TOUGH", "Mental Toughness - Motivational Speech", "toughness is a choice"),
    ("BE THE GREATEST", "The Greatest - Muhammad Ali Inspirational Video", "I am the greatest"),
    ("PROVE THEM WRONG", "THEY WERE WRONG - Motivational Video", QUIT_TEXT),
    ("DARE TO BE ODD", "Statistics (You Have to Be Odd to Be Number One)", "you have to be odd"),
    ("IT'S YOUR TIME", "Time For Greatness - Motivational Speech", "this is the time"),
    ("TAKE MASSIVE ACTION", "Take Action", "take massive action"),
    ("RAISE YOUR STANDARDS", *LIFE),
    ("STAY DISCIPLINED", *DISCIPLINE),
    ("Don’t quit now!", "When You Feel Like Quitting", QUIT_TEXT),     # punctuation / curly quote
    ("FLOAT LIKE A BUTTERFLY", "The Greatest - Muhammad Ali", "float like a butterfly, sting like a bee"),
])
def test_good_thumbnail_phrases_pass(phrase, title, text):
    assert pm.thumb_problem(phrase, ctx(title, text)) is None


@pytest.mark.parametrize("phrase,used", [
    ("BELIEVE IN YOURSELF", ["BELIEVE IN YOURSELF"]),
    ("Believe in yourself!", ["BELIEVE IN YOURSELF"]),
    ("BE TRUE TO YOURSELF", ["STAY TRUE TO YOURSELF"]),
    ("NEVER QUIT", ["DON'T QUIT NOW"]),
    ("BE STRONGER", ["STAY STRONG"]),
    ("KEEP FIGHTING", ["FIGHT BACK"]),
    ("TAKE CONTROL NOW", ["TAKE CONTROL"]),
])
def test_duplicate_thumbnail_phrases_rejected(phrase, used):
    c = ctx("Believe Quit Strong True", "believe in yourself quit strong true", used=used)
    why = pm.thumb_problem(phrase, c)
    assert why and "already used" in why


def test_different_phrases_are_not_duplicates():
    assert not pm.near_duplicate("BE THE GREATEST", "BE THE BEST")
    assert not pm.near_duplicate("FIGHT BACK", "FIGHT FOR YOUR DREAMS")
    assert not pm.near_duplicate("FACE YOUR FEAR", "FIND YOUR WHY")
    assert not pm.near_duplicate("BE THE GREATEST", "UNLEASH GREATNESS")
    assert not pm.near_duplicate("NEVER GIVE UP", "GIVE YOUR BEST")
    assert not pm.near_duplicate("FIND YOUR VOICE", "FIND YOUR WHY")


def test_real_word_check():
    assert all(pm.is_word(w) for w in ("quitting", "greatest", "wolves", "dreams", "mentally", "don't", "it's"))
    assert not pm.is_word("strongness")
    assert pm.is_word("strongness", {"strongness"})          # unless the speaker said it


def test_fallback_from_title():
    assert pm.fallback_thumb(ctx("THEY WERE WRONG - Motivational Video - YouTube")) == "PROVE THEM WRONG"
    assert pm.fallback_thumb(ctx("Statistics (You Have to Be Odd to Be Number One)")) == "DARE TO BE ODD"
    assert pm.fallback_thumb(ctx("Take Responsibility For Your Life")) == "TAKE RESPONSIBILITY"
    # all rule phrases used -> a curated phrase that is not used yet
    c = ctx("THEY WERE WRONG", used=["PROVE THEM WRONG", "SILENCE THE DOUBTERS"])
    out = pm.fallback_thumb(c)
    assert out in pm.STRONG and pm.thumb_problem(out, c, need_relevance=False) is None


# ---------------------------------------------------------------- title gate
def test_title_gate():
    c = ctx("THEY WERE WRONG", QUIT_TEXT)
    c.used_titles = ["They Were Wrong About Me"]
    assert "Motivational" in pm.title_problem("Motivational Speech: Prove Them Wrong", c)
    assert "too long" in pm.title_problem("Prove Them Wrong " * 6, c)
    assert "generic" in pm.title_problem("Never Give Up: Believe in Yourself", c)
    assert "another video" in pm.title_problem("They Were Wrong About Me!", c)
    assert "speech source" in pm.title_problem("Prove Them Wrong | Fearless Motivation", c)
    c.source = "Fearless Motivation"
    assert pm.title_problem("Prove Them Wrong | Fearless Motivation", c) is None
    assert pm.title_problem("Push Through the Pain and Prove Them Wrong", c) is None
    assert pm.title_ja_problem("Prove them wrong") and pm.title_ja_problem("あ" * 41)
    assert pm.title_ja_problem("痛みを越えて見返せ") is None


# ---------------------------------------------------------------- speech source
@pytest.mark.parametrize("name,text,expected", [
    ("46_Greatness Within - Ft. Eric Thomas & Les Brown - Motivational Music", "", "Eric Thomas & Les Brown"),
    ("14_Les Brown and Eric Thomas Motivation", "", "Les Brown & Eric Thomas"),
    ("40_Your Reason For Breathing Fearless Motivation Ft. Freddy Fri", "", "Fearless Motivation & Freddy Fri"),
    ("49_Never Quit - Motivational Speech Featuring Walter Bond", "", "Walter Bond"),
    ("18_MOTIVATION DAYS - THE TRUTH MOTIVATIONAL SPEECH BY ERIC THOMAS", "", "Eric Thomas"),
    ("57_Life's Tough - Motivational Speech by Fearless Motivation", "", "Fearless Motivation"),
    ("31_Tom Bilyeu - Face Your FEAR & Do It Anyway - Fearless Motivation", "", "Tom Bilyeu & Fearless Motivation"),
    ("99_Rise - Ft. John Doe & Jane Roe (Remix)", "", "John Doe & Jane Roe"),
    ("23_Contenders", "and David Goggins said, stay hard", "David Goggins"),
    ("29_The Greatest - Muhammad Ali Inspirational Video", "", "Muhammad Ali"),
    ("01_Discipline - Motivational Speech (Powerful)", "discipline is everything", ""),
])
def test_guess_source(name, text, expected):
    assert pm.guess_source(name, text) == expected


# ---------------------------------------------------------------- tags
def test_tags_limits():
    raw = [f"#very long search tag number {i}" for i in range(30)]
    tags = pm.clean_tags(raw, "Eric Thomas", "PROVE THEM WRONG")
    assert 10 <= len(tags) <= 15 and pm.tags_length(tags) <= 500
    # every tag is a single hashtag word: "#VeryLongSearchTagNumber0"
    assert all(t.startswith("#") and t.count("#") == 1 and " " not in t for t in tags)
    assert pm.as_hashtag("motivational speech") == "#MotivationalSpeech"
    assert pm.as_hashtag("#don't quit") == "#DontQuit"
    long = ["x" * 29 + str(i) for i in range(20)]
    tags = pm.clean_tags(long, "", "GO")
    assert pm.tags_length(tags) <= 500 and len(tags) >= 10
    few = pm.clean_tags(["motivation", "Motivation", "grit"], "要確認", "PROVE THEM WRONG")
    assert 10 <= len(few) <= 15 and len(set(few)) == len(few) and "要確認" not in few


def test_fake_quotes_removed():
    out, removed = pm.strip_fake_quotes('He says “push through the pain” and “winners never sleep at all”.', QUIT_TEXT)
    assert "“push through the pain”" in out and "“winners" not in out and removed == ["winners never sleep at all"]


# ---------------------------------------------------------------- publish_kit end to end (mocked)
class FakeLLM:
    """Routes prompts to canned answers; records every prompt."""

    def __init__(self, main, thumbs=(), titles=(), fits=False):
        self.main, self.thumbs, self.titles, self.fits = main, list(thumbs), list(titles), fits
        self.prompts = []

    def __call__(self, body):
        p = body["messages"][0]["content"]
        self.prompts.append(p)
        if "Give 5 NEW thumbnail phrases" in p:
            d = {"thumbnail_candidates": self.thumbs.pop(0) if self.thumbs else ["SHOW STRONGNESS"]}
        elif "previous titles were rejected" in p:
            d = self.titles.pop(0) if self.titles else {}
        elif "Does the thumbnail phrase" in p:
            d = {"fits": self.fits}
        else:
            d = self.main
        return {"message": {"content": json.dumps(d)}}

    def count(self, marker):
        return sum(marker in p for p in self.prompts)


def main_answer(**kw):
    d = {"title_en": "Push Through the Pain and Prove Them Wrong", "title_ja": "痛みを越えて見返せ",
         "description_en": "A speech about pushing through pain.", "description_ja": "痛みを越えるスピーチ。",
         "key_lines": ["Push through the pain.", "This line was never said by anyone."],
         "tags": ["motivation", "never quit", "#grit"], "hashtags": ["#NeverQuit", "Motivation"],
         "thumbnail_candidates": ["PROVE THEM WRONG"]}
    d.update(kw)
    return d


@pytest.fixture
def kit(tmp_path, monkeypatch):
    vids = tmp_path / "videos"; vids.mkdir()
    video = vids / "17_THEY WERE WRONG - Motivational Video - YouTube.mp4"
    video.write_bytes(b"")
    made = []

    def fake_thumb(meta, dest, seed=None, claim_photos=True):
        made.append((meta["thumbnail_text"], claim_photos))
        dest.write_bytes(b"jpg")
        meta["thumbnail_photos"] = []
    monkeypatch.setattr(pm.thumbs, "make_thumbnail", fake_thumb)
    monkeypatch.setattr(pm, "transcript", lambda mp3: QUIT_TEXT)
    monkeypatch.setattr(pm.mv, "load_settings", lambda *a: {"llm_model": "test"})

    def run(llm, name=None, **kw):
        v = video
        if name:
            v = vids / name
            v.write_bytes(b"")
        monkeypatch.setattr(pm, "_post", llm)
        return pm.publish_kit(v, tmp_path / "a.mp3", log=lambda m: None, **kw)
    return vids, video, run, made


def sibling(vids, n, thumb, title="Some Other Title"):
    (vids / f"{n:02d}_Other.publish.json").write_text(json.dumps({"thumbnail_text": thumb, "title_en": title}))


def test_publish_kit_writes_everything(kit):
    vids, video, run, made = kit
    llm = FakeLLM(main_answer())
    meta = run(llm)
    assert meta["thumbnail_text"] == "PROVE THEM WRONG"
    assert meta["quality"] == {**meta["quality"], "thumbnail_attempts": 1, "fallback": False, "rejected": []}
    assert meta["key_lines"] == ["Push through the pain."]                 # made-up line dropped
    assert meta["speech_source"] == "要確認"
    # no credit / tool lines inside the descriptions (user's request)
    for k in ("description_en_full", "description_ja_full"):
        assert not any(x in meta[k] for x in ("Speech:", "Footage", "Pexels", "Motiversity Auto", "音声:", "映像:"))
    assert 10 <= len(meta["tags"]) <= 15 and all(t.startswith("#") for t in meta["tags"])
    assert meta["hashtags"][:2] == ["#NeverQuit", "#Motivation"]
    assert made == [("PROVE THEM WRONG", True)]                            # thumbnail got the final text
    stem = video.stem
    saved = json.loads((vids / f"{stem}.publish.json").read_text())
    assert saved["thumbnail_text"] == "PROVE THEM WRONG"
    assert "要確認" in (vids / f"{stem}.publish.md").read_text()
    # idempotent: no LLM call the second time, unless forced
    llm2 = FakeLLM(main_answer())
    assert run(llm2)["thumbnail_text"] == "PROVE THEM WRONG" and not llm2.prompts
    run(llm2, force=True)
    assert llm2.prompts


def test_publish_kit_skips_duplicates_of_sibling_videos(kit):
    vids, video, run, made = kit
    sibling(vids, 3, "PROVE THEM WRONG")
    meta = run(FakeLLM(main_answer(thumbnail_candidates=["PROVE THEM WRONG", "PUSH THROUGH THE PAIN"])))
    assert meta["thumbnail_text"] == "PUSH THROUGH THE PAIN"
    assert any("already used" in r for r in meta["quality"]["rejected"])


def test_publish_kit_retries_with_feedback(kit):
    vids, video, run, made = kit
    llm = FakeLLM(main_answer(thumbnail_candidates=["SHOW STRONGNESS", "HEAD STRAIGHT TO THE"]),
                  thumbs=[["DON'T QUIT NOW"]])
    meta = run(llm, name="23_Contenders.mp4")        # no title rule for this name
    assert meta["thumbnail_text"] == "DON'T QUIT NOW"
    q = meta["quality"]
    assert q["thumbnail_attempts"] == 2 and not q["fallback"] and len(q["rejected"]) == 2
    retry = next(p for p in llm.prompts if "Give 5 NEW thumbnail phrases" in p)
    assert "SHOW STRONGNESS" in retry and "not a real English word" in retry


BAD = ["SHOW STRONGNESS", "FLOAT STING WHIP", "DISCIPLINE GROWTH", "BE A LION", "STRAP YOUR POTENTIAL"]


def test_publish_kit_uses_title_rule_when_llm_fails(kit):
    vids, video, run, made = kit
    llm = FakeLLM(main_answer(thumbnail_candidates=BAD))
    meta = run(llm)
    q = meta["quality"]
    assert meta["thumbnail_text"] == "PROVE THEM WRONG"                     # from "THEY WERE WRONG"
    assert q["picked_by"] == "title_rule" and q["fallback"] and q["thumbnail_attempts"] == 1
    assert len(q["rejected"]) == len(BAD) and made[-1][0] == "PROVE THEM WRONG"


def test_publish_kit_falls_back_when_llm_keeps_failing(kit):
    vids, video, run, made = kit
    sibling(vids, 3, "SHOW UP DAILY")
    llm = FakeLLM(main_answer(thumbnail_candidates=BAD), thumbs=[BAD, BAD])
    meta = run(llm, name="23_Contenders.mp4")
    q = meta["quality"]
    assert q["fallback"] and q["picked_by"] == "curated" and q["thumbnail_attempts"] == pm.MAX_ATTEMPTS
    assert meta["thumbnail_text"] in pm.STRONG
    assert llm.count("Give 5 NEW thumbnail phrases") == pm.MAX_ATTEMPTS - 1


def test_publish_kit_llm_relevance_check(kit):
    vids, video, run, made = kit
    # grammatical but shares no word with the speech: accepted only if the LLM says it fits
    meta = run(FakeLLM(main_answer(thumbnail_candidates=["CHOOSE YOUR HARD"]), fits=True), name="23_Contenders.mp4")
    assert meta["thumbnail_text"] == "CHOOSE YOUR HARD" and meta["quality"]["picked_by"] == "llm_checked"
    meta = run(FakeLLM(main_answer(thumbnail_candidates=["CHOOSE YOUR HARD"]), fits=False),
               name="23_Contenders.mp4", force=True)
    assert meta["thumbnail_text"] != "CHOOSE YOUR HARD" and meta["quality"]["fallback"]


def test_publish_kit_title_retry_and_fallback(kit):
    vids, video, run, made = kit
    sibling(vids, 4, "GO FURTHER", "Push Through the Pain and Prove Them Wrong Today")
    sibling(vids, 5, "GO HIGHER", "Go Further Than Ever")
    llm = FakeLLM(main_answer(title_en="Motivational Speech: Never Give Up", title_ja="Never give up"),
                  titles=[{"title_en": "They Were Wrong: Push Through the Pain", "title_ja": "見返してやれ"}])
    meta = run(llm)
    assert meta["title_en"] == "They Were Wrong: Push Through the Pain" and meta["title_ja"] == "見返してやれ"
    assert meta["quality"]["title_attempts"] == 2 and not meta["quality"]["title_fallback"]
    # the LLM never fixes it -> cleaned original title
    llm = FakeLLM(main_answer(title_en="Never Give Up: Believe in Yourself", title_ja="x"))
    meta = run(llm, force=True)
    assert meta["quality"]["title_fallback"] and meta["title_en"] == "They Were Wrong"
    assert pm.title_ja_problem(meta["title_ja"]) is None


def test_publish_kit_preview_out_dir_does_not_claim_photos(kit, tmp_path):
    vids, video, run, made = kit
    out = tmp_path / "preview"
    meta = run(FakeLLM(main_answer()), out_dir=out)
    assert (out / f"{video.stem}.publish.json").exists() and (out / f"{video.stem}.thumb.jpg").exists()
    assert not (vids / f"{video.stem}.publish.json").exists()
    assert made[-1] == (meta["thumbnail_text"], False)


def test_publish_kit_without_transcript(kit, monkeypatch):
    vids, video, run, made = kit
    monkeypatch.setattr(pm, "transcript", lambda mp3: "")
    assert run(FakeLLM(main_answer())) is None


def test_process_still_works(kit, monkeypatch, tmp_path):
    vids, video, run, made = kit
    monkeypatch.setattr(pm, "_post", FakeLLM(main_answer()))
    assert pm.process(video, tmp_path, "test", log=lambda m: None) is True


def test_publish_kit_new_english_title_brings_its_japanese_title(kit):
    vids, video, run, made = kit
    llm = FakeLLM(main_answer(title_en="Never Give Up: Believe in Yourself", title_ja="嵐を乗り越えて"),
                  titles=[{"title_en": "They Were Wrong: Push Through the Pain", "title_ja": "見返してやれ"}])
    meta = run(llm)
    assert meta["title_en"] == "They Were Wrong: Push Through the Pain" and meta["title_ja"] == "見返してやれ"
