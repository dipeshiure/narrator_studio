"""Audio prep: sentence split, ad detection, cut ranges, trimming, gap filling."""
import json, shutil, subprocess

import pytest

import make_video as mv
import prep
from conftest import ffmpeg


def W(text, start=0.0, step=0.4, gap_after=None):
    """Fake Whisper words for a sentence."""
    out, t = [], start
    for w in text.split():
        out.append({"w": w, "s": round(t, 2), "e": round(t + step - 0.05, 2)})
        t += step
    return out


def sent(s, e, cut, text="x"):
    return {"s": s, "e": e, "cut": cut, "text": text}


# ---------------------------------------------------------------- sentences
def test_sentences_split_on_punctuation_pause_and_length():
    words = W("one two three.") + W("four five", start=1.2) + W("six seven", start=4.0)  # 2 s pause
    ss = prep.sentences_from_words(words)
    assert [x["text"] for x in ss] == ["one two three.", "four five", "six seven"]
    long = prep.sentences_from_words(W(" ".join(["w"] * 60)), max_words=28)
    assert [len(x["text"].split()) for x in long] == [28, 28, 4]


# ---------------------------------------------------------------- classification
def test_llm_cut_needs_quote_and_cue_word(monkeypatch):
    """A small LLM sometimes flags real speech; such guesses must be rejected."""
    reply = {"cut": [
        {"i": 0, "reason": "social", "quote": "Your struggle is your gift"},   # speech, no cue word
        {"i": 1, "reason": "app", "quote": "download our app"},               # real ad
        {"i": 2, "reason": "app", "quote": "something not in the sentence"},  # quote not found
        {"i": 3, "reason": "outro"}]}                                         # no quote at all

    class R:
        def read(self): return json.dumps({"message": {"content": json.dumps(reply)}}).encode()
    monkeypatch.setattr(prep.urllib.request, "urlopen", lambda req, timeout=0: R())
    ss = [{"s": 0, "e": 1, "text": "Your struggle is your gift."},
          {"s": 1, "e": 2, "text": "Before we start, download our app today."},
          {"s": 2, "e": 3, "text": "Check the link below."},
          {"s": 3, "e": 4, "text": "See you in the next one, guys."}]
    assert prep.ask_llm(ss, "m") == {1: "アプリの宣伝"}


@pytest.mark.parametrize("text", ["What's up everybody, welcome back to Motivation Daily.",
                                  "Hey guys, today's video is huge.", "Thank you for watching.", "Thanks so much for watching!",
                                  "Don't forget to subscribe.", "Use promo code GRIND."])
def test_keyword_rules_catch_common_outros(text):
    assert prep.AD_RE.search(text)


@pytest.mark.parametrize("text", ["Hey, you!", "Today is the day.", "Welcome to the rest of your life.",
                                  "Your struggle is your gift.", "Do you want to be a legend?",
                                  "Thank you.", "Watch what happens when you never quit."])
def test_keyword_rules_leave_speech_alone(text):
    assert not prep.AD_RE.search(text)


def test_keyword_rules_work_without_llm(monkeypatch):
    def boom(*a, **k):
        raise ConnectionRefusedError
    monkeypatch.setattr(prep, "ask_llm", boom)
    ss = [{"s": 0, "e": 1, "text": "Smash that like button and subscribe."},
          {"s": 1, "e": 2, "text": "You have to keep going no matter what."},
          {"s": 2, "e": 3, "text": "Use promo code GRIND for a discount."}]
    logs = []
    prep.classify(ss, "m", log=logs.append)
    assert [x["cut"] for x in ss] == [True, False, True]
    assert ss[0]["auto"] and "キーワード" in ss[0]["reason"]
    assert any("AI 判定を使えませんでした" in l for l in logs)


def test_llm_result_is_validated(monkeypatch):
    replies = iter([json.dumps({"cut": [{"i": 0, "reason": "promo_code", "quote": "use code win"},
                                        {"i": 1, "reason": "??", "quote": "visit our website"},
                                        {"i": 99}, {"i": "x"}, {"reason": "no i"}]})])

    class R:
        def __init__(self, body): self.body = body
        def read(self): return self.body

    monkeypatch.setattr(prep.urllib.request, "urlopen",
                        lambda req, timeout=0: R(json.dumps({"message": {"content": next(replies)}}).encode()))
    ss = [{"s": 0, "e": 1, "text": "Use code WIN at checkout."}, {"s": 1, "e": 2, "text": "Visit our website."}]
    # categories become fixed Japanese labels; unknown -> generic; bad items ignored
    assert prep.ask_llm(ss, "m") == {0: "プロモコード", 1: "宣伝・告知"}


def test_mixed_sentence_is_split_and_only_the_ad_part_cut(monkeypatch):
    words = W("If you seek the opportunities you wish to find, you thanks for watching.")
    ss = prep.sentences_from_words(words)
    assert len(ss) == 1

    def fake_llm(items, model, chunk=50):
        return {i: "締めの挨拶・告知" for i, x in enumerate(items) if "watching" in x["text"]}
    monkeypatch.setattr(prep, "ask_llm", fake_llm)
    prep.classify(ss, "m", log=lambda m: None, words=words)
    assert [(x["text"], x["cut"]) for x in ss] == [
        ("If you seek the opportunities you wish to find,", False),
        ("you thanks for watching.", True)]


def test_short_fragments_next_to_ads_are_absorbed():
    ss = [sent(0, 0.6, False, "Hey guys,"), sent(1, 1.8, False, "before we start,"),
          sent(2, 4, True, "download the app."), sent(5, 9, False, "You were born to win this fight today."),
          sent(10, 10.5, False, "Okay."), sent(11, 13, True, "subscribe")]
    prep.absorb_islands(ss)
    # leading "Hey guys, before we start," goes; "Okay." stays because it is attached
    # to real speech (a short closing line right before an outro must not be lost)
    assert [x["cut"] for x in ss] == [True, True, True, False, False, True]
    alone = [sent(i, i + 0.8, False, "Go.") for i in range(4)]       # no ads at all
    prep.absorb_islands(alone)
    assert not any(x["cut"] for x in alone)


# ---------------------------------------------------------------- ranges
def test_keep_ranges_nothing_cut():
    assert prep.keep_ranges([sent(0, 1, False)], 10.0) == [(0.0, 10.0)]
    assert prep.keep_ranges([], 10.0) == [(0.0, 10.0)]


def test_keep_ranges_intro_middle_outro():
    ss = [sent(0, 5, True), sent(6, 10, False), sent(11, 14, True), sent(15, 20, False), sent(21, 25, True)]
    r = prep.keep_ranges(ss, 30.0)
    assert r == [(5.85, 10.3), (14.85, 20.3)]                  # pauses around ads go too


def test_keep_ranges_padding_never_reaches_into_the_ad():
    # speech ends 67.2, ad starts 67.3 (almost no pause): the cut must start at 67.3, not 67.5
    ss = [sent(60, 67.2, False), sent(67.3, 68.3, True), sent(68.35, 70, False)]
    assert prep.keep_ranges(ss, 70.0) == [(0.0, 67.3), (68.3, 70.0)]


def test_keep_ranges_everything_cut():
    assert prep.keep_ranges([sent(0, 5, True), sent(6, 9, True)], 10.0) == []


def test_remap_words_onto_trimmed_timeline():
    words = [{"w": "ad", "s": 0.5, "e": 1.0}, {"w": "go", "s": 6.0, "e": 6.5}, {"w": "now", "s": 15.0, "e": 15.4}]
    out = prep.remap_words(words, [(5.0, 10.0), (14.0, 16.0)])
    assert [w["w"] for w in out] == ["go", "now"]
    assert out[0]["s"] == pytest.approx(1.0) and out[1]["s"] == pytest.approx(6.0)


def test_trim_audio_length(tmp_path, assets):
    dest = tmp_path / "t.wav"
    prep.trim_audio(assets["speech"], [(0.0, 2.0), (5.0, 8.5)], dest)
    assert mv.duration(dest) == pytest.approx(5.5, abs=0.1)


# ---------------------------------------------------------------- whole flow (real Whisper + Demucs + LLM)
@pytest.fixture(scope="module")
def ad_audio(tmp_path_factory, assets):
    """'App ad' + real speech + 'subscribe' outro, voices made with macOS say."""
    d = tmp_path_factory.mktemp("ad")
    subprocess.run(["say", "-o", str(d / "ad.aiff"), "Before we start, download the Calm app. "
                    "The link is in the description. Use promo code MOTIVATION for twenty percent off."], check=True)
    subprocess.run(["say", "-o", str(d / "outro.aiff"),
                    "Thanks for watching. Don't forget to subscribe and hit the notification bell."], check=True)
    out = d / "with ad.mp3"
    ffmpeg("-i", str(d / "ad.aiff"), "-i", str(assets["speech"]), "-i", str(d / "outro.aiff"), "-filter_complex",
           "[0:a]aresample=44100,aformat=channel_layouts=stereo[a];[1:a]aresample=44100,aformat=channel_layouts=stereo[b];"
           "[2:a]aresample=44100,aformat=channel_layouts=stereo[c];[a][b][c]concat=n=3:v=0:a=1", str(out))
    return out


def test_ads_found_and_cut(sandbox, ad_audio):
    s = {**mv.load_settings(), "whisper_model": "base", "trim_ads": True, "remove_music": True}
    work = mv.work_dir(ad_audio); work.mkdir(parents=True)
    a = prep.analyze(ad_audio, work, s, log=lambda m: None)
    cut_text = " ".join(x["text"] for x in a["sentences"] if x["cut"]).lower()
    kept_text = " ".join(x["text"] for x in a["sentences"] if not x["cut"]).lower()
    assert "promo" in cut_text or "app" in cut_text               # intro ad
    assert "subscribe" in cut_text                                # outro (Whisper gap filled)
    assert "opportunity" in kept_text and "subscribe" not in kept_text
    assert a["music_db"] is not None
    audio, words = prep.prepare(ad_audio, work, s, log=lambda m: None)
    assert audio.name == "final.wav"
    speech_len = mv.duration(ad_audio) - 12      # roughly the 12 s speech sample survives
    assert mv.duration(audio) < mv.duration(ad_audio) - 8
    assert words and words[0]["s"] < 2.0                          # timeline starts at the speech


def test_trim_off_keeps_everything(sandbox, ad_audio):
    s = {**mv.load_settings(), "whisper_model": "base", "remove_music": True, "trim_ads": False, "tighten_pauses": False}
    work = mv.work_dir(ad_audio); work.mkdir(parents=True)
    audio, _ = prep.prepare(ad_audio, work, s, log=lambda m: None)
    assert audio.name == "voice.wav"
    assert not any(x["cut"] for x in mv.read_json(work / "analysis.json", {})["sentences"])


# ---------------------------------------------------------------- voice-only inputs / cache reuse
def fake_demucs(db, calls):
    def isolate(mp3, work, log):
        calls.append(mp3)
        (work / "voice.wav").write_bytes(b"VOICE")
        mv.write_json(work / "music.json", {"db": db})
        return work / "voice.wav"
    return isolate


def test_voice_only_input_keeps_original_and_skips_demucs_next_time(sandbox, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mv, "isolate_voice", fake_demucs(-60.0, calls))
    mp3 = tmp_path / "voice only.mp3"; mp3.write_bytes(b"x")
    work = mv.work_dir(mp3); work.mkdir(parents=True)
    s = {**mv.load_settings(), "remove_music": True}
    assert prep.source_audio(mp3, work, s, log=lambda m: None) == mp3
    assert not (work / "voice.wav").exists()                     # original is used, no separation artefacts
    assert mv.read_json(work / "music.json", {}) == {"db": -60.0, "voice_only": True}
    assert prep.source_audio(mp3, work, s, log=lambda m: None) == mp3
    assert len(calls) == 1                                       # Demucs ran once only


def test_music_input_uses_separated_voice(sandbox, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mv, "isolate_voice", fake_demucs(-18.0, calls))
    mp3 = tmp_path / "music.mp3"; mp3.write_bytes(b"x")
    work = mv.work_dir(mp3); work.mkdir(parents=True)
    s = {**mv.load_settings(), "remove_music": True}
    assert prep.source_audio(mp3, work, s, log=lambda m: None) == work / "voice.wav"
    assert prep.source_audio(mp3, work, s, log=lambda m: None) == work / "voice.wav"
    assert len(calls) == 1                                       # cached voice.wav reused


def test_voice_only_analysis_transcribes_the_original(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "isolate_voice", fake_demucs(-60.0, []))
    seen = []
    monkeypatch.setattr(mv, "transcribe", lambda src, cache, model: seen.append((src, cache.name)) or W("never give up."))
    monkeypatch.setattr(mv, "duration", lambda p: 2.0)
    mp3 = tmp_path / "v.mp3"; mp3.write_bytes(b"x")
    work = mv.work_dir(mp3)
    a = prep.analyze(mp3, work, {**mv.load_settings(), "remove_music": True, "trim_ads": False}, log=lambda m: None)
    assert a["source"] == "original" and seen == [(mp3, "words_original.json")]
    assert a["music_db"] == -60.0


def test_same_audio_content_reuses_cached_transcript(sandbox, monkeypatch, tmp_path):
    a1 = tmp_path / "first name.mp3"; a1.write_bytes(b"SAME CONTENT")
    a2 = tmp_path / "renamed.mp3"; a2.write_bytes(b"SAME CONTENT")
    w1, w2 = mv.work_dir(a1), mv.work_dir(a2)
    w1.mkdir(parents=True); w2.mkdir(parents=True)
    assert prep.reuse_cache(a1, w1) is False                     # first sighting: just indexed
    mv.write_json(w1 / "words_voice.json", W("hello there"))
    mv.write_json(w1 / "music.json", {"db": -20.0})
    (w1 / "voice.wav").write_bytes(b"V")
    msgs = []
    assert prep.reuse_cache(a2, w2, log=msgs.append) is True
    assert mv.read_json(w2 / "words_voice.json", None) == W("hello there") and (w2 / "voice.wav").exists()
    assert msgs
    # different content: nothing copied
    a3 = tmp_path / "other.mp3"; a3.write_bytes(b"OTHER")
    w3 = mv.work_dir(a3); w3.mkdir(parents=True)
    assert prep.reuse_cache(a3, w3) is False and not list(w3.iterdir())


def test_cut_sentence_words_never_reach_subtitles_even_with_collapsed_timestamps():
    """Whisper put a hallucinated outro onto the next real word's timestamp (52_Lone Wolf)."""
    words = [{"w": "Thanks", "s": 118.0, "e": 118.5}, {"w": "watching.", "s": 118.5, "e": 119.0},
             {"w": "see", "s": 123.0, "e": 123.0}, {"w": "video.", "s": 123.0, "e": 123.0},
             {"w": "You", "s": 123.0, "e": 123.2}, {"w": "have", "s": 123.2, "e": 123.5}]
    ss = [{"s": 118.0, "e": 119.0, "cut": True, "text": "Thanks watching.", "wi": [0, 1]},
          {"s": 123.0, "e": 123.0, "cut": True, "text": "see video.", "wi": [2, 3]},
          {"s": 123.0, "e": 123.5, "cut": False, "text": "You have", "wi": [4, 5]}]
    ranges = prep.keep_ranges(ss, 130.0)
    out = prep.remap_words(words, ranges, prep.cut_word_indices(ss))
    assert [w["w"] for w in out] == ["You", "have"]


def test_tighten_removes_long_silences_keeps_natural_pauses():
    words = [{"w": "a", "s": 2.0, "e": 2.5}, {"w": "b", "s": 2.8, "e": 3.2},       # 0.3 s pause: kept
             {"w": "c", "s": 10.0, "e": 10.5},                                    # 6.8 s silence: shortened
             {"w": "d", "s": 11.5, "e": 12.0}]                                    # 1.0 s pause: kept
    r = prep.tighten_ranges([(0.0, 20.0)], words, 20.0, max_pause=1.2, pad=0.35, head=0.25, tail=0.6)
    assert r == [(1.75, 3.55), (9.65, 12.6)]
    kept = sum(b - a for a, b in r)
    assert kept < 5.0                       # 20 s -> under 5 s, nothing silent longer than 0.7 s inside
    out = prep.remap_words(words, r)
    assert [w["w"] for w in out] == ["a", "b", "c", "d"]
    assert out[2]["s"] - out[1]["e"] == pytest.approx(0.7, abs=0.01)


def test_tighten_respects_ad_cuts_and_dropped_words():
    words = [{"w": "ad", "s": 0.5, "e": 1.0}, {"w": "go", "s": 5.0, "e": 5.5}]
    r = prep.tighten_ranges([(4.0, 8.0)], words, 8.0, drop={0})
    assert r == [(4.75, 6.1)]


def test_trim_audio_removes_parts_without_silence(tmp_path, assets):
    dest = tmp_path / "t.wav"
    prep.trim_audio(assets["speech"], [(0.0, 2.0), (5.0, 8.5), (9.0, 9.5)], dest)
    assert mv.duration(dest) == pytest.approx(6.0, abs=0.08)


def test_tighten_uses_level_silence_when_words_are_missing_or_hallucinated():
    # no words at all (transcript missing): level silence alone is enough
    r = prep.tighten_ranges([(0.0, 30.0)], [], 30.0, quiet=[(10.0, 25.0)])
    assert r == [(0.0, 10.35), (24.65, 30.0)]
    # a hallucinated word inside a silent stretch does not keep the silence
    words = [{"w": "go", "s": 1.0, "e": 1.5}, {"w": "watching", "s": 17.0, "e": 17.3}, {"w": "now", "s": 26.0, "e": 26.4}]
    r = prep.tighten_ranges([(0.0, 30.0)], words, 30.0, quiet=[(5.0, 24.0)])
    assert all(not (a < 17.1 < b) for a, b in r)
    assert [w["w"] for w in prep.remap_words(words, r)] == ["go", "now"]


def test_silences_detects_quiet_stretch(tmp_path, assets):
    f = tmp_path / "gap.wav"
    ffmpeg("-i", str(assets["short"]), "-f", "lavfi", "-t", "3", "-i", "anullsrc=r=44100:cl=stereo",
           "-i", str(assets["short"]), "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1", str(f))
    q = prep.silences(f, 1.2)
    assert any(2.5 < a < 3.5 and 5.5 < b < 6.5 for a, b in q)
