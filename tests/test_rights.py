"""rights.py: risk levels, records, Shazam result parsing (no network)."""
import make_video as mv
import rights


def test_risk_levels():
    assert rights.risk({"shazam": {"status": "match"}, "youtube": {"status": "unchecked"}}) == "high"
    assert rights.risk({"shazam": {"status": "none"}, "youtube": {"status": "unchecked"}}) == "low"
    assert rights.risk({"shazam": {"status": "match"}, "youtube": {"status": "ok"}}) == "ok"
    assert rights.risk({"shazam": {"status": "none"}, "youtube": {"status": "claim"}}) == "claimed"
    assert rights.risk({"shazam": {"status": "unchecked"}, "youtube": {"status": "unchecked"}}) == "unknown"


def test_track_info_and_detected_source():
    out = {"track": {"title": "The Lion Attitude", "subtitle": "Fearless Motivation", "url": "u",
                     "sections": [{"metadata": [{"title": "Label", "text": "Fearless Motivation"}]}]}}
    m = rights._track_info(out, 12.3)
    assert m == {"at": 12.3, "title": "The Lion Attitude", "artist": "Fearless Motivation",
                 "label": "Fearless Motivation", "url": "u"}
    assert rights._track_info({"matches": []}, 0) is None
    r = {"shazam": {"matches": [m, m, {**m, "artist": "Cover Boy"}]}}
    assert rights.detected_source(r) == "Fearless Motivation"


def test_records_roundtrip(tmp_path):
    v = tmp_path / "x.mp4"; v.write_bytes(b"v")
    r = rights.load(v)
    assert r["shazam"]["status"] == "unchecked" and r["youtube"]["status"] == "unchecked"
    rights.set_youtube(v, "deleted", "removed after claim")
    assert mv.read_json(tmp_path / "x.rights.json", {})["youtube"]["status"] == "deleted"
    try:
        rights.set_youtube(v, "nope")
        assert False
    except ValueError:
        pass
