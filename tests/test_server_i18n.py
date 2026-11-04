import server_i18n as s


def test_templates_translate_whole_lines_and_nested_parts():
    assert s.en("エラー: 素材がありません") == "Error: No footage"
    assert s.en("  シーン 2/9: イラスト 3 枚") == "  Scene 2/9: 3 illustrations"
    assert s.en("[3/10] lion: +4 本") == "[3/10] lion: +4 clips"
    assert s.en("動画 1.00 秒 / 音声 1.01 秒(差 0.01 秒)") == "video 1.00s / audio 1.01s (diff 0.01s)"


def test_unknown_text_and_content_fields_pass_through():
    assert s.en("成功への鍵：自制心") == "成功への鍵：自制心"
    out = s.localize({"title_ja": "長さ", "speech_source": "要確認", "checks": [{"label": "長さ", "detail": "なし"}]})
    assert out == {"title_ja": "長さ", "speech_source": "要確認", "checks": [{"label": "Length", "detail": "None"}]}


def test_every_template_has_matching_placeholders():
    import re
    for ja, en in s.T.items():
        assert set(re.findall(r"\{(\w+)\}", ja)) == set(re.findall(r"\{(\w+)\}", en)), ja


def test_server_translates_json_only_for_english_cookie():
    import server
    c = server.app.test_client()
    ja = c.get("/api/story/options").get_json()
    c.set_cookie("lang", "en")
    en = c.get("/api/story/options").get_json()
    assert any("男性" in v["label"] for v in ja["dia_voices"])
    assert not any("男性" in v["label"] for v in en["dia_voices"])


def test_translation_files_match_english_keys_and_placeholders():
    import subprocess, sys
    r = subprocess.run([sys.executable, "tools/i18n_check.py"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
