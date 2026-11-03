"""Turn a scrawled email into tidy, separate, sorted reminders.

No AI model is needed: this is rules + word lists, which run instantly on a
Raspberry Pi 3B+.  The pipeline for one email body is

    clean_body  ->  split_blocks  ->  split_actions  ->  extract_deadline
                ->  fix_text (spelling / grammar / punctuation)  ->  sort_items
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from . import icons as icon_lib
from .dates import Deadline, extract_deadline

# --------------------------------------------------------------------------
# Word lists
# --------------------------------------------------------------------------

# Verbs that typically start a task.  A comma/"and" is only treated as a
# boundary between two reminders when the words after it start with one of these,
# which is what keeps "buy milk and eggs" together but splits
# "wash dishes and walk the dog".
VERBS = set("""
add agree allow answer apply arrange ask attend bake bath bathe bring book borrow brush buy
call cancel charge check chase clean clear close collect complete confirm contact cook
copy create cut deliver design discuss do download draft drop dry email empty enrol enroll
enter feed file fill find finish fix fold follow forward gather get give go grab hang
hire iron invite keep kick label launch leave let lock look mail make mow meet message mop move
note order organise organize pack paint pay pick plan plant plug post practice practise
prepare print put quote read recharge record refill register remind remove renew rent
repair reply report request reschedule research reserve respond return review ring run
schedule send set sew shave shop shred sign sort speak stop submit swap take talk text
thank throw tidy top transfer try turn type unpack update upload use vacuum visit wait
wake walk wash watch water wipe wrap write
""".split())

# Opening phrases that are noise once the text becomes a printed reminder.
_FILLER_START = re.compile(
    r"^(?:(?:please|pls|plz|also|and|then|oh and|ok|okay|ok so|so|reminder|reminders|todo|to do|to-do|note to self)\b[\s:,\-]*)+",
    re.I)
_INTENT_START = re.compile(
    r"^(?:(?:remind me to|remind me|remember to|don'?t forget to|do not forget to|dont forget to|"
    r"i (?:really )?(?:need|have|got|must|should|want|gotta) to|i'?ve got to|we (?:need|have|should) to|"
    r"need to|needs to|have to|got to|gotta|must|should|make sure (?:to|you|i|we)|"
    r"can you|could you|would you|will you|pls|please)\s+)+", re.I)

# Words before an unfamiliar lowercase word that suggest it is a person's name.
_PERSON_CONTEXT = {"call", "phone", "ring", "text", "email", "message", "msg", "ask",
                   "tell", "meet", "thank", "visit", "ping", "with", "from", "cc",
                   "dm", "hi", "hey", "dear", "see"}

COMMON_TYPOS = {
    "teh": "the", "adn": "and", "taht": "that", "wiht": "with", "waht": "what",
    "recieve": "receive", "reciept": "receipt", "definately": "definitely",
    "definatly": "definitely", "seperate": "separate", "occured": "occurred",
    "untill": "until", "wich": "which", "becuase": "because", "beacuse": "because",
    "becasue": "because", "tommorow": "tomorrow", "tomorow": "tomorrow",
    "tommorrow": "tomorrow", "tomorro": "tomorrow", "wensday": "Wednesday",
    "wednsday": "Wednesday", "wendsday": "Wednesday", "thurday": "Thursday",
    "thrusday": "Thursday", "firday": "Friday", "fridy": "Friday",
    "saterday": "Saturday", "satuday": "Saturday", "teusday": "Tuesday",
    "tuseday": "Tuesday", "remeber": "remember", "rember": "remember",
    "apointment": "appointment", "appointmnet": "appointment", "appoinment": "appointment",
    "meeitng": "meeting", "meting": "meeting", "calender": "calendar",
    "neccessary": "necessary", "necesary": "necessary", "accomodate": "accommodate",
    "existance": "existence", "goverment": "government", "immediatly": "immediately",
    "independant": "independent", "occassion": "occasion", "persistant": "persistent",
    "publically": "publicly", "realy": "really", "refered": "referred",
    "relevent": "relevant", "resturant": "restaurant", "restaraunt": "restaurant",
    "shedule": "schedule", "schedual": "schedule", "sucessful": "successful",
    "thier": "their", "writting": "writing", "wierd": "weird", "alot": "a lot",
    "pls": "please", "plz": "please", "thx": "thanks", "wana": "want to",
    "gonna": "going to", "gotta": "got to", "enuf": "enough", "fone": "phone",
    "tonite": "tonight", "ofice": "office", "emial": "email", "emal": "email",
    "pickup": "pick up", "dishs": "dishes", "wahs": "wash", "laundy": "laundry",
    "grocerys": "groceries", "groceri": "groceries", "invioce": "invoice",
    "invoce": "invoice", "relpy": "reply", "rpely": "reply",
    "dont": "don't", "doesnt": "doesn't", "didnt": "didn't", "cant": "can't",
    "wont": "won't", "isnt": "isn't", "arent": "aren't", "wasnt": "wasn't",
    "werent": "weren't", "hasnt": "hasn't", "havent": "haven't", "hadnt": "hadn't",
    "couldnt": "couldn't", "shouldnt": "shouldn't", "wouldnt": "wouldn't",
    "im": "I'm", "ive": "I've", "youre": "you're", "theyre": "they're",
    "thats": "that's", "whats": "what's",
}

_WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTH_NAMES = ["january", "february", "april", "june", "july", "august",
                "september", "october", "november", "december"]

_PROPER = ["sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra", "hobart",
           "darwin", "london", "paris", "tokyo", "auckland", "australia", "google", "gmail",
           "facebook", "instagram", "linkedin", "microsoft", "amazon", "christmas", "easter"]

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u200B\u200C\u2060\uFEFF]")
_SMART = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"',
                        "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ",
                        "\u2022": "-", "\u25cf": "-", "\u2023": "-", "\u2043": "-"})

_BULLET = re.compile(r"^\s*(?:[-*\u2022+>]|\(?\d{1,2}[.)]|\[[ xX]?\])\s+")
_ABBR = ["mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "a.m.", "p.m.", "no.", "inc.", "ltd.", "approx."]

# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


@dataclass
class Item:
    text: str
    deadline: Deadline | None = None
    priority: bool = False
    checklist: list[str] = field(default_factory=list)
    icons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # what was changed, for the log
    index: int = 0


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

_SIG_STOP = re.compile(
    r"^(?:--\s*$|__+\s*$|sent from my |get outlook for |sent via |sent from mail for |on .{5,120} wrote:\s*$|"
    r"-{2,}\s*(?:original message|forwarded message)|from:\s.+<.+@.+>\s*$)", re.I)


def tidy_chars(text: str) -> str:
    text = text.translate(_SMART)
    text = _EMOJI.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def clean_body(text: str, *, strip_quotes: bool = True) -> str:
    """Normalise characters; drop quoted replies, signatures and 'sent from' lines."""
    text = tidy_chars(text or "")
    out: list[str] = []
    for line in text.split("\n"):
        if strip_quotes and _SIG_STOP.match(line.strip()):
            break
        if strip_quotes and line.lstrip().startswith(">"):
            continue
        out.append(line.rstrip())
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Splitting
# --------------------------------------------------------------------------

_DELIM = re.compile(
    r"\s*,?\s*(?:and then|and also|then|and|also|plus)\b\s*|\s*&\s*|\s*[,;]\s*", re.I)


def _strip_lead(s: str) -> str:
    s = _FILLER_START.sub("", s.strip())
    s = _INTENT_START.sub("", s)
    return s.strip()


def starts_with_action(s: str) -> bool:
    s = _strip_lead(s)
    m = re.match(r"[A-Za-z']+", s)
    return bool(m) and m.group(0).lower() in VERBS


def _split_sentences(line: str) -> list[str]:
    protected = line
    for a in _ABBR:
        protected = re.sub(re.escape(a), a.replace(".", "\x00"), protected, flags=re.I)
    protected = re.sub(r"(\d)\.(\d)", "\\1\x00\\2", protected)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Za-z0-9\"'(])", protected)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def split_actions(line: str) -> list[str]:
    """Split "wash dishes, walk the dog and reply to work email" into 3 tasks."""
    segs: list[tuple[int, int]] = []  # (start, end) of each segment
    delims: list[tuple[int, int]] = []
    pos = 0
    for m in _DELIM.finditer(line):
        if m.start() == m.end():
            continue
        segs.append((pos, m.start()))
        delims.append((m.start(), m.end()))
        pos = m.end()
    segs.append((pos, len(line)))
    if len(segs) < 2 or not starts_with_action(line[segs[0][0]:segs[0][1]]):
        return [line]

    items: list[str] = []
    cur_start = segs[0][0]
    for i in range(1, len(segs)):
        seg = line[segs[i][0]:segs[i][1]]
        prev_words = len(line[cur_start:segs[i - 1][1]].split())
        if starts_with_action(seg) and prev_words >= 2 and len(seg.split()) >= 2:
            items.append(line[cur_start:segs[i - 1][1]].strip())
            cur_start = segs[i][0]
    items.append(line[cur_start:].strip())
    return [i for i in items if i]


def _dedupe(ids):
    out: list[str] = []
    for i in ids:
        if i not in out:
            out.append(i)
    return out


def pop_leading_tags(text: str, cfg: dict) -> tuple[list[str], str]:
    """If the FIRST line is only tags ("[school] [!]"), they belong to every reminder in the email."""
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and icon_lib.is_tags_only(lines[i], cfg):
        return icon_lib.extract_tags(lines[i], cfg)[1], "\n".join(lines[i + 1:]).lstrip("\n")
    return [], text


def split_blocks(text: str, split: bool = True, cfg: dict | None = None) -> list[dict]:
    """Return blocks: {"text": str, "icons": [...]} or with a "checklist": [...].

    * Blank lines separate reminders.
    * A single paragraph with one task per line is read as a list of tasks.
    * "Shopping:" followed by bullets is ONE reminder with a checklist.
    * Inside one line, "and"/commas split only where each part is an action.
    * With ``cfg``, [symbol] tags are lifted out of each line and stay with the reminder(s) made from that
      line; a line holding only tags decorates the next reminder.
    """
    def tag(line: str) -> tuple[str, list[str]]:
        return icon_lib.extract_tags(line, cfg) if cfg is not None else (line, [])

    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    blocks: list[dict] = []
    pending: list[str] = []
    one_per_line = len(paragraphs) == 1

    def add(block: dict, ids: list[str]) -> None:
        ids = _dedupe(pending + ids)
        pending.clear()
        if ids:
            block["icons"] = ids
        blocks.append(block)

    for para in paragraphs:
        cleaned: list[tuple[str, list[str]]] = []   # tags-only lines stay in place as ("", ids) markers
        for l in (l for l in para.split("\n") if l.strip()):
            cleaned.append(tag(l))
        lines = [c for c, _ in cleaned if c.strip()]
        para_ids = _dedupe([i for _, ids in cleaned for i in ids])
        if not lines:
            pending.extend(para_ids)         # only tags here: they decorate the next reminder
            continue
        bulleted = [bool(_BULLET.match(l)) for l in lines]
        if len(lines) > 1 and lines[0].rstrip().endswith(":") and all(bulleted[1:]):
            add({"text": lines[0].rstrip(": ").strip(),
                 "checklist": [_BULLET.sub("", l).strip() for l in lines[1:]]}, para_ids)
            continue
        if len(lines) > 1 and not one_per_line and not any(bulleted):
            add({"text": "\n".join(l.strip() for l in lines)}, para_ids)      # one multi-line reminder
            continue
        for l, ids in cleaned:
            if not l.strip():
                pending.extend(ids)          # a tags-only line decorates the NEXT reminder
                continue
            l = _BULLET.sub("", l).strip()
            if not l:
                continue
            if not split:
                add({"text": l}, ids)
                continue
            sentences = _split_sentences(l)
            merged: list[str] = []
            for sent in sentences:
                if merged and not starts_with_action(sent):
                    merged[-1] += " " + sent  # detail belonging to the previous task
                else:
                    merged.append(sent)
            for sent in merged:
                for a in split_actions(sent):
                    add({"text": a}, ids)
    return blocks


# --------------------------------------------------------------------------
# Spelling, grammar, punctuation
# --------------------------------------------------------------------------

_speller = None
_speller_tried = False


def _get_speller():
    global _speller, _speller_tried
    if not _speller_tried:
        _speller_tried = True
        try:
            from spellchecker import SpellChecker  # type: ignore
            _speller = SpellChecker(language="en", distance=1)
        except Exception:  # package missing -> built-in typo list only
            _speller = None
    return _speller


def _match_case(src: str, dst: str) -> str:
    if src.isupper() and len(src) > 1:
        return dst.upper()
    if src[:1].isupper():
        return dst[:1].upper() + dst[1:]
    return dst


_WORD = re.compile(r"[A-Za-z][A-Za-z']*")

# British/Australian spellings the (American-leaning) dictionary doesn't know but that are perfectly right.
_UK_WORDS = {"cheque", "cheques", "licence", "licences", "practise", "practised", "programme", "programmes", "grey",
             "mum", "mums", "aluminium", "pyjamas", "kerb", "plough", "storey", "draught", "tyre", "tyres", "analogue",
             "catalogue", "dialogue", "jewellery", "mould", "sceptical", "speciality", "whilst", "arvo", "brekkie"}
_UK_SUFFIXES = [("ise", "ize"), ("ised", "ized"), ("ises", "izes"), ("ising", "izing"), ("isation", "ization"),
                ("yse", "yze"), ("ysed", "yzed"), ("tre", "ter"), ("tres", "ters"), ("yre", "ire"), ("yres", "ires"),
                ("ence", "ense"), ("lled", "led"), ("lling", "ling"), ("ller", "ler"), ("ogue", "og")]


def _is_uk_spelling(word: str, speller) -> bool:
    """colour/organise/centre/tyres ... valid when the American form is a real word."""
    w = word.lower()
    if w in _UK_WORDS:
        return True
    forms = [w.replace("our", "or")] if "our" in w else []
    forms += [w[: -len(a)] + b for a, b in _UK_SUFFIXES if w.endswith(a)]
    return any(f != w and not speller.unknown([f]) for f in forms)


def fix_spelling(text: str, custom_words=(), notes: list[str] | None = None) -> str:
    custom = {w.lower() for w in custom_words}
    speller = _get_speller()
    out, last = [], 0
    tag_spans = [m.span() for m in re.finditer(r"\[[^\[\]\n]{1,30}\]", text)]    # [school], [wrk] ... not ours to fix
    for m in _WORD.finditer(text):
        if any(a <= m.start() < b for a, b in tag_spans):
            continue
        w = m.group(0)
        lw = w.lower()
        s, e = m.start(), m.end()
        before = text[s - 1] if s else " "
        after = text[e] if e < len(text) else " "
        if lw in custom:
            continue
        if (s and before in "@/#_\\") or after in "@/_#":
            continue  # part of an email address, URL, hashtag or path
        if before == "." and s > 1 and text[s - 2].isalnum():
            continue  # file.pdf
        if after == "." and e + 1 < len(text) and text[e + 1].isalnum():
            continue  # a.b
        if any(ch.isdigit() for ch in text[max(0, s - 1):e + 1]):
            continue
        prev = re.search(r"([A-Za-z']+)\W*$", text[:s])
        prev_word = prev.group(1).lower() if prev else ""
        repl = None
        if lw in COMMON_TYPOS:
            repl = COMMON_TYPOS[lw]
            if not repl[0].isupper():  # "I'm" / "Wednesday" are always capitalised
                repl = _match_case(w, repl)
        elif speller is not None and len(w) >= 4 and "'" not in w and not w.isupper():
            sentence_start = not text[:s].strip() or text[:s].rstrip()[-1:] in ".!?\n:"
            capitalised = w[0].isupper() and not sentence_start
            if lw in speller.unknown([lw]) and not capitalised and not _is_uk_spelling(w, speller):
                if prev_word in _PERSON_CONTEXT:
                    repl = w[0].upper() + w[1:]  # call sarah -> call Sarah
                else:
                    cands = speller.candidates(lw)
                    if cands:
                        best = max(cands, key=lambda c: speller.word_usage_frequency(c))
                        if best != lw and best.isalpha():
                            repl = _match_case(w, best)
        if repl and repl != w:
            out.append(text[last:s])
            out.append(repl)
            last = e
            if notes is not None:
                notes.append(f"{w} -> {repl}")
    out.append(text[last:])
    return "".join(out)


_VOWEL_SOUND_EXC = ("uni", "use", "usu", "uti", "eu", "one", "once", "ura", "uri")
_SILENT_H = ("hour", "honest", "honor", "honour", "heir")


def _a_an(m: re.Match) -> str:
    art, word = m.group(1), m.group(2)
    lw = word.lower()
    if word.isupper() and len(word) > 1:
        return m.group(0)  # acronyms: "a FAQ", "an NBN"
    vowel = lw[0] in "aeiou" and not lw.startswith(_VOWEL_SOUND_EXC)
    vowel = vowel or lw.startswith(_SILENT_H)
    fixed = ("an" if vowel else "a") if art.islower() else ("An" if vowel else "A")
    return f"{fixed} {word}"


def fix_grammar(text: str) -> str:
    t = text
    t = re.sub(r"\bi\b(?!\.)", "I", t)
    t = re.sub(r"\b(should|could|would|must|might) of\b", r"\1 have", t, flags=re.I)
    t = re.sub(r"\byour (welcome|right|wrong)\b", r"you're \1", t, flags=re.I)
    t = re.sub(r"\bits (a|an|been|not|time|going|ok|okay|too|only|the|urgent|important|due|late|done|ready|fine|my|your|our|just|still|very|so|really)\b", r"it's \1", t, flags=re.I)
    t = re.sub(r"\b(a|an) ([A-Za-z]+)", _a_an, t)
    t = re.sub(r"\b(\w+)( \1\b)+", lambda m: m.group(0) if m.group(1).lower() in ("had", "that") else m.group(1), t, flags=re.I)
    for d in _WEEKDAY_NAMES + _MONTH_NAMES:
        t = re.sub(rf"\b{d}\b", d.capitalize(), t)
    t = re.sub(r"\b(" + "|".join(_PROPER) + r")\b", lambda m: m.group(1).capitalize(), t)
    t = re.sub(r"\b(may|march)(?= \d)", lambda m: m.group(1).capitalize(), t)
    return t


def fix_punctuation(text: str) -> str:
    t = text
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:])(?=[A-Za-z])", r"\1 ", t)
    t = re.sub(r"(?<=[a-z)])([.!?])(?=[A-Z])", r"\1 ", t)
    t = re.sub(r"([!?]){4,}", r"\1\1\1", t)
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r"(?<![.])\.\.(?![.])", ".", t)
    t = re.sub(r",{2,}", ",", t)
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\s+\)", ")", t)
    return t.strip()


def capitalise_sentences(text: str) -> str:
    def up(m):
        return m.group(1) + m.group(2).upper()
    t = re.sub(r"(^|[.!?]\s+|\n\s*)([a-z])", up, text)
    return t


def fix_text(text: str, cfg: dict, *, is_task: bool = True, notes: list[str] | None = None,
             spell: bool = True) -> str:
    """Spelling + grammar + punctuation on one reminder (keeps line breaks)."""
    tcfg = cfg["text"]
    lines = []
    for line in text.split("\n"):
        if not line.strip():
            lines.append("")
            continue
        l = tidy_chars(line)
        if spell and tcfg.get("fix_spelling", True):
            l = fix_spelling(l, tcfg.get("custom_words", []), notes)
        l = fix_punctuation(l)
        if tcfg.get("fix_grammar", True):
            l = fix_grammar(l)
        l = capitalise_sentences(l)
        lines.append(l)
    out = "\n".join(lines).strip()
    return _final_punct(out, is_task, tcfg.get("trailing_period", False))


def _final_punct(text: str, is_task: bool, trailing_period: bool) -> str:
    if not text:
        return text
    sentences = len(re.findall(r"[.!?](?:\s|$)", text))
    if text[-1] in ".!?)\"'":
        # a lone short task keeps no full stop unless asked to
        if is_task and not trailing_period and text.endswith(".") and not text.endswith("...") and sentences <= 1 and "\n" not in text:
            return text[:-1]
        return text
    if (not is_task) or sentences >= 1 or "\n" in text or trailing_period:
        return text + "." if text[-1].isalnum() else text
    return text


# --------------------------------------------------------------------------
# Whole-email pipeline
# --------------------------------------------------------------------------


def _is_priority(text: str, words) -> bool:
    if re.search(r"(^|\s)!{2,}|!{2,}$", text):
        return True
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in words if w.strip())


def build_items(text: str, ref: datetime, cfg: dict, *, split: bool | None = None) -> list[Item]:
    """Full pipeline for the body of a plain 'reminder' email."""
    tcfg = cfg["text"]
    split = tcfg.get("split", True) if split is None else split
    date_order = cfg["general"].get("date_order", "dmy")
    text = clean_body(text)
    lead_ids, text = pop_leading_tags(text, cfg)
    if not text:
        return []
    # Spelling goes first: "wahs the dishes tomorow" must be read as an action
    # with a date, so the typos are fixed before splitting / date-finding.
    spell_notes: list[str] = []
    if tcfg.get("fix_spelling", True):
        text = "\n".join(fix_spelling(l, tcfg.get("custom_words", []), spell_notes) for l in text.split("\n"))
    if split:
        blocks = split_blocks(text, True, cfg)
    else:
        clean, ids = icon_lib.extract_tags(text, cfg)
        blocks = [{"text": clean, **({"icons": ids} if ids else {})}]
    items: list[Item] = []
    for i, b in enumerate(blocks):
        raw = b["text"]
        notes: list[str] = list(spell_notes) if not items else []
        deadline = None
        # Look for the deadline line-by-line so multi-line reminders work too.
        lines = raw.split("\n")
        for li, line in enumerate(lines):
            line = _strip_lead(line) if li == 0 else line
            if deadline is None and tcfg.get("deadline_words", True):
                line, deadline = extract_deadline(line, ref, date_order)
            lines[li] = line
        raw = "\n".join(l for l in lines if l.strip() or len(lines) == 1)
        priority = _is_priority(raw, tcfg.get("priority_words", []))
        fixed = fix_text(raw, cfg, is_task=len(lines) == 1 and split, notes=notes, spell=False) if raw.strip() else ""
        checklist = [fix_text(c, cfg, is_task=True, spell=False) for c in b.get("checklist", [])]
        if not fixed and not checklist and deadline is None:
            continue
        items.append(Item(text=fixed or "(see deadline)", deadline=deadline, priority=priority,
                          checklist=checklist, notes=notes, index=i, icons=_dedupe(lead_ids + b.get("icons", []))))
    return items


def sort_items(items: list[Item], mode: str = "smart") -> list[Item]:
    """smart: urgent first, then soonest deadline, then undated in written order."""
    if mode == "original":
        return sorted(items, key=lambda i: i.index)
    far = datetime.max.replace(tzinfo=None)

    def when(i: Item):
        return i.deadline.when.replace(tzinfo=None) if i.deadline else far

    if mode == "deadline":
        return sorted(items, key=lambda i: (when(i), i.index))
    return sorted(items, key=lambda i: (not i.priority, when(i), i.index))
