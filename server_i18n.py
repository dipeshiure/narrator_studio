"""English for the Japanese text the server sends to the UI (job logs, errors, QA results ...).

The pipeline keeps writing Japanese (stored results, log files and story.js' progress parser
stay as they are); server.py runs every JSON response through `localize()` when the UI is in
English (cookie "lang=en", set by static/i18n.js). A string is translated only when it matches
one of the templates below as a whole line — anything else (titles, descriptions, file names)
passes through untouched. `{x}` matches any text, which is itself translated (so nested
messages like "エラー: 素材がありません" come out fully English).
"""
import re

T = {
    # ---- generic
    "エラー: {e}": "Error: {e}",
    "注意: {w}": "Note: {w}",
    "完了": "Done",
    "できました": "Done",
    "完成しました": "Done",
    "準備中": "Preparing",
    "開始": "Starting",
    "要確認": "Unknown",
    "ワーカー {n}": "Worker {n}",
    # ---- server.py
    "解析エラー: {e}": "Analysis error: {e}",
    "後処理のエラー: {e}": "Post-processing error: {e}",
    "動画を作れませんでした {e}": "Couldn't make the video {e}",
    "再起動で後処理が中断されました(投稿ページの「公開キットを作り直す」で再実行できます)":
        "Post-processing was interrupted by a restart (run it again with \"Rebuild post kit\" on the Publish page)",
    "まだ解析が終わっていません": "Analysis hasn't finished yet",
    "処理中は再解析できません": "Can't re-analyze while processing",
    "フォルダが見つかりません": "Folder not found",
    "フォルダが見つかりません: {p}": "Folder not found: {p}",
    "ファイルがありません": "No file",
    "対応していない形式です": "Unsupported format",
    "音声ファイルが見つかりません": "No audio files found",
    "同じ名前の動画がすでにあります": "A video with the same name already exists",
    "生成できませんでした(他のワーカーが処理中の可能性があります)": "Couldn't generate (another worker may be processing it)",
    "Christopher(低く重厚・米)": "Christopher (deep, heavy · US)",
    "Prabhat(落ち着いた・インド英語)": "Prabhat (calm · Indian English)",
    "Guy(情熱的・米)": "Guy (passionate · US)",
    "Andrew(温かい・米)": "Andrew (warm · US)",
    "Brian(親しみ・米)": "Brian (friendly · US)",
    "Eric(冷静・米)": "Eric (cool · US)",
    "Ryan(英国)": "Ryan (UK)",
    "Thomas(英国)": "Thomas (UK)",
    "長尺の語り(白黒イラスト)": "Long narration (B&W illustrations)",
    "モチベーション(白黒)": "Motivation (B&W)", "宇宙・SF": "Space & sci-fi", "歴史": "History", "自然・動物": "Nature & animals",
    "ミステリー・怖い話": "Mystery & scary stories", "科学・解説": "Science explainer", "ファンタジー": "Fantasy",
    "テーマを入力してください": "Enter a theme",
    "下書きができました。読んで直してから「動画を作る」を押してください": "Draft ready. Read and edit it, then press \"Make video\"",
    "声を作れませんでした(ネット接続を確認してください): {e}": "Couldn't make the voice (check your internet connection): {e}",
    "Dia が入っていません: {e}": "Dia isn't installed: {e}",
    "声を作れませんでした: {e}": "Couldn't make the voice: {e}",
    "素材フォルダが見つかりません": "Footage folder not found",
    "台本を入力してください": "Enter a script",
    "このフォルダには使える動画・写真がありません": "This folder has no usable videos or photos",
    "素材 {n} 本と照らし合わせています": "Matching against {n} clips",
    "タイトルと台本を入力してください": "Enter a title and script",
    "同じタイトルの動画があります(「作り直す」をオンにしてください)": "A video with this title exists (turn on \"Redo\")",
    "台本: {f}({n} 段落, プリセット: {p})": "Script: {f} ({n} paragraphs, preset: {p})",
    "注意: 投稿用フォルダを更新できませんでした({e})": "Note: couldn't update the posting folder ({e})",
    "フォント名か Google Fonts の URL を入れてください": "Enter a font name or a Google Fonts URL",
    "Google Fonts から取得できませんでした(ネット接続を確認してください): {e}": "Couldn't get it from Google Fonts (check your internet connection): {e}",
    "追加したフォントだけ削除できます": "Only fonts you added can be deleted",
    "すでに実行中です": "Already running",
    "{q}: +{n} 本": "{q}: +{n} clips",
    "完了: 未使用の素材 {n} 本": "Done: {n} unused clips",
    "キーの形式が正しくありません": "The key format isn't valid",
    "完了: {n} 本をチェックしました": "Done: checked {n} videos",
    "公開キットを作り直しています: {f}": "Rebuilding post kit: {f}",
    "公開キットを作れませんでした": "Couldn't build the post kit",
    "投稿用フォルダの更新エラー: {e}": "Posting folder update error: {e}",
    "台本を選んでください": "Choose scripts",
    "録音ファイルを選んでください": "Choose a recording",
    "自分の声か、使う許可をもらった声だけ追加できます": "Only your own voice or one you have permission to use can be added",
    "録音が短すぎます({n} 秒以上にしてください)": "The recording is too short (at least {n} s)",
    "録音の中の言葉を聞き取れませんでした。話した内容を入力してください": "Couldn't hear the words in the recording. Type what is said",
    "台本が見つかりません": "Script not found",
    "1 行目にタイトルを書いてください": "Put the title on the first line",
    "== {t}": "== {t}",
    "スキップ(同じタイトルの動画があります)": "Skipped (a video with this title exists)",
    "残りを中止しました": "Cancelled the rest",
    "一括作成: {a} 本完成 / {b} 本失敗 / {c} 本スキップ": "Batch: {a} done / {b} failed / {c} skipped",
    # ---- rights.py
    "未確認": "Not checked", "問題なし": "No issues", "申し立てあり": "Claimed",
    "ブロック・制限": "Blocked / restricted", "削除した": "Deleted",
    "権利チェック: {s}": "Rights check: {s}",
    # ---- qa.py
    "長さ": "Length", "デコード": "Decode", "音声トラック": "Audio track", "解像度": "Resolution",
    "字幕": "Captions", "宣伝の消し残し": "Leftover promos", "字幕と声のずれ": "Caption / voice sync",
    "字幕の単語データがありません": "No caption word data",
    "照合できた言葉が少なすぎます({n} 箇所)": "Too few words could be matched ({n})",
    "ずれ 中央値 {a} 秒 / 最大 {b} 秒({n} 箇所, ±{p} 秒以内 {q})": "drift median {a}s / max {b}s ({n} points, {q} within ±{p}s)",
    "動画 {a} 秒 / 音声 {b} 秒(差 {c} 秒)": "video {a}s / audio {b}s (diff {c}s)",
    "音声の長さを確認できません: {e}": "Can't read the audio length: {e}",
    "QA: 全体をデコードしてエラーを確認中…": "QA: decoding the whole video to check for errors…",
    "エラーなし": "No errors",
    "音声がありません": "No audio",
    "{a}x{b}(期待 {c}x{d})": "{a}x{b} (expected {c}x{d})",
    "字幕ファイルがありません": "No caption file",
    "{n} 行": "{n} lines",
    "字幕が 0 行です": "Captions have 0 lines",
    "字幕はオフ": "Captions off",
    "残っている言葉: {w}": "Words left: {w}",
    "なし": "None",
    "オフ": "Off",
    "QA: 字幕と声のずれを確認中(60 秒・Whisper base)…": "QA: checking caption / voice sync (60 s · Whisper base)…",
    "確認できませんでした: {e}": "Couldn't check: {e}",
    "品質チェック: {r}": "Quality check: {r}",
    "品質チェックのエラー: {e}": "Quality check error: {e}",
    # ---- make_video.py
    "Pexels の API キーがありません(motiversity_auto/.env に PEXELS_API_KEY=... を書いてください)":
        "No Pexels API key (add PEXELS_API_KEY=... to .env)",
    "素材が {n} 本足りないので Pexels からダウンロードします": "{n} clips short, downloading from Pexels",
    "+ {q}: {n} 本": "+ {q}: {n} clips",
    "+ {q}: {n} 本ダウンロード": "+ {q}: downloaded {n} clips",
    "Pexels の API キーがないため、手元の素材だけで作ります": "No Pexels API key, using local footage only",
    "未使用の素材が足りないため、使用済み素材(使用回数の少ない順)も使います": "Not enough unused footage, also using used clips (least used first)",
    "メモリの空きが少ないので待っています(空き {a}%、{b}% になったら再開)。ほかのアプリを閉じると早く進みます":
        "Low free memory, waiting ({a}% free, resumes at {b}%). Closing other apps speeds this up",
    "! メモリが空かないまま続けます(スワップが増えるかもしれません)": "! Continuing without enough free memory (swap may grow)",
    "kinetic-captions の環境がありません。motiversity_auto で次を実行してください: {c}": "kinetic-captions isn't set up. Run: {c}",
    "字幕 {p}%": "Captions {p}%",
    "kinetic-captions の書き出しに失敗しました: {e}": "kinetic-captions export failed: {e}",
    "音楽を除去して声だけにしています(Demucs)...": "Removing music to keep only the voice (Demucs)...",
    "音楽の除去に失敗しました": "Music removal failed",
    "素材がありません(オフライン設定で素材が0本の可能性)": "No footage (offline mode may have 0 clips)",
    "素材 {n} 本を確保(未使用 残り {m} 本)": "Reserved {n} clips ({m} unused left)",
    "kinetic-captions で字幕を入れています...": "Adding captions with kinetic-captions...",
    "BGM 入りの長さが元の動画と合いません": "Length with BGM doesn't match the original video",
    "BGM を入れました: {t}": "Added BGM: {t}",
    "BGM の曲がないので、無料音楽(incompetech)をダウンロードします": "No BGM tracks, downloading free music (incompetech)",
    "! BGM を用意できなかったので、声だけで作ります": "! Couldn't get BGM, making it with voice only",
    "! BGM エラー({e})。声だけで作ります": "! BGM error ({e}). Making it with voice only",
    "公開キット(タイトル・説明文・サムネイル)を作成中…": "Making post kit (title, description, thumbnail)…",
    "公開キットのエラー: {e}": "Post kit error: {e}",
    "公開キット: {s}": "Post kit: {s}",
    "品質チェック中(長さ・デコード・字幕)…": "Quality check (length, decode, captions)…",
    "投稿用フォルダを更新中…": "Updating posting folder…",
    # ---- prep.py (inbox analysis)
    "広告": "Ad", "スポンサー紹介": "Sponsor", "プロモコード": "Promo code", "アプリの宣伝": "App promo",
    "リンクの案内": "Link mention", "チャンネル登録・高評価のお願い": "Subscribe / like request", "SNS の案内": "Social media",
    "チャンネルの挨拶(冒頭)": "Channel intro", "締めの挨拶・告知": "Outro / announcement", "宣伝・告知": "Promo",
    "キーワード「{k}」": "Keyword \"{k}\"",
    "宣伝の前後のつなぎ": "Lead-in / out of a promo",
    "! Ollama が見つかりません(AI を使う処理はキーワードだけで行います)": "! Ollama not found (AI steps use keywords only)",
    "Ollama を起動しています…": "Starting Ollama…",
    "! Ollama を起動できませんでした": "! Couldn't start Ollama",
    "! AI 判定を使えませんでした({e})。キーワード判定のみ行います": "! Couldn't use AI detection ({e}). Keywords only",
    "同じ内容の音声の解析結果を再利用します": "Reusing the analysis of identical audio",
    "音楽はほぼ含まれていません({d} dB)。元の音声をそのまま使います": "Almost no music ({d} dB). Using the original audio",
    "文字起こし中(Whisper)...": "Transcribing (Whisper)...",
    "宣伝・告知をチェック中(AI: {m})...": "Checking for promos (AI: {m})...",
    "宣伝・告知: {n} 文": "Promos: {n} lines",
    "宣伝・告知は見つかりませんでした": "No promos found",
    "すべての文がカット対象になっています。確認画面で残す部分を選んでください": "Every line is marked to cut. Choose what to keep in the review screen",
    "宣伝・告知など {s} 秒をカットしました": "Cut {s}s of promos",
    "長い無音(間)を {s} 秒詰めました": "Tightened {s}s of long silence",
    # ---- story.py
    "{w}: chart タグは [[chart:line|タイトル|ラベル=値,...|単位|出典]] の形式です": "{w}: a chart tag looks like [[chart:line|title|label=value,...|unit|source]]",
    "{w}: chart の値「{p}」は ラベル=数値 にしてください": "{w}: chart value \"{p}\" must be label=number",
    "{w}: 不明なタグ [[{k}:...]] (photo / entity / query / chart / media)": "{w}: unknown tag [[{k}:...]] (photo / entity / query / chart / media)",
    "{w}: 1 つのシーンに {k} タグが 2 つあります。段落を分けてください": "{w}: one scene has two {k} tags. Split the paragraph",
    "{w}: [[{k}:]] の中身が空です": "{w}: [[{k}:]] is empty",
    "{f}: title と scenes(text) が必要です": "{f}: needs a title and scenes (text)",
    "台本の構成を作っています({m} 分 / {n} 章)": "Outlining the script ({m} min / {n} chapters)",
    "{i} 章を書いています: {h}": "Writing chapter {i}: {h}",
    "AI が台本を書けませんでした(Ollama の状態を確認してください)": "The AI couldn't write the script (check Ollama)",
    "! LLM で検索語を作れませんでした({e})。キーワードで代用します": "! Couldn't make search terms with the LLM ({e}). Using keywords",
    "! {n} 場面は AI で検索語を作れなかったので、プリセットの映像リストから選びます": "! No AI search terms for {n} scenes, picking from the preset's footage list",
    "音声が空でした": "The audio was empty",
    "ナレーションを作れませんでした(edge-tts に接続できません)。ネット接続を確認して、もう一度作ると続きから再開します":
        "Couldn't make the narration (can't reach edge-tts). Check your internet connection; making it again resumes where it stopped",
    "男性・低い": "male · deep", "男性・落ち着いた": "male · calm", "男性・やや高め": "male · slightly higher",
    "ナレーションを作成中(Dia・この Mac で生成): {n} 段落": "Making narration (Dia, on this Mac): {n} paragraphs",
    "ナレーション {a}/{b}": "Narration {a}/{b}",
    "メモリの空きが少ないので一時停止しています(スワップを防ぐため)。ほかのアプリを閉じると再開します":
        "Paused: low free memory (to avoid swap). Closing other apps resumes it",
    "ほかの重い処理(声または絵)が終わるのを待っています(同時には動かしません)": "Waiting for the other heavy task (voice or images) to finish (they never run together)",
    "メモリが空いたので再開しました": "Memory freed, resumed",
    "! 聞き取りにくい文が残りました: {s}": "! A hard-to-hear line remains: {s}",
    "Dia でナレーションを作れませんでした: {e}": "Dia couldn't make the narration: {e}",
    "! {n} か所で声の高さがお手本からずれたままです(聞いて確認してください)": "! Pitch still off the reference in {n} places (listen to check)",
    "グラフ動画の作成に失敗しました": "Couldn't make the chart video",
    "! 「{p}」の写真が見つからないので、素材映像を使います": "! No photo found for \"{p}\", using footage",
    "ほかの動画を作成中です。終わるまで待ってから始めます(重い処理は 1 本ずつ)": "Another video is being made. Waiting for it to finish (heavy jobs run one at a time)",
    "! この作成中にスワップが {n}MB 増えました(ほかのアプリを閉じて作ると防げます)": "! Swap grew {n}MB during this render (closing other apps prevents it)",
    "== {t}  ({n} シーン, 声: {v})": "== {t}  ({n} scenes, voice: {v})",
    "! Dia の準備ができていないので、edge-tts の声で作ります": "! Dia isn't ready, using an edge-tts voice",
    "ナレーション完成: {m} 分": "Narration done: {m} min",
    "映像を準備しています(場面ごとの検索語を決めています)": "Preparing visuals (choosing search terms per scene)",
    "自分の素材 {n} 本から、段落の意味に合うものを選んでいます": "Picking clips that fit each paragraph from your {n} clips",
    "! 素材フォルダに使えるファイルがないので、素材映像(Pexels)で作ります": "! No usable files in the footage folder, using stock footage (Pexels)",
    "! AI イラストのモデルがまだ準備できていないので、白黒の素材映像で作ります": "! The AI illustration model isn't ready, using B&W stock footage",
    "シーン {a}: イラスト {n} 枚": "Scene {a}: {n} illustrations",
    "シーン {a}: 写真「{p}」 {n} 枚": "Scene {a}: photo \"{p}\" ×{n}",
    "シーン {a}: グラフ「{t}」": "Scene {a}: chart \"{t}\"",
    "シーン {a}: 「{q}」 {n} カット": "Scene {a}: \"{q}\" {n} cuts",
    "シーン {a}: {x}": "Scene {a}: {x}",
    "素材がありません": "No footage",
    "字幕を作成中(Whisper)...": "Making captions (Whisper)...",
    "最終エンコード中...": "Final encode...",
    "プリセットがありません: {p}": "No such preset: {p}",
    # ---- illustrate.py / media_match.py / music.py / kinetic bridge
    "! AI で絵の指示文を作れませんでした({e})。キーワードで代用します": "! Couldn't write image prompts with AI ({e}). Using keywords",
    "イラストを生成中: {n} 枚({m}, 作成済みは再利用)": "Generating {n} illustrations ({m}, reusing ones already made)",
    "イラスト {a}/{b}": "Illustration {a}/{b}",
    "! イラスト {n} 枚を作れませんでした({e})。その場面は素材映像を使います": "! Couldn't make {n} illustrations ({e}). Those scenes use footage",
    "素材フォルダを解析しています: 新しいファイル {n} 本(初回だけ時間がかかります)": "Analyzing the footage folder: {n} new files (slow only the first time)",
    "解析 {a}/{b}": "Analyzing {a}/{b}",
    "! AI に接続できません({e})。キーワードで代用します": "! Can't reach the AI ({e}). Using keywords",
    "! AI で映像の説明を作れませんでした({e})。その部分はキーワードで代用します": "! Couldn't describe the footage with AI ({e}). Using keywords there",
    "! ダウンロードが続けて失敗したので中断しました(ネット接続を確認してください)": "! Downloads kept failing, stopped (check your internet connection)",
    "字幕の演出を決めています(kinetic-captions)": "Planning caption animation (kinetic-captions)",
    "字幕を書き込んでいます({n} フレーズ)": "Writing captions ({n} phrases)",
    # ---- kinetic_captions/kc/fonts.py
    "Courgette（筆記体）": "Courgette (script)",
    "フォント名かURLを入れてください": "Enter a font name or URL",
    "「{n}」はフォント名として読み取れませんでした": "\"{n}\" couldn't be read as a font name",
    "Google Fonts 以外からはダウンロードしません": "Only Google Fonts downloads are allowed",
    "Google Fonts に「{n}」が見つかりませんでした（スペルを確認してください）": "\"{n}\" wasn't found on Google Fonts (check the spelling)",
    "フォントファイルを取得できませんでした": "Couldn't get the font file",
}

# content fields: never translated even if a line happens to match
SKIP = {"title", "title_en", "title_ja", "text", "description_en_full", "description_ja_full", "tags", "hashtags",
        "thumbnail_text", "speech_source", "detected_source", "name", "stem", "file", "path", "url", "folder", "artist", "draft", "script"}
JP = re.compile(r"[぀-ヿ一-鿿]")
LEAD = re.compile(r"^(\s*(?:\[[^\]]*\]\s*)?)(.*?)(\s*)$", re.S)   # indent / "[12:01:02] " stamp kept as is


def _compile():
    out = []
    for ja, en in T.items():
        names = re.findall(r"\{(\w+)\}", ja)
        pat = "".join(f"(?P<{p[1:-1]}>.+?)" if re.fullmatch(r"\{\w+\}", p) else re.escape(p)
                      for p in re.split(r"(\{\w+\})", ja) if p)
        literal = len(re.sub(r"\{\w+\}", "", ja))
        out.append((literal, re.compile(pat + r"\Z", re.S), en, names))
    out.sort(key=lambda x: -x[0])          # most specific template first
    return [x[1:] for x in out]


RULES = _compile()


def en(text):
    """English for one message (line by line); unknown text is returned unchanged."""
    if not isinstance(text, str) or not JP.search(text):
        return text
    if "\n" in text:
        return "\n".join(en(line) for line in text.split("\n"))
    m = LEAD.match(text)
    lead, body, trail = m.group(1), m.group(2), m.group(3)
    for rx, out, names in RULES:
        mm = rx.match(body)
        if mm:
            vals = {k: en(v) for k, v in mm.groupdict().items()}
            return lead + out.format(**vals) + trail
    return text


def localize(obj, key=None):
    if key in SKIP:
        return obj
    if isinstance(obj, str):
        return en(obj)
    if isinstance(obj, list):
        return [localize(x, key) for x in obj]
    if isinstance(obj, dict):
        return {k: localize(v, k) for k, v in obj.items()}
    return obj
