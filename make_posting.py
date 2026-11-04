"""Collect finished videos into ready-to-post folders (hard links: no extra disk space).

  venv/bin/python make_posting.py output/videos output/posting

<dest>/<NN>_<title>/   (or <title>/ for files without a number prefix)
    video.mp4           the video
    thumbnail.jpg       thumbnail
    post.txt            titles, descriptions, tags, hashtags (copy & paste)
Only videos that already have their publishing text are included. Incremental:
unchanged folders are left alone, so it is cheap to run after every render
(make_video.after_render does). <dest>/_index.json maps video stem -> folder.
"""
import os, re, shutil, sys
from pathlib import Path

import make_video as mv

OWN_FILES = ("video.mp4", "thumbnail.jpg", "post.txt")
OLD_FILES = ("動画.mp4", "サムネイル.jpg", "投稿テキスト.txt")   # names used before 2026-11-04


def safe(name, limit=60):
    name = re.sub(r'[\\/:*?"<>|]+', " ", name)
    return re.sub(r"\s+", " ", name).strip()[:limit]


def link(src, dst):
    """Hard-link src to dst (copy across disks). Returns True if dst changed."""
    if dst.exists():
        if os.path.samefile(src, dst):
            return False
        dst.unlink()
    try:
        os.link(src, dst)                      # same disk: no extra space
    except OSError:
        shutil.copy2(src, dst)
    return True


def remove_folder(folder):
    """Remove a posting folder we made (only our own files; keeps anything else)."""
    for name in OWN_FILES + OLD_FILES:
        (folder / name).unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass


def post_text(m, video):
    return f"""【タイトル(英語)】
{m['title_en']}

【タイトル(日本語)】
{m['title_ja']}

【説明文(英語)】
{m['description_en_full']}

【説明文(日本語)】
{m['description_ja_full']}

【タグ(カンマ区切り)】
{', '.join(m['tags'])}

【ハッシュタグ】
{' '.join(m['hashtags'])}

【元の音声】
{video.stem}
【音声の出典】
{m.get('speech_source') or '要確認(公開前に話し手・チャンネル名を記入してください)'}
"""


def main(vdir, dest, log=print):
    """Build/refresh every posting folder. Returns the number of folders."""
    vdir, dest = Path(vdir), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    index = mv.read_json(dest / "_index.json", {})
    rows, changed = [], 0
    # every finished video gets a folder right away; the publishing kit (title/description)
    # may come later - until then the folder uses the file name and a placeholder text
    entries = {}
    for meta_path in vdir.glob("*.publish.json"):
        m = mv.read_json(meta_path, None)
        if isinstance(m, dict) and m.get("video") and m.get("title_en") and (vdir / m["video"]).exists():
            entries[Path(m["video"]).stem] = m
    for video in vdir.glob("*.mp4"):
        if video.stem not in entries and not video.name.endswith((".remix.mp4", ".tight.mp4")):
            entries[video.stem] = None
    for stem in sorted(entries):
        m = entries[stem]
        video = vdir / ((m or {}).get("video") or stem + ".mp4")
        num_m = re.match(r"(\d+)_", video.stem)
        num = num_m[1] if num_m else ""
        name = safe(m["title_en"]) if m else safe(re.sub(r"^\d+_", "", video.stem))
        folder = dest / (f"{num}_{name}" if num else name or safe(video.stem))
        # the title may have changed: drop the older folder of the same video
        old = index.get(video.stem)
        if old and old != folder.name and (dest / old).is_dir():
            remove_folder(dest / old)
        if num:   # folders made before _index.json existed (same number, other title)
            for o in dest.glob(f"{num}_*"):
                if o != folder and o.is_dir() and o.name not in index.values():
                    remove_folder(o)
        index[video.stem] = folder.name
        folder.mkdir(exist_ok=True)
        dirty = link(video, folder / "video.mp4")
        thumb = vdir / ((m or {}).get("thumbnail") or video.stem + ".thumb.jpg")
        if thumb.exists():
            dirty |= link(thumb, folder / "thumbnail.jpg")
        text = post_text(m, video) if m else ("公開キット(タイトル・説明文・タグ)はまだ作成中です。\n"
                                              "できると自動でこのファイルに入ります。\n"
                                              "急ぐときはアプリの「投稿」ページで「公開キットを作り直す」を押してください。\n\n"
                                              f"【元の音声】\n{video.stem}\n")
        txt = folder / "post.txt"
        if not txt.exists() or txt.read_text() != text:
            txt.write_text(text); dirty = True
        changed += dirty
        if m:
            rows.append(f"| {num or '-'} | {m['title_en']} | {m.get('title_ja', '')} | "
                        f"{m.get('speech_source') or '要確認'} | {folder.name} |")
        else:
            rows.append(f"| {num or '-'} | (公開キット作成中) | | | {folder.name} |")
    mv.write_json(dest / "_index.json", index)
    (dest / "_index.md").write_text("# 投稿用動画\n\n各フォルダに video.mp4 / thumbnail.jpg / post.txt があります。\n"
                                  "公開前に、音声の権利元の利用条件を確認してください。\n\n"
                                  "| No | タイトル (EN) | タイトル (JA) | 音声の出典 | フォルダ |\n|---|---|---|---|---|\n"
                                  + "\n".join(rows) + "\n")
    log(f"{len(rows)} folders in {dest} ({changed} updated)")
    return len(rows)


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser())
