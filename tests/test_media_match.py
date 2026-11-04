"""Own-folder footage matching (media_match.py): naming signal, LLM output parsing, API guards."""
from pathlib import Path

import media_match as mm
from test_api import client  # noqa: F401  (fixture)


def test_name_text_drops_camera_numbers(tmp_path):
    p = tmp_path / "rain_window" / "IMG_2041 sad man.mp4"
    assert mm.name_text(p, tmp_path) == "rain window sad man"
    assert mm.name_text(tmp_path / "DSC0001.jpg", tmp_path) == ""


def test_flat_handles_badly_nested_llm_json():
    assert mm._flat([["a", ["b"]], {"x": "c"}, " "]) == ["a", "b", "c"]


def test_list_media_filters_types(tmp_path):
    for n in ("a.mp4", "b.JPG", "c.txt", ".hidden.mp4", "sub/d.mov"):
        (tmp_path / n).parent.mkdir(parents=True, exist_ok=True); (tmp_path / n).write_bytes(b"x")
    assert [p.name for p in mm.list_media(tmp_path)] == ["a.mp4", "b.JPG", "d.mov"]


def test_media_tag_is_accepted(tmp_path):
    import story
    p = tmp_path / "s.txt"
    p.write_text("T\n\nThink ahead. [[media:chess_01]]\n")
    assert story.load_script(p)["scenes"][0]["media"] == "chess_01"


def test_match_preview_guards(client):
    r = client.post("/api/story/match-preview", json={"text": "x", "options": {"media_folder": "/nope"}})
    assert r.status_code == 400
    assert client.get("/api/story/thumb/..%2F..%2Fserver.py").status_code == 404
