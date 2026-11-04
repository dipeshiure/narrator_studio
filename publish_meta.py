"""Publishing kit for finished videos: title, description, tags, thumbnail.

  venv/bin/python publish_meta.py <videos dir> <audio dir> [--watch] [--force] [--out DIR]

Public API (used by the server):

  publish_kit(video, audio, log=print, force=False, *, out_dir=None, model=None) -> dict | None

For a finished <videos dir>/<name>.mp4 (+ its source audio <name>.mp3):
  - reads the transcript (work dir cache of the audio)
  - asks the local LLM (Ollama) for EN/JA titles, descriptions, tags, hashtags, thumbnail phrases
  - runs quality gates on everything and re-asks the LLM with feedback when something fails
    (thumbnail phrase: up to 3 attempts, then a rule-based fallback that never fails)
  - writes <name>.publish.json, <name>.publish.md (copy-paste ready), <name>.thumb.jpg
Credits (speech source + Pexels footage) are always appended by code, not by the LLM.
What the gates did is recorded in meta["quality"].
"""
import argparse, json, re, subprocess, time, urllib.request
from collections import Counter
from functools import lru_cache
from pathlib import Path

import make_video as mv
import prep
import thumbs

FONT = "/System/Library/Fonts/Supplemental/Impact.ttf"
DICT = Path("/usr/share/dict/words")
MAX_ATTEMPTS = 3              # LLM attempts for the thumbnail phrase / titles
UNKNOWN_SOURCE = "要確認"

# ---------------------------------------------------------------- speech source
# (pattern, credit). Patterns are matched case-insensitively against the file name;
# the full names are also searched in the transcript.
SOURCES = [
    (r"eric thomas|\bet the hip ?hop preacher", "Eric Thomas"), (r"les brown", "Les Brown"),
    (r"goggins", "David Goggins"), (r"walter bond", "Walter Bond"), (r"freddy fri", "Freddy Fri"),
    (r"tom bilyeu", "Tom Bilyeu"), (r"muhammad ali|mohammed ali", "Muhammad Ali"),
    (r"tony robbins", "Tony Robbins"), (r"jim rohn", "Jim Rohn"), (r"inky johnson", "Inky Johnson"),
    (r"ray lewis", "Ray Lewis"), (r"kobe bryant", "Kobe Bryant"), (r"denzel washington", "Denzel Washington"),
    (r"will smith", "Will Smith"), (r"arnold schwarzenegger", "Arnold Schwarzenegger"),
    (r"jocko willink|\bjocko\b", "Jocko Willink"), (r"\bct fletcher", "CT Fletcher"),
    (r"billy alsbrooks", "Billy Alsbrooks"), (r"gary vaynerchuk|garyvee", "Gary Vaynerchuk"),
    (r"mel robbins", "Mel Robbins"), (r"steve jobs", "Steve Jobs"), (r"jordan peterson", "Jordan Peterson"),
    (r"nick vujicic", "Nick Vujicic"), (r"prince ea\b", "Prince Ea"), (r"coach pain", "Coach Pain"),
    (r"td jakes|t\.d\. jakes", "T.D. Jakes"), (r"matthew mcconaughey", "Matthew McConaughey"),
    (r"conor mcgregor", "Conor McGregor"), (r"jim carrey", "Jim Carrey"), (r"simon sinek", "Simon Sinek"),
    (r"dwayne johnson|the rock\b", "Dwayne Johnson"), (r"sylvester stallone", "Sylvester Stallone"),
    (r"brendon burchard", "Brendon Burchard"), (r"oprah", "Oprah Winfrey"), (r"ben lionel scott", "Ben Lionel Scott"),
    (r"fearless motivation", "Fearless Motivation"), (r"fearless so(ul)?\b", "Fearless Soul"),
    (r"motiversity", "Motiversity"),
]
# names that may be detected in the transcript (people only; channels never say their name)
TRANSCRIPT_NAMES = {"Eric Thomas", "Les Brown", "David Goggins", "Walter Bond", "Tom Bilyeu", "Tony Robbins",
                    "Jim Rohn", "Inky Johnson", "Ray Lewis", "Kobe Bryant", "Denzel Washington", "Jocko Willink",
                    "Billy Alsbrooks", "Gary Vaynerchuk", "Mel Robbins", "Jordan Peterson", "Nick Vujicic",
                    "Arnold Schwarzenegger", "Matthew McConaughey", "Conor McGregor", "Simon Sinek",
                    "Muhammad Ali", "Freddy Fri"}
_FEAT = re.compile(r"\b(?:ft\.?|feat\.?|featuring|by)\s+(.+)", re.I)
_FEAT_STOP = re.compile(r"\s+(?:-|–|—|\||\(|motivational|motivation\b|speech|music|video|remix|v\d)", re.I)


def guess_source(name, text=""):
    """All speakers / producers: known names in the file name, 'Ft. X & Y' / 'Featuring X' / 'by X'
    in the file name, known full names in the transcript. '' if nothing found."""
    found = []

    def add(n):
        n = n.strip(" .,-&")
        if n and n.lower() not in {f.lower() for f in found}:
            found.append(n)

    title = clean_title(name)
    hits = sorted((m.start(), credit) for rx, credit in SOURCES for m in [re.search(rx, title, re.I)] if m)
    for _, credit in hits:                           # in the order they appear in the name
        add(credit)
    m = _FEAT.search(title)
    if m:
        rest = m.group(1)
        for rx, _ in SOURCES:                        # known names were handled above
            rest = re.sub(rx, " & ", rest, flags=re.I)
        chunk = _FEAT_STOP.split(rest)[0]
        for part in re.split(r"\s*(?:&|,|\band\b|\+)\s*", chunk):
            words = part.split()
            if not 2 <= len(words) <= 3 or not all(re.fullmatch(r"[A-Za-z][\w'.]*", w) for w in words):
                continue                             # a person's name: 2-3 words
            add(" ".join(w if w.isupper() and len(w) <= 3 else w.capitalize() for w in words))
    low = (text or "").lower()
    for rx, credit in SOURCES:
        if credit in TRANSCRIPT_NAMES and credit.lower() in low:
            add(credit)
    return " & ".join(found)


def clean_title(stem):
    t = re.sub(r"^\d+_", "", str(stem))
    return re.sub(r"\s+", " ", t.replace("＂", '"')).strip()


def transcript(mp3):
    work = mv.work_dir(mp3)
    for name in ("words_original.json", "words_voice.json"):
        w = mv.read_json(work / name, None)
        if w:
            return " ".join(x["w"] for x in w)
    return ""


# ---------------------------------------------------------------- LLM
def _post(body):
    """The only HTTP call (mocked in tests). Returns Ollama's decoded JSON response."""
    req = urllib.request.Request(prep.OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=600))


def chat(prompt, model, temperature=0.4):
    """One JSON-mode chat turn -> parsed dict ({} if the model returned something else)."""
    r = _post({"model": model, "stream": False, "format": "json",
               "options": {"temperature": temperature, "num_ctx": 8192},
               "messages": [{"role": "user", "content": prompt}]})
    try:
        d = json.loads(r["message"]["content"])
    except (KeyError, TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


PROMPT = """You write YouTube publishing text for a motivational speech video.
Original file title: "{title}"
Speech transcript (may be cut short):
\"\"\"{text}\"\"\"

Write JSON with these keys:
- "title_en": catchy English YouTube title, max 70 characters. Use a concrete image, claim or phrase
  from THIS speech (not a generic "Never Give Up" / "Believe in Yourself" title). Do not start with
  "Motivational Speech". No fake claims.
- "title_ja": natural Japanese title, max 40 characters
- "description_en": 2 short paragraphs in English about the message of the speech (no invented quotes)
- "description_ja": the same description in natural Japanese
- "key_lines": 3 short powerful sentences copied EXACTLY from the transcript
- "tags": 12 English search tags (strings, no #)
- "hashtags": 5 hashtags starting with #
- "thumbnail_candidates": 5 different thumbnail phrases. Each one is a short, grammatical English
  command to the viewer: 2 to 4 common words, starts with a verb (or DON'T / NEVER / BE), complete
  (never ends with "the", "to", "your"...), no commas, no two nouns side by side, no made-up words.
  Build them from THIS speech's own main message and wording.
{avoid}Return only the JSON."""

THUMB_PROMPT = """You write the big text on a YouTube thumbnail for a motivational speech.
Original file title: "{title}"
Video title: "{video_title}"
Lines from the speech:
{lines}
Speech excerpt:
\"\"\"{text}\"\"\"

Give 5 NEW thumbnail phrases. Rules - every phrase must:
- be a short, grammatical English command to the viewer, 2 to 4 words
- start with a verb, or with DON'T / NEVER / BE / IT'S YOUR
- use only common real English words, no commas, no made-up words
- be complete (never end with "the", "to", "your", "of"...), never two nouns side by side
- say the main message of THIS speech (reuse its key words)
{feedback}{avoid}Return JSON: {{"thumbnail_candidates": ["...", "...", "...", "...", "..."]}}"""

TITLE_PROMPT = """You write YouTube titles for a motivational speech video.
Original file title: "{title}"
Lines from the speech:
{lines}
Speech excerpt:
\"\"\"{text}\"\"\"

The previous titles were rejected:
{feedback}
Write a better pair:
- "title_en": max 70 characters, with a concrete hook from THIS speech (an image, claim or phrase
  the speaker uses), not generic, does not start with "Motivational Speech"
- "title_ja": natural Japanese, max 40 characters
{avoid}Return JSON: {{"title_en": "...", "title_ja": "..."}}"""

RELEVANCE_PROMPT = """A motivational speech has this title: "{title}"
Lines from the speech:
{lines}
Excerpt: \"\"\"{text}\"\"\"

Does the thumbnail phrase "{phrase}" express the main message of THIS speech (not just a generic
motivational phrase, and not a literal misreading)? Return JSON: {{"fits": true}} or {{"fits": false}}"""


# ---------------------------------------------------------------- words
STOP = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "and", "or", "by", "with", "as", "from", "into",
        "is", "are", "was", "were", "be", "been", "am", "it", "it's", "its", "this", "that", "these", "those",
        "i", "me", "my", "you", "your", "yours", "yourself", "we", "our", "us", "they", "them", "their", "he",
        "him", "his", "she", "her", "what", "who", "how", "why", "when", "if", "but", "so", "not", "no", "do",
        "does", "did", "don't", "can", "can't", "will", "won't", "just", "all", "up", "out", "now", "get",
        "got", "have", "has", "had", "going", "gonna", "there", "here", "then", "than", "very", "one", "about",
        "like", "know", "say", "said", "want", "because", "would", "could", "should", "every", "more", "never",
        "i'm", "you're", "that's", "let", "let's", "make", "take", "keep", "stay", "go", "&", "vs", "through",
        "over", "until", "after", "before"}
# words that never end a thumbnail phrase
DANGLING_END = {"a", "an", "the", "to", "of", "for", "and", "or", "by", "with", "your", "my", "our", "their",
                "his", "her", "in", "on", "at", "from", "into", "than", "as", "is", "are", "be", "that", "this",
                "if", "but", "so", "very", "its", "whose", "will", "can", "don't"}
FUNCTION = DANGLING_END | {"it", "me", "you", "them", "us", "him", "yourself", "up", "out", "down", "back", "off",
                           "on", "away", "over", "through", "forward", "again", "now", "today", "all", "in",
                           "one", "more", "no", "every", "each", "some", "any", "ever", "always", "together",
                           "alone", "first", "last", "never", "not", "here", "there", "anyway", "harder",
                           "further", "higher", "daily", "big", "hard", "long", "wrong", "right", "deep",
                           "around", "beyond", "past", "until", "what", "who", "how", "why"}
CONTRACTIONS = {"don't", "won't", "can't", "it's", "you're", "they're", "we're", "i'm", "you'll", "that's",
                "let's", "isn't", "aren't", "ain't", "didn't", "you've", "we'll", "i'll", "doesn't", "wasn't"}
# imperative verbs a thumbnail phrase may start with (curated: ambiguous noun-like words such as
# "discipline", "strap", "float", "head" are deliberately missing)
VERBS = set("""be stay keep get go make take give find face fight prove raise rise build chase dare run push start
stop believe trust own earn win play work outwork dream live love embrace choose create seize master control
leave stand show hold break become focus finish do wake step walk climb change defy conquer unleash ignite fuel
protect remember forget learn grow lead look see feel decide commit sacrifice endure survive outlast overcome
attack pursue hunt crush beat defeat demand expect define set aim reach pay bet invest turn move carry lift train
bleed suffer speak tell hustle grind respect honor claim fix write rewrite open shine act think breathe accept
forgive help serve try begin return come bring put hit burn light spark guard defend discover unlock release
free rebuild restart refuse reject ignore silence shut quit imagine envision plan prepare practice repeat
outrun outthink outsmart outgrow fall fail cut drop clear shape forge sharpen toughen strengthen deserve count
risk jump leap fly soar roar charge march wait slow rest pray hope listen hear watch last level dig double
answer kill bury float sting let let's never don't it's""".split())
ADJ_SUFFIX = ("ive", "ful", "ous", "al", "ic", "ible", "able", "est", "less", "ent", "ant", "ary", "ed")
ADJ = {"big", "hard", "full", "true", "real", "strong", "new", "great", "bold", "raw", "pure", "extra", "daily",
       "hungry", "humble", "brave", "tough", "calm", "free", "wild", "fierce", "fearless", "relentless", "small",
       "good", "best", "bad", "own", "whole", "deep", "high", "every", "first", "last", "next", "odd", "ready"}
NESS_OK = {"greatness", "toughness", "weakness", "darkness", "happiness", "kindness", "fitness", "madness",
           "business", "boldness", "forgiveness", "awareness", "readiness", "willingness", "fearlessness",
           "sadness", "loneliness", "goodness", "fairness", "consciousness", "hardness", "selfishness",
           "witness", "harshness", "illness", "emptiness", "uniqueness", "stillness"}
# too literal / misleading on a thumbnail unless the original title itself is about it
LITERAL = {"muscle", "muscles", "gym", "body", "abs", "biceps", "sweat", "diet", "protein", "lion", "lions",
           "wolf", "storm", "eagle"}
# phrases used as examples in prompts / known LLM favourites: need a real link to the speech
EXAMPLES = {"STAY DISCIPLINED", "GET BACK UP", "BE A LION", "NEVER GIVE UP", "BELIEVE IN YOURSELF",
            "KEEP GOING", "STAY STRONG", "UNLEASH YOUR POTENTIAL"}
# words that make a title or thumbnail generic (no concrete hook)
GENERIC = {"motivat", "speech", "inspir", "success", "succeed", "dream", "believ", "life", "power", "potential",
           "unleash", "inner", "achiev", "mindset", "journey", "goal", "great", "best", "never", "give", "up",
           "quit", "strong", "strength", "keep", "go", "push", "forward", "rise", "win", "champion", "beast",
           "unstoppable", "unyield", "epic", "today", "now", "way", "true", "path", "embrace", "overcom",
           "challeng", "obstacl", "fearless", "within", "yourself", "spirit", "path", "tip", "matter", "key"}
BOILERPLATE = re.compile(r"\b(motivational (speech|video|music)|fearless motivation|motivation|motivational|"
                         r"inspirational video|youtube|remix|epic|v\d(\.\d)?|official|powerful|ft\.?.*|"
                         r"featuring.*|by .*|life changing|must listen|must watch)\b", re.I)


@lru_cache(maxsize=1)
def dictionary():
    try:
        return {w.strip().lower() for w in DICT.read_text().split()}
    except OSError:
        return set()


def norm_words(text):
    return re.sub(r"[^A-Za-z' ]", " ", str(text).replace("’", "'").replace("‘", "'")).split()


def stem(w):
    """Crude stemmer for comparing phrases (toughness ~ tough, mentally ~ mental, quitting ~ quit)."""
    w = w.lower().strip("'")
    for suf in ("fulness", "ness", "ments", "ment", "ingly", "edly", "ing", "ies", "ied", "est", "ers",
                "er", "ed", "ly", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":       # quitt -> quit
        w = w[:-1]
    if w.endswith("i") and len(w) > 3:                                   # happi -> happy
        w = w[:-1] + "y"
    return w.rstrip("e") if len(w) > 4 else w


def is_word(w, spoken=frozenset()):
    """Real English word? Dictionary + simple inflections; contractions and curated words allowed.
    Odd -ness nouns ("strongness") only count when the speaker actually said them."""
    w = w.lower().strip("'")
    if not w or not re.fullmatch(r"[a-z']+", w):
        return False
    if w in CONTRACTIONS or w in VERBS or w in FUNCTION or w in ADJ or w in STOP:
        return True
    if w.endswith("ness") and w not in NESS_OK:
        return w in spoken
    words = dictionary()
    if not words:
        return True                    # no word list on this system: do not block
    if w in words:
        return True
    cands = []
    for suf, rep in (("ies", "y"), ("ied", "y"), ("ier", "y"), ("iest", "y"), ("ily", "y"), ("es", ""),
                     ("s", ""), ("ed", ""), ("ed", "e"), ("d", ""), ("ing", ""), ("ing", "e"), ("er", ""),
                     ("er", "e"), ("r", ""), ("est", ""), ("est", "e"), ("st", ""), ("ly", ""), ("ally", ""),
                     ("ness", ""), ("ves", "f"), ("ves", "fe")):
        if w.endswith(suf):
            b = w[:-len(suf)] + rep
            cands.append(b)
            if len(b) > 2 and b[-1] == b[-2]:               # quitting -> quitt -> quit
                cands.append(b[:-1])
    return any(len(b) >= 2 and b in words for b in cands) or w in spoken


def content_stems(text, drop=STOP):
    return {stem(w) for w in norm_words(text) if w.lower() not in drop and len(w) > 2}


# words that do not make two phrases different ("STAY STRONG" = "BE STRONGER", "FIGHT BACK" = "KEEP FIGHTING")
WEAK = {"be", "become", "stay", "keep", "get", "now", "give", "find", "show", "do", "go", "make", "take", "start",
        "back", "up", "on", "through", "out", "down", "off", "away", "forward", "again", "today", "always",
        "never", "don't", "it's", "let's", "every", "day", "all", "more"}


def phrase_key(text):
    """Canonical form for near-duplicate detection: content stems without filler words."""
    return frozenset(stem(w) for w in norm_words(text) if w.lower() not in STOP and w.lower() not in WEAK)


def near_duplicate(a, b):
    """Same words, or the same content words once filler verbs/particles are ignored."""
    if " ".join(norm_words(a)).upper() == " ".join(norm_words(b)).upper():
        return True
    ka, kb = phrase_key(a), phrase_key(b)
    return bool(ka) and ka == kb


def tags_length(tags):
    """YouTube's count: characters + commas, tags with spaces count their quotes too."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


# ---------------------------------------------------------------- context
class Context:
    """Everything the gates need to know about one video and its siblings."""

    def __init__(self, title, text, key_lines=(), used_thumbs=(), used_titles=()):
        self.title = title
        self.text = text
        self.key_lines = list(key_lines)
        self.used_thumbs = [t for t in used_thumbs if t]
        self.used_titles = [t for t in used_titles if t]
        self.spoken = {w.lower().strip("'") for w in norm_words(text)}
        self.title_stems = content_stems(title)
        self.video_title = title
        self.source = ""
        self.refresh()

    def refresh(self):
        counts = Counter(stem(w) for w in norm_words(self.text) if w.lower() not in STOP and len(w) > 3)
        top = {w for w, _ in counts.most_common(30)}
        self.vocab = self.title_stems | top | set().union(*[content_stems(k) for k in self.key_lines] or [set()])


def siblings(dirs, exclude_stem):
    """thumbnail_text / title_en of other videos' publish.json files in these folders."""
    th, ti, seen = [], [], set()
    for d in dirs:
        for p in sorted(Path(d).glob("*.publish.json")):
            stem_ = p.name[:-len(".publish.json")]
            if stem_ == exclude_stem or stem_ in seen:
                continue
            seen.add(stem_)
            m = mv.read_json(p, {}) or {}
            th.append(str(m.get("thumbnail_text") or ""))
            ti.append(str(m.get("title_en") or ""))
    return th, ti


# ---------------------------------------------------------------- gates
def thumb_text(raw):
    return " ".join(norm_words(raw)).upper()


def thumb_problem(raw, ctx, need_relevance=True):
    """None if the phrase is a good thumbnail text, else a short reason. Relevance by content-word
    overlap only (the LLM relevance check is done by the caller for phrases that fail just that)."""
    text = thumb_text(raw)
    words = [w.lower() for w in text.split()]
    if not 2 <= len(words) <= 4:
        return "must be 2-4 words"
    bad = [w for w in words if not is_word(w, ctx.spoken)]
    if bad:
        return f"not a real English word: {bad[0].upper()}"
    if words[-1] in DANGLING_END:
        return f"ends with a dangling '{words[-1].upper()}'"
    if words[0] in ("don't", "never"):
        rest = words[1:]
        if not rest or rest[0] not in VERBS - {"never", "don't", "it's"}:
            return f"{words[0].upper()} must be followed by a verb"
        rest = rest[1:]
    elif words[0] in ("it's", "let's"):
        rest = []
    elif words[0] in VERBS:
        rest = words[1:]
    else:
        return f"does not start with a verb ({words[0].upper()})"
    if len(rest) >= 2 and not any(w in FUNCTION for w in rest):
        first = rest[0]
        if not (first in ADJ or first.endswith(ADJ_SUFFIX) or first.endswith("ly")):
            return "not a phrase (bare words strung together)"
    if len(words) == 2 and words[0] == "be" and words[1] not in ADJ and not words[1].endswith(ADJ_SUFFIX) \
            and not words[1].endswith(("y", "ish", "ing", "en", "ate", "ile", "ine", "ar", "er")) and words[1] not in FUNCTION:
        return "BE + noun without an article"
    lit = [w for w in words if w in LITERAL and stem(w) not in ctx.title_stems]
    if lit:
        return f"too literal / off-topic word: {lit[0].upper()}"
    for u in ctx.used_thumbs:
        if near_duplicate(text, u):
            return f"already used by another video ({thumb_text(u)})"
    if need_relevance and not relevant(text, ctx):
        return "not clearly about this speech"
    return None


def relevant(text, ctx):
    stems = content_stems(text, drop=STOP | {"be", "do", "it's", "don't"})
    if text in EXAMPLES:              # stock phrases need a link to the title or the key lines
        strong = ctx.title_stems | set().union(*[content_stems(k) for k in ctx.key_lines] or [set()])
        return bool(stems & strong)
    return bool(stems & ctx.vocab)


def is_generic(s):
    return any(s == g if len(g) <= 4 else s.startswith(g) for g in GENERIC)


def title_problem(t, ctx):
    t = str(t or "").strip()
    if not t:
        return "empty title"
    if len(t) > 70:
        return f"too long ({len(t)} > 70 characters)"
    if re.match(r"\s*motivational\s+(speech|video)", t, re.I):
        return "starts with 'Motivational Speech'"
    hooks = {stem(w) for w in norm_words(t) if w.lower() not in STOP and len(w) > 2
             and not is_generic(w.lower()) and not is_generic(stem(w))}
    if not hooks:
        return "too generic (no concrete hook from the speech)"
    if not hooks & ctx.vocab:
        return "no concrete hook from the speech (shares no key word with it)"
    names = [c for rx, c in SOURCES if re.search(rx, t, re.I) and c.lower() not in ctx.source.lower()]
    if names:
        return f"names {names[0]}, who is not the detected speech source"
    for u in ctx.used_titles:
        a, b = content_stems(t), content_stems(u)
        if t.lower() == u.lower() or (a and b and len(a & b) / len(a | b) >= 0.75):
            return f"too close to another video's title ({u})"
    return None


def title_ja_problem(t):
    t = str(t or "").strip()
    if not re.search(r"[぀-ヿ一-鿿]", t):
        return "title_ja is not Japanese"
    if len(t) > 40:
        return f"title_ja too long ({len(t)} > 40)"
    return None


# ---------------------------------------------------------------- fallbacks
TITLE_RULES = [  # (regex on the original title, phrases) - first unused passing phrase wins
    (r"they were wrong|haters|doubt", ["PROVE THEM WRONG", "SILENCE THE DOUBTERS"]),
    (r"you have to be (\w+)", ["DARE TO BE {0}", "BE {0}"]),
    (r"quit", ["DON'T QUIT NOW", "NEVER QUIT", "DON'T STOP NOW"]),
    (r"\bgreatest\b", ["BE THE GREATEST"]),
    (r"time for|your time", ["IT'S YOUR TIME", "SEIZE YOUR TIME"]),
    (r"mental(ly)? tough", ["BE MENTALLY TOUGH", "TOUGHEN YOUR MIND"]),
    (r"disciplin", ["STAY DISCIPLINED", "EARN IT DAILY"]),
    (r"greatness", ["CHASE GREATNESS", "BE GREAT"]),
    (r"focus", ["STAY FOCUSED", "LOCK IN NOW"]),
    (r"revival|comeback|come back", ["RISE AGAIN", "COME BACK STRONGER"]),
    (r"\bfall\b|fail", ["GET BACK UP", "FAIL FORWARD"]),
    (r"dream", ["CHASE YOUR DREAM", "DREAM BIGGER"]),
    (r"fear", ["FACE YOUR FEAR", "DO IT ANYWAY"]),
    (r"regret|too late", ["LIVE WITHOUT REGRET", "START TODAY"]),
    (r"opportunit", ["SEIZE THE MOMENT"]),
    (r"action", ["TAKE ACTION NOW"]),
    (r"goal", ["SET BIGGER GOALS"]),
    (r"responsib", ["OWN YOUR LIFE"]),
    (r"unstoppable", ["BE UNSTOPPABLE"]),
    (r"hard times|tough", ["OUTLAST THE STORM", "GET TOUGHER"]),
    (r"struggle", ["EMBRACE THE STRUGGLE"]),
    (r"integrity|my word", ["KEEP YOUR WORD"]),
    (r"remember|legacy|mark", ["LEAVE A LEGACY"]),
    (r"warrior", ["BE A WARRIOR"]),
    (r"believe", ["BELIEVE IN YOURSELF", "TRUST YOURSELF"]),
    (r"effort|work", ["OUTWORK EVERYONE", "GIVE FULL EFFORT"]),
    (r"\bwin\b|victory|champion", ["PLAY TO WIN", "WIN THE DAY"]),
    (r"fight|war|battle", ["FIGHT BACK", "KEEP FIGHTING"]),
    (r"best version|best", ["BE YOUR BEST"]),
    (r"ready|prepare", ["STAY READY"]),
]
STRONG = ["OUTWORK EVERYONE", "EARN YOUR PLACE", "RISE AGAIN", "DO THE WORK", "SHOW UP DAILY", "STAY HUNGRY",
          "OWN YOUR FUTURE", "BET ON YOURSELF", "EMBRACE THE PAIN", "GO THE DISTANCE", "TRUST THE PROCESS",
          "OUTLAST THEM ALL", "CHOOSE TO WIN", "START RIGHT NOW", "BREAK YOUR LIMITS", "DO IT ANYWAY",
          "PUSH PAST PAIN", "EARN IT DAILY", "STAND YOUR GROUND", "MAKE IT COUNT", "PROVE THEM WRONG",
          "STAY HUMBLE", "KEEP CLIMBING", "WIN THE DAY", "DEMAND MORE", "LEVEL UP NOW", "FINISH STRONG",
          "LOCK IN NOW", "DIG DEEPER", "OWN THE MOMENT"]


def title_candidates(ctx):
    """Imperatives derived from the original file title (rules + the title itself when it already
    starts with a verb), only those passing the format + duplicate gates."""
    low = ctx.title.lower()
    cands = []
    head = re.split(r"[-–—|:(!?►]", ctx.title)[0]
    words = norm_words(head)
    if words and words[0].lower() in VERBS:
        words = words[:4]
        while len(words) > 2 and words[-1].lower() in DANGLING_END:
            words.pop()
        cands.append(" ".join(words).upper())
    for rx, phrases in TITLE_RULES:
        m = re.search(rx, low)
        if m:
            cands += [p.format(*[g.upper() for g in m.groups() if g]) if "{" in p else p for p in phrases]
    out = []
    for c in cands:
        c = thumb_text(c)
        if c not in out and not thumb_problem(c, ctx, need_relevance=False):
            out.append(c)
    return out


def fallback_thumb(ctx):
    """Phrase that passes the format + duplicate gates without the LLM. Never fails."""
    cands = title_candidates(ctx)
    if cands:
        return cands[0]
    ranked = sorted(STRONG, key=lambda p: not relevant(p, ctx))     # related ones first, stable
    for c in ranked:
        if not thumb_problem(c, ctx, need_relevance=False):
            return thumb_text(c)
    return ranked[0]


def fallback_title(ctx, ja_cands=()):
    t = BOILERPLATE.sub(" ", ctx.title.replace('"', ""))
    t = re.sub(r"[\[\]()►|_]+|\s-\s|\s+-\s*$", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -&")
    if not t:
        t = "Motivation"
    if t.isupper() or t.islower():
        t = " ".join(w if w in ("I", "&") else (w.lower() if i and w.lower() in thumbs.SMALL else w.capitalize())
                     for i, w in enumerate(t.split()))
    if len(t) > 70:
        t = t[:70].rsplit(" ", 1)[0]
    ja = next((j.strip()[:40] for j in ja_cands if not title_ja_problem(str(j)[:40])), "")
    return t, ja or "心に響くモチベーションスピーチ"


# ---------------------------------------------------------------- generation steps
def _lines_block(ctx):
    return "\n".join(f"- {k}" for k in ctx.key_lines) or "- (none)"


def _avoid(ctx, titles=False):
    used = sorted({thumb_text(u) for u in ctx.used_thumbs if u})
    s = f"Already used by other videos (do not repeat or paraphrase): {', '.join(used)}\n" if used else ""
    if titles and ctx.used_titles:
        s += "Other videos' titles (do not copy): " + " / ".join(ctx.used_titles[-40:]) + "\n"
    return s


def choose_thumbnail(first_cands, ctx, model, log=print):
    """Pick the thumbnail phrase. Returns (text, quality). Order of preference per attempt:
      1. an LLM candidate passing every gate and sharing a key word with the speech
      2. an imperative derived from the original title (e.g. "THEY WERE WRONG" -> "PROVE THEM WRONG")
      3. an LLM candidate passing every gate except the key-word overlap, approved by an LLM yes/no check
    then re-ask the LLM with the rejection reasons (MAX_ATTEMPTS in total), then a curated phrase."""
    q = {"thumbnail_attempts": 0, "rejected": [], "fallback": False, "picked_by": "llm"}
    cands = first_cands
    llm_checks = 0
    excerpt = " ".join(ctx.text.split()[:450])
    from_title = title_candidates(ctx)
    for attempt in range(MAX_ATTEMPTS):
        if attempt:          # re-ask with the reasons
            fb = "These phrases were rejected - do not repeat them:\n" + "\n".join(
                f"- {r}" for r in q["rejected"][-12:]) + "\n"
            try:
                d = chat(THUMB_PROMPT.format(title=ctx.title, video_title=ctx.video_title, lines=_lines_block(ctx),
                                             text=excerpt, feedback=fb, avoid=_avoid(ctx)), model, 0.7)
                cands = d.get("thumbnail_candidates") or d.get("candidates") or []
            except Exception as e:
                log(f"  ! LLM error ({e.__class__.__name__}) while asking for thumbnail text")
                cands = []
        q["thumbnail_attempts"] += 1
        if isinstance(cands, str):
            cands = [cands]
        maybe = []
        for c in [c for c in cands if isinstance(c, str)][:8]:
            text = thumb_text(c)
            why = thumb_problem(text, ctx, need_relevance=False)
            if not why and not relevant(text, ctx):
                maybe.append(text)
                why = "not clearly about this speech"
            if why is None:
                return text, q
            q["rejected"].append(f"{text or repr(c)}: {why}")
        if from_title:
            q.update(picked_by="title_rule", fallback=True)
            return from_title[0], q
        for text in maybe:      # no shared key word: let the LLM judge (bounded)
            if llm_checks >= 3:
                break
            llm_checks += 1
            try:
                ok = chat(RELEVANCE_PROMPT.format(title=ctx.title, lines=_lines_block(ctx), text=excerpt[:1500],
                                                  phrase=text), model, 0).get("fits")
            except Exception:
                ok = False
            if ok is True or str(ok).lower() == "true":
                q["rejected"] = [r for r in q["rejected"] if not r.startswith(text + ":")]
                q["picked_by"] = "llm_checked"
                return text, q
    q.update(fallback=True, picked_by="curated")
    return fallback_thumb(ctx), q


def choose_titles(data, ctx, model, log=print):
    """Gate title_en / title_ja; re-ask with feedback; fall back to the cleaned original title."""
    q = {"title_attempts": 1, "title_rejected": [], "title_fallback": False}
    en, ja = str(data.get("title_en") or "").strip(), str(data.get("title_ja") or "").strip()
    ja_seen = [ja]
    excerpt = " ".join(ctx.text.split()[:450])
    for attempt in range(MAX_ATTEMPTS):
        p_en, p_ja = title_problem(en, ctx), title_ja_problem(ja)
        if not p_en and not p_ja:
            return en, ja, q
        if p_en:
            q["title_rejected"].append(f"{en}: {p_en}")
        if p_ja:
            q["title_rejected"].append(f"{ja}: {p_ja}")
        if attempt == MAX_ATTEMPTS - 1:
            break
        q["title_attempts"] += 1
        try:
            d = chat(TITLE_PROMPT.format(title=ctx.title, lines=_lines_block(ctx), text=excerpt,
                                         feedback="\n".join(f"- {r}" for r in q["title_rejected"][-6:]),
                                         avoid=_avoid(ctx, titles=True)), model, 0.6)
        except Exception as e:
            log(f"  ! LLM error ({e.__class__.__name__}) while asking for titles")
            d = {}
        new_en, new_ja = str(d.get("title_en") or "").strip(), str(d.get("title_ja") or "").strip()
        if p_en and new_en:
            en = new_en
        # keep EN and JA a pair: a new English title brings its own Japanese one
        if new_ja and (p_ja or (p_en and new_en and not title_ja_problem(new_ja))):
            ja = new_ja
            ja_seen.append(ja)
    if title_problem(en, ctx):
        en, _ = fallback_title(ctx)
        q["title_fallback"] = True
    if title_ja_problem(ja):
        ja = fallback_title(ctx, ja_seen)[1]
        q["title_fallback"] = True
    return en, ja, q


def strip_fake_quotes(desc, text):
    """Remove quotation marks around quoted passages that do not occur in the transcript."""
    n = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower().replace("’", "'"))
    nt = re.sub(r"\s+", " ", n(text))
    removed = []

    def fix(m):
        inner = m.group(2)
        if len(inner.split()) >= 3 and re.sub(r"\s+", " ", n(inner)).strip() not in nt:
            removed.append(inner)
            return inner
        return m.group(0)
    out = re.sub(r"([“\"「])([^”\"」]{3,300})([”\"」])", fix, desc)
    return out, removed


def clean_tags(raw, source, thumb):
    tags, seen = [], set()
    extra = [s.lower() for s in source.split(" & ") if s and s != UNKNOWN_SOURCE]
    defaults = ["motivational speech", "motivation", "inspiration", "self improvement", "mindset", "success",
                "discipline", "motivational video", "personal growth", "never give up", thumb.lower()]
    items = raw if isinstance(raw, list) else re.split(r"[,\n]", str(raw or ""))
    for t in [*items, *extra]:
        t = re.sub(r"\s+", " ", re.sub(r"[#\"<>,]", " ", str(t))).strip().lower()
        if 2 <= len(t) <= 30 and t not in seen:
            seen.add(t); tags.append(t)
    tags = tags[:15]
    for d in defaults:
        if len(tags) >= 10:
            break
        if d not in seen:
            seen.add(d); tags.append(d)
    while tags_length(tags) > 500 and len(tags) > 10:
        tags.pop()
    while tags_length(tags) > 500:
        tags.pop(max(range(len(tags)), key=lambda i: len(tags[i])))
    return [as_hashtag(t) for t in tags]


def as_hashtag(tag):
    """'motivational speech' -> '#MotivationalSpeech' (tags are shown with # as the user asked)."""
    tag = str(tag).strip()
    if tag.startswith("#"):
        tag = tag[1:]
    words = re.findall(r"[A-Za-z0-9']+", tag)
    if not words:
        return ""
    return "#" + "".join(w[:1].upper() + w[1:] for w in words).replace("'", "")


def clean_hashtags(raw, thumb):
    out = []
    items = raw if isinstance(raw, list) else str(raw or "").split()
    for h in items:
        h = re.sub(r"[^\w]", "", str(h).replace(" ", ""))
        if h and len(h) <= 30 and ("#" + h).lower() not in {x.lower() for x in out}:
            out.append("#" + h)
    if len(out) < 3:
        for h in ("#Motivation", "#MotivationalSpeech", "#" + "".join(w.capitalize() for w in thumb.split())):
            if h.lower() not in {x.lower() for x in out}:
                out.append(re.sub(r"[^\w#]", "", h))
    return out[:5]


# ---------------------------------------------------------------- outputs
def thumbnail(video, text, dest):
    """Fallback thumbnail: frame from the brightest part of the first third + big outlined caption."""
    dur = mv.duration(video)
    best, best_t = -999, dur * 0.2
    for t in (dur * f for f in (0.12, 0.2, 0.28, 0.36)):
        p = dest.with_suffix(".probe.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", str(p)])
        out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(p), "-vf", "signalstats,metadata=print", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        m = re.search(r"YAVG=([\d.]+)", out)
        if m and float(m.group(1)) > best:
            best, best_t = float(m.group(1)), t
        p.unlink(missing_ok=True)
    words = text.upper().split()
    lines = [" ".join(words[:2]), " ".join(words[2:])] if len(words) > 2 else [" ".join(words)]
    draw = []
    for k, line in enumerate(l for l in lines if l):
        safe = line.replace("\\", "").replace("'", "’").replace(":", "\\:").replace("%", "\\%")
        y = f"h*0.62+{k}*170" if len(lines) > 1 else "h*0.70"
        draw.append(f"drawtext=fontfile={FONT}:text='{safe}':fontsize=170:fontcolor=white:"
                    f"borderw=10:bordercolor=black:x=(w-text_w)/2:y={y}")
    vf = "eq=brightness=0.04:contrast=1.15:saturation=1.1," + ",".join(draw)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{best_t:.2f}", "-i", str(video), "-frames:v", "1",
                    "-vf", vf, "-q:v", "2", str(dest)], check=True)


def markdown(meta):
    tags = ", ".join(meta["tags"])
    q = meta.get("quality") or {}
    check = "\n\n> 要確認: 音声の出典(Speech)を確認して説明文に記載してください" \
        if meta.get("speech_source") == UNKNOWN_SOURCE else ""
    return f"""# {meta['title_en']}

## Title (EN)
{meta['title_en']}

## タイトル (JA)
{meta['title_ja']}

## Description (EN)
{meta['description_en_full']}

## 説明文 (JA)
{meta['description_ja_full']}

## Tags
{tags}

## Thumbnail
{meta['thumbnail']}  (text: {meta['thumbnail_text']})

## Quality
thumbnail attempts: {q.get('thumbnail_attempts', '-')}, fallback: {q.get('fallback', False)}, \
title fallback: {q.get('title_fallback', False)}{check}
"""


REQUIRED = ("title_en", "title_ja", "description_en", "description_ja", "tags")


def publish_kit(video, audio, log=print, force=False, *, out_dir=None, model=None):
    """Build publish.json + publish.md + thumbnail for one finished video.

    video: <name>.mp4, audio: the source audio (its work-dir cache holds the transcript).
    Idempotent: returns the existing meta if <name>.publish.json exists (unless force).
    out_dir: where to write (default: next to the video). Writing elsewhere is a preview:
    Pexels photos are not claimed in 素材/photos/used.json.
    Returns the meta dict, or None if there is no transcript / the LLM is unreachable."""
    video, audio = Path(video), Path(audio)
    out = Path(out_dir) if out_dir else video.parent
    out.mkdir(parents=True, exist_ok=True)
    pj = out / (video.stem + ".publish.json")
    if pj.exists() and not force:
        existing = mv.read_json(pj, None)
        if existing:
            return existing
    model = model or mv.load_settings()["llm_model"]
    title = clean_title(video.stem)
    text = transcript(audio)
    if not text:
        log(f"  ! no transcript for {video.name}"); return None
    used_thumbs, used_titles = siblings([out, video.parent], video.stem)
    ctx = Context(title, text, used_thumbs=used_thumbs, used_titles=used_titles)
    source = guess_source(video.stem, text) or UNKNOWN_SOURCE
    ctx.source = source
    data = {}
    for attempt in range(3):
        try:
            data = chat(PROMPT.format(title=title, text=" ".join(text.split()[:1200]),
                                      avoid=_avoid(ctx, titles=True)), model)
            if all(data.get(k) for k in REQUIRED):
                break
        except Exception as e:
            log(f"  ! LLM error ({e.__class__.__name__}), retry")
            time.sleep(3)
    else:
        log(f"  ! could not generate text for {video.name}"); return None

    # quotes: keep only lines that really occur in the transcript
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower().replace("’", "'"))
    nt = re.sub(r"\s+", " ", norm(text))
    lines = [q.strip().strip('"“”') for q in data.get("key_lines") or [] if isinstance(q, str)]
    lines = [q for q in lines if len(q.split()) >= 3 and re.sub(r"\s+", " ", norm(q)).strip() in nt][:3]
    ctx.key_lines = lines
    ctx.refresh()

    title_en, title_ja, tq = choose_titles(data, ctx, model, log)
    ctx.video_title = title_en
    first = data.get("thumbnail_candidates") or []
    if isinstance(first, str):
        first = [first]
    if data.get("thumbnail_text"):
        first = [*first, data["thumbnail_text"]]
    thumb, quality = choose_thumbnail(first, ctx, model, log)
    quality.update(tq)

    # no credit / tool lines in the descriptions (user's choice); the speech source stays in
    # meta["speech_source"] and the posting text, not in the copy-paste description
    credit_en = credit_ja = ""
    quotes_en = ("\n\nKey lines:\n" + "\n".join(f"- “{q}”" for q in lines)) if lines else ""
    quotes_ja = ("\n\n印象的な言葉:\n" + "\n".join(f"- “{q}”" for q in lines)) if lines else ""
    desc_en, fake_en = strip_fake_quotes(str(data["description_en"]).strip(), text)
    desc_ja, fake_ja = strip_fake_quotes(str(data["description_ja"]).strip(), text)
    if fake_en or fake_ja:
        quality["unverified_quotes_removed"] = fake_en + fake_ja
    hashtags = clean_hashtags(data.get("hashtags"), thumb)
    tail_en = f"{quotes_en}\n\n{' '.join(hashtags)}{credit_en}"
    tail_ja = f"{quotes_ja}\n\n{' '.join(hashtags)}{credit_ja}"
    desc_en = desc_en[:max(200, 5000 - len(tail_en))].rstrip()
    desc_ja = desc_ja[:max(200, 5000 - len(tail_ja))].rstrip()
    meta = {
        "video": video.name, "source_title": title, "speech_source": source,
        "title_en": title_en, "title_ja": title_ja,
        "description_en_full": f"{desc_en}{tail_en}"[:5000],
        "description_ja_full": f"{desc_ja}{tail_ja}"[:5000],
        "key_lines": lines,
        "tags": clean_tags(data.get("tags"), source, thumb),
        "hashtags": hashtags,
        "thumbnail_text": thumb,
        "thumbnail": video.stem + ".thumb.jpg",
        "quality": quality,
    }
    dest = out / meta["thumbnail"]
    preview = out.resolve() != video.parent.resolve()
    try:   # Motiversity-style Pexels photo thumbnail; old frame+caption one only as fallback
        thumbs.make_thumbnail(meta, dest, claim_photos=not preview)
    except Exception as e:
        log(f"  ! photo thumbnail failed ({e.__class__.__name__}: {e}), using a video frame")
        thumbnail(video, meta["thumbnail_text"], dest)
    mv.write_json(pj, meta)
    (out / (video.stem + ".publish.md")).write_text(markdown(meta))
    log(f"  + {video.name}: {meta['title_en']} [{thumb}]")
    return meta


def process(video, audio_dir, model, log=print):
    """Old entry point (kept for callers): True if a publishing kit was written."""
    return publish_kit(video, Path(audio_dir) / (Path(video).stem + ".mp3"), log, force=True, model=model) is not None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("videos"); ap.add_argument("audio")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--force", action="store_true", help="rebuild even if publish.json exists")
    ap.add_argument("--out", help="write the kits here instead of next to the videos (preview)")
    ap.add_argument("--only", help="only videos whose name starts with one of these comma-separated prefixes")
    ap.add_argument("--model", default=mv.load_settings()["llm_model"])
    a = ap.parse_args()
    vdir, adir = Path(a.videos).expanduser(), Path(a.audio).expanduser()
    out = Path(a.out).expanduser() if a.out else None
    prefixes = tuple(a.only.split(",")) if a.only else None
    done = set()
    while True:
        for v in sorted(vdir.glob("*.mp4")):
            if (prefixes and not v.name.startswith(prefixes)) or v in done:
                continue
            publish_kit(v, adir / (v.stem + ".mp3"), log=lambda m: print(m, flush=True), force=a.force,
                        out_dir=out, model=a.model)
            if a.force:
                done.add(v)          # --force --watch: rebuild each video once per run
        if not a.watch:
            break
        time.sleep(30)
