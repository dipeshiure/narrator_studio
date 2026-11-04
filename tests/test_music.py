"""Free BGM: catalogue filtering, credits, picking, mixing under the voice."""
import json, re, shutil, subprocess

import make_video as mv
import music
from conftest import add_clips, ffmpeg


def piece(title, feel, length="00:03:00"):
    return {"title": title, "filename": title + ".mp3", "feel": feel, "length": length, "instruments": "Piano"}


def test_score_keeps_motivational_and_drops_silly():
    assert music.score(piece("Heroic", "Epic, Uplifting")) > 0
    assert music.score(piece("Merry Christmas Rap", "Uplifting, Bright")) == 0     # title filter
    assert music.score(piece("Funny Walk", "Humorous, Bouncy")) == 0
    assert music.score(piece("Short", "Epic", "00:00:40")) == 0                     # too short
    assert music.score(piece("Calm", "Relaxed, Calming")) == 0


def test_credit_block_exact_text():
    assert music.credit_block(["Undaunted"]) == ('"Undaunted"\nKevin MacLeod (incompetech.com)\n'
                                               "Licensed under Creative Commons: By Attribution 4.0\n"
                                               "http://creativecommons.org/licenses/by/4.0/")


def test_add_credit_idempotent_and_replaces_old_track():
    meta = {"description_en_full": "Speech text.\n\n#Motivation", "description_ja_full": "本文"}
    m1 = {"title": "Undaunted", "artist": "Kevin MacLeod", "source": "incompetech.com", "license": "CC BY 4.0", "file": "u.mp3"}
    assert music.add_credit(meta, m1) and not music.add_credit(meta, m1)
    assert meta["description_en_full"].count("Kevin MacLeod") == 1 and "Music:\n\"Undaunted\"" in meta["description_en_full"]
    assert "音楽:\n\"Undaunted\"" in meta["description_ja_full"]
    music.add_credit(meta, {**m1, "title": "Reign"})
    assert "Undaunted" not in meta["description_en_full"] and '"Reign"' in meta["description_en_full"]
    assert meta["music"]["title"] == "Reign"


def test_pick_least_used_respects_enabled_and_mood(sandbox):
    music.DIR.mkdir()
    for f in ("a.mp3", "b.mp3", "c.mp3"):
        (music.DIR / f).write_bytes(b"x")
    music.save_tracks([
        {"title": "A", "file": "a.mp3", "feel": ["epic"], "length": 200, "enabled": True, "uses": 3},
        {"title": "B", "file": "b.mp3", "feel": ["epic"], "length": 200, "enabled": True, "uses": 0},
        {"title": "C", "file": "c.mp3", "feel": ["uplifting"], "length": 200, "enabled": False, "uses": 0}])
    t = music.pick(100, "epic")
    assert t["title"] == "B" and "Kevin MacLeod" in t["credit"]
    assert [x["uses"] for x in music.load_tracks()] == [3, 1, 0]
    assert music.pick(100, "uplifting")["title"] in ("A", "B")     # C disabled -> fallback to any


def test_render_with_bgm_ducks_music_and_credits(sandbox, assets):
    music.DIR.mkdir()
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=30", "-ac", "2", str(music.DIR / "bed.mp3"))
    music.save_tracks([{"title": "Test Bed", "file": "bed.mp3", "feel": ["epic"], "length": 30, "enabled": True,
                        "uses": 0, "artist": "Kevin MacLeod", "source": "incompetech.com", "license": "CC BY 4.0"}])
    add_clips(mv.LIB, assets["clip"], 30)
    audio = sandbox["root"] / "speech.mp3"; shutil.copy(assets["speech"], audio)
    s = {**mv.load_settings(), "offline": True, "remove_music": False, "subs": False, "trim_ads": False,
         "bgm": True, "auto_publish": False, "qa": False}
    log = []
    out = mv.make(audio, sandbox["OUT"], s, log=log.append)
    assert out and any("BGM: Test Bed" in l for l in log)
    assert abs(mv.duration(out) - mv.duration(audio)) < 0.5
    rj = mv.read_json(mv.work_dir(audio) / "render.json", {})
    assert rj["music"]["title"] == "Test Bed"
    # a kit gets the CC BY credit automatically
    mv.write_json(out.parent / (out.stem + ".publish.json"), {"video": out.name, "title_en": "x", "title_ja": "x",
                  "description_en_full": "d", "description_ja_full": "d", "tags": [], "hashtags": [],
                  "thumbnail": "x.jpg", "thumbnail_text": "GO"})
    mv.apply_music_credit(out, audio)
    meta = mv.read_json(out.parent / (out.stem + ".publish.json"), {})
    assert "Kevin MacLeod (incompetech.com)" in meta["description_en_full"]


def test_no_tracks_means_voice_only(sandbox, assets):
    add_clips(mv.LIB, assets["clip"], 30)
    audio = sandbox["root"] / "plain.mp3"; shutil.copy(assets["short"], audio)
    s = {**mv.load_settings(), "offline": True, "remove_music": False, "subs": False, "trim_ads": False,
         "bgm": True, "auto_publish": False, "qa": False}
    log = []
    assert mv.make(audio, sandbox["OUT"], s, log=log.append)
    assert any("声だけで作ります" in l for l in log)
