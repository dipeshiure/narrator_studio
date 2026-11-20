"""Turn a narration line into a short English stock-search query.

One model: the checkpoint in tools/models/flan-t5-ad-en. The script line goes in.
The query that comes out names a place and the mood of that line. Ollama is not asked.
"""
from pathlib import Path

_model = None
_tok = None

PROMPT = (
    'Here is a line from a narration script.\n'
    'Line: "{s}"\n'
    "Write a stock-video search query of 2 to 4 English words. "
    "Include the place and the mood of this line. Reply with the query only."
)


def model_dir():
    here = Path(__file__).resolve().parents[1] / "tools" / "models" / "flan-t5-ad-en"
    if (here / "config.json").exists():
        return here
    other = Path.home() / "Downloads" / "narrator_studio" / "tools" / "models" / "flan-t5-ad-en"
    return other


def rewrite(query, text):
    """Search phrase for this script line. The original line is used only if the model fails."""
    line = " ".join((text or query or "").split())
    if not line:
        return query or ""
    try:
        out = _generate(line[:400])
    except Exception:
        return (query or line)[:80]
    out = " ".join(out.replace('"', " ").split())
    if not out or out.lower() in ("yes", "no"):
        return (query or line)[:80]
    return out[:80]


def script_queries(scenes, model=None, log=print):
    """One search phrase per scene, from the script text. No mood call to Ollama."""
    rows = []
    for sc in scenes:
        text = (sc.get("text") or "").strip()
        q = rewrite(text, text)
        if q:
            log(f"  T5 の検索語: {q}")
        rows.append({"queries": [q] if q else [], "prompt": q or text[:180]})
    release()
    return rows


def _generate(line):
    global _model, _tok
    if _model is None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        name = str(model_dir())
        _tok = AutoTokenizer.from_pretrained(name)
        _model = AutoModelForSeq2SeqLM.from_pretrained(name)
        _model.eval()
        if torch.backends.mps.is_available():
            _model.to("mps")
    import torch
    ids = _tok(PROMPT.format(s=line), return_tensors="pt", truncation=True, max_length=256)
    ids = {k: v.to(_model.device) for k, v in ids.items()}
    with torch.no_grad():
        out = _model.generate(**ids, max_new_tokens=12)
    return _tok.decode(out[0], skip_special_tokens=True)


def release():
    """Drop the search model so CLIP or FLUX can use the same memory."""
    global _model, _tok
    _model = None
    _tok = None
    import gc
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass
