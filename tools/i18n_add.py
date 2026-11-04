"""Helper for moving JS strings onto translation keys (used by the one-off i18n passes).

    from i18n_add import apply
    apply("static/app.js", [(old, new), ...], {"key": ("日本語", "English"), ...})

Every `old` must occur exactly as written (asserted); entries are merged into
static/i18n/ja.json and en.json (existing keys are overwritten).
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
I18N = HERE / "static" / "i18n"


def apply(path, replacements, entries):
    p = HERE / path
    text = p.read_text()
    for old, new in replacements:
        assert old in text, f"{path}: not found: {old[:80]}"
        text = text.replace(old, new)
    p.write_text(text)
    for lang, idx in (("ja", 0), ("en", 1)):
        f = I18N / f"{lang}.json"
        d = json.loads(f.read_text()) if f.exists() else {}
        d.update({k: v[idx] for k, v in entries.items()})
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    print(f"{path}: {len(replacements)} replacements, {len(entries)} keys")
