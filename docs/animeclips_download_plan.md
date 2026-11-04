# animeclips.online 素材ダウンロード計画(あとで実行)

作成: 2026-11-03 / まだ何もダウンロードしていません。

## 前提
- サイト: https://animeclips.online/ (AMV 編集者向けの無料アニメ素材。ログイン不要)
- 利用規約(https://animeclips.online/terms/)では、書面の許可がない「スクレイピング・一括ダウンロード・自動収集」は禁止。**許可はもらい済み**(ユーザー確認)。
- 著作権は元のアニメ会社のもの(サイトも所有していない)。収益化する動画では Content ID の申し立て・収益化停止のリスクあり。

## サイトの構成
- 作品 → シーズン → Episode N → 素材(`作品名 - S1E1 (12).mp4` のような連番。中身の説明なし)
- 1 話 ≒ 60 本前後 / 約 1.7GB(Vinland Saga S1E1 = 64 本・1719MB)。1 本は数秒〜1 分弱、元の画質(1080p / 約 24fps)、音声付き。
- ダウンロード方法: 話数フォルダを開くとプレビューが読み込まれ、`https://cdn.animeclips.online/drive/<id>?acct=main&exp=...&sig=...` の期限付き URL が出る。サイトの「Download selected」でも可。

## ダウンロードする範囲(どちらか)

| 案 | 中身 | 大きさ | 時間(4.5MB/s) |
|---|---|---|---|
| おすすめ 5 作品 | 下の表すべて | 約 12GB | 約 1 時間 |
| 少なめ | Vinland Saga S1E1 + Berserk (1997) E1 | 約 3GB | 約 15 分 |

| 作品 | URL | 範囲 | 向いている場面 |
|---|---|---|---|
| Vinland Saga | https://animeclips.online/vinland-saga/ | Season 1 / Episode 1・2 | 戦い・孤独・復讐 |
| Berserk (1997) | https://animeclips.online/berserk-1997/ | Episode 1・2 | 戦い・苦しみ |
| Megalo Box | https://animeclips.online/megalo-box/ | Episode 1・2 | 修行・逆境・勝利 |
| Sword of the Stranger | https://animeclips.online/sword-of-the-stranger/ | 映画 | 剣・孤独 |
| Uzumaki | https://animeclips.online/kurouzu-cho/ | Episode 1 | 白黒の雰囲気 |

## 保存先と振り分け
保存先: `motiversity_auto/素材/anime/`(フォルダは作成済み)

| フォルダ | 場面 |
|---|---|
| `fight_戦い` | 戦い・剣・殴り合い |
| `training_修行` | 修行・努力・汗 |
| `alone_孤独` | 一人で歩く・佇む・背中 |
| `rain_storm_雨嵐` | 雨・嵐・暗い空 |
| `sad_悲しみ` | 涙・喪失・うつむく |
| `victory_勝利` | 立ち上がる・勝つ・叫ぶ |
| `nature_景色` | 空・海・山・街並み |

落としたあと: CLIP(media_match.py と同じモデル)で中身を見て上のフォルダへ自動で振り分け → 台本ページの「自分のフォルダ」で `素材/anime` を選ぶ。

## そのほか使えそうな作品(サイト内 237 作品から)
- ◎ BHATT 風に合う: Berserk – The Golden Age、Samurai Champloo、Afro Samurai (+Resurrection)、Rurouni Kenshin、Hell's Paradise、Ninja Kamui、Vampire Hunter D: Bloodlust、Hunter x Hunter、Fullmetal Alchemist: Brotherhood、Solo Leveling、Attack on Titan、Demon Slayer (+Infinity Castle Part 1)、Jujutsu Kaisen 0、Mob Psycho 100、One Punch Man、Kaiju No.8、Record of Ragnarok、Gachiakuta、Fire Force、To Your Eternity、Frieren、Blue Lock、Haikyuu!!、Kuroko's Basketball、100 Meters、Tokyo Ghoul、Death Note、Chainsaw Man、Devilman: Crybaby、Hellsing Ultimate、Castlevania、Arcane、Cyberpunk: Edgerunners (1・2)、Akira、Ghost in the Shell (1995)、Psycho-Pass、Evangelion(新劇場版・End of Evangelion)、Dorohedoro、Kabaneri of the Iron Fortress、Bleach: Thousand-Year Blood War
- ○ 場面によって: A Silent Voice、Your Lie in April、Violet Evergarden、5 Centimeters per Second、The Garden of Words、Weathering with You、Suzume、Spirited Away、Howl's Moving Castle、Look Back、Maquia、Children of the Sea、The Girl Who Leapt Through Time、Summer Wars、Ride Your Wave、Orange、Sonny Boy、Angel's Egg、Perfect Blue、Serial Experiments Lain、Death Parade、Erased、Steins;Gate、Heavenly Delusion、The Summer Hikaru Died、Cowboy Bebop: The Movie、Terminator Zero、Gurren Lagann(劇場版)、Dragon Ball の劇場版
- × 向かない: ラブコメ・日常・お色気が中心の作品
