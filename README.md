# Narrator Studio

台本を入れるだけで、この Mac の中で声（CosyVoice 2）・白黒の劇画風イラスト（FLUX.2 klein）・動く字幕（kinetic-captions）・BGM まで入った語り動画を作るツールです。録音した音声から作ることもできます。

## 起動

`start.command` をダブルクリック → ブラウザで http://localhost:8510 が開きます。

## フォルダ

| フォルダ | 中身 |
|---|---|
| `scripts/` | 作成ページで書いた台本（タイトル.txt） |
| `media/stock` | Pexels の映像（未使用） |
| `media/stock_used` | 使用済みの映像 |
| `media/anime` | アニメ素材（雰囲気ごと。`tools/sort_anime.py` で振り分け） |
| `media/ai_illustrations` | AI で描いたイラスト（作り直すときに使い回す） |
| `media/photos` / `media/commons` | サムネイル用の写真 / Wikimedia の写真 |
| `media/music` | BGM（CC BY の無料曲） |
| `media/samples` | 見本の動画 |
| `output/videos` | できた動画・サムネイル・説明文（完成動画ページ・投稿ページ） |
| `output/posting` | 投稿用にまとめたフォルダ（video.mp4・thumbnail.jpg・post.txt） |
| `output/old_story_videos` | 以前のコマンドで作った台本動画 |
| `uploads/` | アップロードした録音 |
| `config/` | 設定（settings.json）・画面の状態・プリセット・素材のダウンロード記録 |
| `docs/` | メモ・計画・デザインのルール |
| `tools/` | 補助スクリプト（絵・声・文字起こし・モデルのダウンロード・素材の振り分け、`legacy/` は古いもの） |
| `_old/` | どこからも使われていない古いファイル（不要なら消してかまいません） |

## プログラム（触らなくて大丈夫）

`server.py`（画面とAPI）、`story.py`（台本→動画）、`make_video.py`（音声→動画）、`prep.py`・`illustrate.py`・`media_match.py`・`music.py`・`thumbs.py`・`commons.py`・`publish_meta.py`・`qa.py`・`rights.py`・`make_posting.py`、`static/`（画面）、`tests/`、`kinetic_captions/`（字幕エンジン）、`venv/`（Python の環境）、`logs/`・`tmp/`（作業用）
