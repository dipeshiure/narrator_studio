"""Compare each static/i18n/<lang>.json with en.json: unknown keys, missing keys, {placeholder} mismatches.

    venv/bin/python tools/i18n_check.py          # missing keys just fall back to English at runtime
"""
import json
import re
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "static" / "i18n"
en = json.loads((D / "en.json").read_text())
ph = lambda s: sorted(re.findall(r"\{(\w+)\}", s))
bad = 0
for f in sorted(D.glob("*.json")):
    d = json.loads(f.read_text())
    extra = set(d) - set(en)
    wrong = [k for k in d if k in en and ph(d[k]) != ph(en[k])]
    bad += len(extra) + len(wrong)
    print(f"{f.stem}: {len(d)}/{len(en)} keys" + (f", unknown {sorted(extra)}" if extra else "")
          + (f", placeholder mismatch {wrong}" if wrong else ""))
raise SystemExit(1 if bad else 0)
