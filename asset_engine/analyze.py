"""One local LLM call reads the scenes and returns two filmable search phrases each."""
import json

SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"},
        "subject": {"type": "string"},
        "environment": {"type": "string"},
        "actions": {"type": "string"},
        "mood": {"type": "string"},
        "style": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}},
    }, "required": ["n", "queries"]}}},
    "required": ["scenes"],
}


def _fallback(sc):
    import story
    q = story.keyword_query(sc.get("text") or "", ["person walking"])
    return {"subject": "", "environment": "", "actions": "", "mood": "", "style": "",
            "queries": [q, ""], "prompt": q}


def briefs(scenes, model, log=print, ask=None):
    """[{subject, environment, actions, mood, style, queries, prompt}] aligned to scenes."""
    if ask is None:
        import story

        def ask(prompt, schema):
            return story._ollama(prompt, model, temperature=0.2, timeout=180, schema=schema)

    numbered = "\n".join(f"{i + 1}. {(sc.get('text') or '')[:500]}" for i, sc in enumerate(scenes))
    prompt = (
        "For each numbered narration paragraph, describe the picture a camera should find. "
        "subject, environment, actions, mood and style are short English phrases. "
        "queries is exactly two stock-search phrases (2-4 words each). The second is a different way to search "
        "for the same moment, used only if the first finds nothing. No brand names. "
        f"Return exactly {len(scenes)} rows, n starting at 1.\n\n{numbered}")
    try:
        raw = ask(prompt, SCHEMA)
        data = json.loads(raw) if isinstance(raw, str) else raw
        rows = data.get("scenes") if isinstance(data, dict) else None
        by_n = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                n = int(row.get("n"))
            except (TypeError, ValueError):
                continue
            qs = [str(q).strip() for q in (row.get("queries") or []) if str(q).strip()]
            by_n[n] = {
                "subject": str(row.get("subject") or "").strip(),
                "environment": str(row.get("environment") or "").strip(),
                "actions": str(row.get("actions") or "").strip(),
                "mood": str(row.get("mood") or "").strip(),
                "style": str(row.get("style") or "").strip(),
                "queries": (qs + ["", ""])[:2],
            }
        out = []
        for i, sc in enumerate(scenes):
            row = by_n.get(i + 1) or _fallback(sc)
            bits = [row.get("subject"), row.get("actions"), row.get("environment"), row.get("mood")]
            row["prompt"] = ", ".join(b for b in bits if b) or row["queries"][0]
            row.setdefault("queries", ["", ""])
            out.append(row)
        return out
    except Exception as e:
        log(f"  ! 場面の分析に失敗しました({e})。キーワードで探します")
        return [_fallback(sc) for sc in scenes]
