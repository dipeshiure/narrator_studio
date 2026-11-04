"""Ready-to-post folders: every finished video appears, kit or not; kit later renames it."""
import os

import make_posting
import make_video as mv


def kit(vdir, stem, title):
    mv.write_json(vdir / f"{stem}.publish.json", {
        "video": f"{stem}.mp4", "title_en": title, "title_ja": "タイトル", "description_en_full": "EN",
        "description_ja_full": "JA", "tags": ["#Go"], "hashtags": ["#Go"], "thumbnail": f"{stem}.thumb.jpg",
        "speech_source": "Fearless Motivation"})


def test_new_video_shows_up_before_and_after_its_kit(tmp_path):
    vdir, dest = tmp_path / "videos", tmp_path / "posting"
    vdir.mkdir()
    (vdir / "69_New Speech.mp4").write_bytes(b"video")
    make_posting.main(vdir, dest, log=lambda m: None)
    first = dest / "69_New Speech"
    assert (first / "video.mp4").exists() and os.path.samefile(first / "video.mp4", vdir / "69_New Speech.mp4")
    assert "作成中" in (first / "post.txt").read_text()
    # the kit arrives: folder renamed to the title, placeholder replaced, old folder gone
    (vdir / "69_New Speech.thumb.jpg").write_bytes(b"jpg")
    kit(vdir, "69_New Speech", "Never Give Up")
    make_posting.main(vdir, dest, log=lambda m: None)
    final = dest / "69_Never Give Up"
    assert not first.exists() and (final / "thumbnail.jpg").exists()
    assert "Never Give Up" in (final / "post.txt").read_text()
    assert "69_Never Give Up" in (dest / "_index.md").read_text()


def test_temp_render_files_are_ignored(tmp_path):
    vdir, dest = tmp_path / "videos", tmp_path / "p"
    vdir.mkdir()
    (vdir / "01_A.remix.mp4").write_bytes(b"x")
    (vdir / "01_A.tight.mp4").write_bytes(b"x")
    assert make_posting.main(vdir, dest, log=lambda m: None) == 0
