# 素材自動選択の統合計画

実装はまだしない。この文書は、今の Narrator Studio に足す場所を決めるためのもの。

## 決めたこと

新しい映像の選び方は、今の3つのモードを置き換えない。`visuals` に `asset` を足したときだけ動く。

- `ai` のままなら、今どおり台本全文から FLUX の絵を作る。
- `stock` のままなら、今どおり Pexels の動画を検索語だけで切る。
- `folder` のままなら、今どおり自分のフォルダを CLIP で選ぶ。
- `asset` のときだけ、ストックを先に探し、点数が足りなければその場面だけ AI の絵にする。

視覚の採点は、すでに入っている CLIP（`media_match.py`）を使う。有料の画像 API は足さない。Pexels 以外の素材サイトも足さない。

## 今の構成

Python の Flask アプリ。入口は `server.py`（http://127.0.0.1:8510）。動画の本体は `story.py` の `make_story`。

1. 台本は空行で場面になる。`##` 見出し、`[[query:]]`、`[[chart:]]`、`[[photo:]]` も読む。
2. CosyVoice 2 が読み、各場面に `dur` と単語時刻が付く。
3. 映像は `visuals` で分かれる。
4. 各カットは ffmpeg（`h264_videotoolbox`）で短い mp4 になる。
5. それらを concat し、声と BGM を載せ、字幕（kinetic-captions または ASS）を焼いて `output/videos/` に出す。

素材の出どころは次のとおり。

| モード | 選び方 | 主なファイル |
| --- | --- | --- |
| ai | 台本全文を Ollama に1回渡し、英語プロンプトを FLUX で描く | `illustrate.py` |
| stock | 場面ごとに短い検索語を作り、Pexels の動画を落とす。中身は見ていない | `story.plan_queries`、`story.take_for_query`、`make_video.fetch_new` |
| folder | 自分の動画と写真を CLIP で文と照合する | `media_match.py` |
| `[[photo:]]` | Wikimedia Commons | `commons.py` |
| `[[chart:]]` | グラフを描く | `story.render_chart` |

Pexels のキーは環境変数 `PEXELS_API_KEY`、なければこのプロジェクトの `.env`。キーが無いときは手元のファイルだけ使う。ダウンロード済みの動画は `media/stock/<検索語>/<id>.mp4`、使ったものは `media/stock_used/`。同じ id は `known_ids()` で二度落とさない。

ffmpeg、字幕、声、BGM、クロスズーム、書き出しは `story.make_story` の後半にまとまっている。新しいモジュールはここを複製しない。

## 統合する場所

`story.make_story` の「2. visuals」の分岐に、`visuals == "asset"` を1つ足す。ナレーションが終わった後、各場面の秒数が分かってから呼ぶ。

モジュールが返すのは、今の描画がすでに受け取れる形だけにする。

- 動画: ファイルパスと、そのカットの秒数。今の stock 分岐と同じ ffmpeg の切り出しに渡す。
- 写真: ファイルパスと秒数。今の `render_photo` に渡す。
- AI の絵: その場面だけ `illustrate` で1枚作り、`render_photo` に渡す。動画全体のタイムライン生成は呼ばない。
- 権利表示: 今の `credits` のリストに、作者とライセンスと URL を足す。

`[[chart:]]` と `[[photo:]]` は今の処理のまま。素材選びの対象にしない。

## モジュールの中身

新しいフォルダ `asset_engine/`。既存の関数を呼ぶ。新しいフレームワークは入れない。

1. `analyze.py`  
   場面ごとに、主題、場所、動作、雰囲気、絵の種類、検索語を出す。Ollama は今の `story._ollama`。検索語は2組まで。1組で足りなければ2組目でもう一度探す。
2. `search.py`  
   同じ Pexels キーで、先に動画、次に写真を探す。動画の本ファイルはまだ落とさない。API が返すプレビュー画像だけを採点に使う。
3. `evaluate.py`  
   CLIP で「この文に合うか」を採点する。ファイル名では選ばない。設定した点数（初期値 0.28）以上のいちばん高いものを採用する。同じファイルの再採点は `tmp/asset_cache/` に残す。
4. `select.py`  
   採用が無いとき、代替の検索語で1回だけやり直す。それでも無ければ、その場面だけ AI の絵にする。
5. `store.py`  
   採用したものだけダウンロードする。`media/stock` と `known_ids()` を使い、同じ id は落とさない。横に `id.json` を置き、出典、作者、ライセンス、ページ URL、検索語、点数を残す。mp4 は 200KB 未満を壊れたファイルとして捨てる。写真は JPEG か PNG で、小さすぎるものは捨てる。

失敗、API の待ち、落とした本数、AI の絵を作った場面数は、今の `log` に出す。

## 変えるファイル

- `story.py`  
  `visuals == "asset"` の分岐だけ足す。描画は今の ffmpeg と `render_photo` を呼ぶ。
- `server.py`  
  台本ページの選択肢に `asset` を足すときだけ。設定のキーは今の `STORY_KEYS` に寄せる。
- `server_i18n.py`  
  新しいログ文の英訳だけ。

今の `ai`、`stock`、`folder` の関数は変えない。

## 新しく作るファイル

- `asset_engine/__init__.py`
- `asset_engine/analyze.py`
- `asset_engine/search.py`
- `asset_engine/evaluate.py`
- `asset_engine/select.py`
- `asset_engine/store.py`
- `config/asset_engine.json`（採点の最低点、候補の最大数、AI へ落とすかどうか）
- `tests/test_asset_engine.py`

## 費用を抑えるルール

1. 候補はプレビュー画像で採点し、勝った1本だけダウンロードする。
2. すでに `media/stock` にある id は API でもディスクでもやり直さない。
3. 検索のやり直しは場面あたり1回。
4. 点数が閾値以上なら、少し低くても AI の絵は作らない。
5. AI の絵は、ストックが無かった場面だけ。FLUX を動画全体には回さない。
6. CLIP はローカル。画像を外部の視覚 API に送らない。

## 壊しそうな点

- CLIP と FLUX と Ollama は同じメモリを使う。採点が終わってから、必要なときだけ FLUX を起動する。今の `unload_llm` と同じ順にする。
- `visuals` の初期値は変えない。変えると、今の AI 動画がストック選びに切り替わる。
- Pexels が失敗しても動画全体は止めない。その場面だけ AI の絵か、手元の未使用クリップに戻す。
- 権利の文字列は、今の字幕クレジットの長さに合わせる。長い URL は説明欄用の json にだけ残す。
- テストはネットワークを叩かない。Pexels の応答と CLIP の点数は差し替える。

## テストする項目

モジュール単体で、次を先に通す。

- キーが無い
- API が 429 または 500
- 検索結果が空
- 点数が閾値未満で、代替語のあと AI の絵になる
- 同じ id を二度落とさない
- `id.json` に出典とライセンスがある
- 壊れたファイルを採用しない

その後、`story.py` には「返ってきたパスを、今の ffmpeg と `render_photo` に渡す」ことだけをテストする。字幕、声、BGM のテストは今のものを壊していないことだけ見る。

## まだやらないこと

- 新しいアプリ、新しいタイムライン、新しい字幕、新しい書き出し。
- Pexels 以外の素材サイト。
- 有料の画像生成 API。今の FLUX 以外で絵を作らない。
- 画面の大改修。`asset` を選ぶ項目を足すのは、モジュールのテストが通ってから。
