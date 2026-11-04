"""Export voice-only audio: background music removed and ads / channel intros cut.

  venv/bin/python export_voice.py <folder with mp3s> [--out DIR] [--part 1/2]

For every mp3: Demucs (voice only) -> Whisper -> ad check (keywords + local AI)
-> cut -> loudness normalised mp3 in <folder>/voice_only/. A report of what
was cut is written next to it (voice_only/_report.md). Originals are never touched.
Uses the same cache as the video pipeline (tmp/<hash>/), so files already
processed there are fast.
"""
import argparse, json, subprocess, sys, time
import sys as _sys
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))  # app modules live in the tool's root
from pathlib import Path

import make_video as mv
import prep


def export(mp3, out_dir, s, log=print):
    out = out_dir / (mp3.stem + ".mp3")
    work = mv.work_dir(mp3)
    work.mkdir(parents=True, exist_ok=True)
    audio, _ = prep.prepare(mp3, work, s, log)
    if not mv.read_json(work / "music.json", {}).get("db") and (work / "voice.wav").exists():
        # voice.wav cached from an older run: measure the music as original minus voice
        res = work / "music_est.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-i", str(work / "voice.wav"),
                        "-filter_complex", "[0:a]aresample=44100[o];[1:a]aresample=44100,volume=-1[v];"
                        "[o][v]amix=inputs=2:normalize=0:duration=shortest", str(res)], check=True)
        mv.write_json(work / "music.json", {"db": mv.mean_volume(res)})
        res.unlink(missing_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(audio),
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "libmp3lame", "-b:a", "192k",
                    str(out.with_suffix(".part.mp3"))], check=True)
    out.with_suffix(".part.mp3").replace(out)
    a = mv.read_json(work / "analysis.json", {})
    a["music_db"] = mv.read_json(work / "music.json", {}).get("db")
    cuts = [x for x in a.get("sentences", []) if x["cut"]]
    return {"name": mp3.name, "original": a.get("total", 0), "final": mv.duration(out),
            "music_db": a.get("music_db"),
            "cuts": [{"s": x["s"], "e": x["e"], "text": x["text"], "reason": x["reason"]} for x in cuts]}


def write_report(out_dir):
    rows = [mv.read_json(p, None) for p in sorted(out_dir.glob(".report/*.json"))]
    rows = [r for r in rows if r]
    fmt = lambda t: f"{int(t // 60)}:{int(t % 60):02d}"
    md = ["# 声だけ音声の書き出しレポート", "",
          f"{len(rows)} 本 ・ 合計 {fmt(sum(r['original'] for r in rows))} → {fmt(sum(r['final'] for r in rows))}", "",
          "| ファイル | 元 | 書き出し | 音楽 | カットした文 |", "|---|---|---|---|---|"]
    for r in rows:
        music = "あり→除去" if (r["music_db"] or -99) > -45 else "なし"
        md.append(f"| {r['name']} | {fmt(r['original'])} | {fmt(r['final'])} | {music} | {len(r['cuts'])} |")
    md += ["", "## カットした内容", ""]
    for r in rows:
        if r["cuts"]:
            md.append(f"### {r['name']}")
            md += [f"- {fmt(c['s'])}〜{fmt(c['e'])} [{c['reason']}] {c['text']}" for c in r["cuts"]]
            md.append("")
    (out_dir / "_report.md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out")
    ap.add_argument("--part", default="1/1", help="k/n: process every n-th file starting at k (parallel runs)")
    a = ap.parse_args()
    src = Path(a.folder).expanduser()
    out_dir = Path(a.out).expanduser() if a.out else src / "voice_only"
    (out_dir / ".report").mkdir(parents=True, exist_ok=True)
    k, n = map(int, a.part.split("/"))
    s = {**mv.load_settings(), "remove_music": True, "trim_ads": True}
    files = sorted(src.glob("*.mp3"))[k - 1::n]
    for i, mp3 in enumerate(files, 1):
        rep = out_dir / ".report" / (mp3.stem + ".json")
        if (out_dir / (mp3.stem + ".mp3")).exists() and rep.exists():
            print(f"[{i}/{len(files)}] skip {mp3.name}", flush=True)
            continue
        t0 = time.time()
        print(f"[{i}/{len(files)}] {mp3.name}", flush=True)
        try:
            r = export(mp3, out_dir, s, log=lambda m: print(m, flush=True))
            mv.write_json(rep, r)
            print(f"  -> {r['original']:.0f}s -> {r['final']:.0f}s, {len(r['cuts'])} cut ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:
            print(f"  FAILED: {e}", flush=True)
        write_report(out_dir)
