"""One-off: move every Japanese text of static/index.html onto translation keys.

    venv/bin/python tools/i18n_extract_html.py      # rewrites index.html, writes static/i18n/ja.json

A text that is the whole content of its element gets data-i18n on that element; a text
mixed with other markup (an icon, a link) is wrapped in <span data-i18n>. placeholder /
title / aria-label become data-i18n-attr; data-label / data-unit values (read by app.js)
are replaced by their key. Keys are "<page>.<n>" (page = the enclosing page-<id> section).
"""
import html as htmllib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
HTML = HERE / "static" / "index.html"
OUT = HERE / "static" / "i18n" / "ja.json"
JP = re.compile(r"[　-ヿ㐀-鿿＀-￯]")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
TOKEN = re.compile(r"(<!--.*?-->|<[^<>]+>)", re.S)


def main():
    html = HTML.read_text()
    parts = TOKEN.split(html)                 # text, tag, text, tag, ...
    ja, by_text, counters = {}, {}, {}
    sections, depth = ["app"], []

    def key_for(text):
        sec = sections[-1]
        k = by_text.get((sec, text))
        if not k:
            counters[sec] = counters.get(sec, 0) + 1
            k = f"{sec}.{counters[sec]}"
            by_text[(sec, text)] = k
            ja[k] = htmllib.unescape(text)       # stored as plain text (textContent)
        return k

    def fix_attrs(tag):
        pairs = []

        def attr(m):
            name, val = m.group(1), m.group(2)
            if not JP.search(val):
                return m.group(0)
            if name in ("data-label", "data-unit"):
                return f' {name}="{key_for(val)}"'
            pairs.append(f"{name}:{key_for(val)}")
            return m.group(0)
        tag = re.sub(r'\s(placeholder|title|aria-label|data-label|data-unit)="([^"]*)"', attr, tag)
        if pairs and "data-i18n-attr" not in tag:
            tag = tag[:-1].rstrip("/").rstrip() + f' data-i18n-attr="{";".join(pairs)}"' + (" />" if tag.endswith("/>") else ">")
        return tag

    for i, p in enumerate(parts):
        if p.startswith("<") and not p.startswith("<!--"):
            m = re.match(r"<(/?)([a-zA-Z0-9-]+)", p)
            if m:
                closing, name = m.group(1) == "/", m.group(2).lower()
                if name == "section":
                    if closing:
                        if depth and depth.pop():
                            sections.pop()
                    else:
                        sid = re.search(r'id="page-([a-z0-9-]+)"', p)
                        depth.append(bool(sid))
                        if sid:
                            sections.append(sid.group(1))
            parts[i] = fix_attrs(p)
    for i, p in enumerate(parts):
        if p.startswith("<") or not JP.search(p):
            continue
        text = p.strip()
        lead, trail = p[:len(p) - len(p.lstrip())], p[len(p.rstrip()):]
        # need the page this text sits in: recompute by scanning the tags before it
        secs, dep = ["app"], []
        for q in parts[:i]:
            m = re.match(r"<(/?)section\b", q)
            if m:
                if m.group(1):
                    if dep and dep.pop():
                        secs.pop()
                else:
                    sid = re.search(r'id="page-([a-z0-9-]+)"', q)
                    dep.append(bool(sid))
                    if sid:
                        secs.append(sid.group(1))
        sections[:] = secs
        k = key_for(text)
        prev, nxt = parts[i - 1] if i else "", parts[i + 1] if i + 1 < len(parts) else ""
        om = re.match(r"<([a-zA-Z0-9-]+)\b[^>]*>$", prev)
        whole = om and om.group(1).lower() not in VOID and not prev.endswith("/>") \
            and re.match(rf"</{om.group(1)}\s*>", nxt) and "data-i18n=" not in prev
        if whole:
            parts[i - 1] = prev[:-1] + f' data-i18n="{k}">'
        else:
            parts[i] = f'{lead}<span data-i18n="{k}">{text}</span>{trail}'
    HTML.write_text("".join(parts))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ja, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(ja)} keys -> {OUT}")


if __name__ == "__main__":
    main()
