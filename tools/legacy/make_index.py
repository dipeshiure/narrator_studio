"""Write <videos dir>/_一覧.md: every audio, its video, titles, thumbnail and status.

  venv/bin/python tools/legacy/make_index.py output/videos ~/Desktop/soundcloud_motivational/voice_only
"""
import sys
import sys as _sys
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))  # app modules live in the tool's root
from pathlib import Path

import make_video as mv

vdir, adir = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
fmt = lambda t: f"{int(t // 60)}:{int(t % 60):02d}"
rows, done, total_len = [], 0, 0.0
for mp3 in sorted(adir.glob("*.mp3")):
    v = vdir / (mp3.stem + ".mp4")
    meta = mv.read_json(vdir / (mp3.stem + ".publish.json"), None)
    if v.exists():
        done += 1; d = mv.duration(v); total_len += d
        status = "完成" + ("" if meta else "(説明文は作成中)")
    else:
        d, status = mv.duration(mp3), "作成中"
    title = meta["title_en"] if meta else ""
    ja = meta["title_ja"] if meta else ""
    thumb = f"[サムネ]({meta['thumbnail'].replace(' ', '%20')})" if meta else ""
    src = (meta or {}).get("speech_source") or "要確認"
    rows.append(f"| {mp3.stem} | {fmt(d)} | {status} | {title} | {ja} | {src} | {thumb} |")
md = [f"# 動画一覧(声だけ音声 × Pexels 素材)", "",
      f"完成 {done} / {len(rows)} 本 ・ 完成分の合計 {fmt(total_len)}", "",
      "各動画の公開用テキスト(タイトル・説明文・タグ)は `<名前>.publish.md`、サムネイルは `<名前>.thumb.jpg` です。", "",
      "| 音声 | 長さ | 状態 | タイトル (EN) | タイトル (JA) | 音声の出典 | サムネイル |",
      "|---|---|---|---|---|---|---|"] + rows
(vdir / "_一覧.md").write_text("\n".join(md) + "\n")
print(f"done {done}/{len(rows)}")
