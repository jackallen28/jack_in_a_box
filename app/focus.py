"""[focus] requests: tell the box what you care about in a forwarded email.

Above the forwarded message, in square brackets:

    [reply by friday]   [paper due monday]   [cost]   [actions]   [deadlines]   [full]

* ``[full]`` prints the whole message instead of a summary (signature/disclaimer/older replies still removed).
* anything else is a question: the sentences of the email most relevant to it are lifted out and printed under the
  heading you typed, e.g.  REPLY BY FRIDAY -> "Please let me know by Wednesday 5th."  If nothing relevant is in
  the email it says so, so you know it wasn't missed.
Symbols ([school], [!] ...) are untouched: they keep working as before.
This is word-matching (with synonyms such as reply = respond/get back), so it only ever quotes the email and
can't invent anything.
"""
from __future__ import annotations

import math
import re

from . import icons as icon_lib

_TAG = re.compile(r"\[([^\[\]\n]{1,60})\]")
FULL_WORDS = {"full", "fullemail", "fulltext", "whole", "wholeemail", "all", "entire"}
ACTION_WORDS = {"actions", "action", "actionitems", "todo", "todos", "tasks", "requests", "asks"}
DEADLINE_WORDS = {"deadlines", "deadline", "dates", "when", "dues"}

STOP = set("""a an and are as at be by do for from have i if in is it me my of on or our that the their them then there
these this to up was we what when where which will with you your please about can could should would just also
how much many who whom whose why""".split())

# groups of words that mean (nearly) the same thing, by stem
_SYN = [
    "reply respond response answer revert feedback acknowledge",
    "pay payment invoice bill remit transfer owe overdue balance outstanding fee",
    "due deadline submit submission cutoff expire expiry",
    "meet meeting call appointment session catch discuss agenda conference",
    "send attach forward provide supply share deliver",
    "confirm rsvp approve agree accept sign authorise authorize ok",
    "attend come join present",
    "book reserve schedule arrange organise organize",
    "cost price quote estimate amount total charge",
    "paper assignment essay report document homework project thesis",
    "review check read look proof",
    "bring pack take",
    "cancel postpone reschedule move change",
]
_GROUP: dict[str, int] = {}
for gi, grp in enumerate(_SYN):
    for w in grp.split():
        _GROUP[w] = gi

# multi-word ways of saying "reply", folded to one word before matching
_REPLYISH = re.compile(r"\b(?:let (?:me|us) know|get back to|write back|come back to|touch base|hear (?:back )?from)\b", re.I)
_MONEY_WORDS = {"cost", "price", "amount", "total", "fee", "charge", "quote", "budget", "owing", "owed", "pay", "payment", "invoice"}
_MONEY = re.compile(r"\$\s?\d")

_WEEKDAY = r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|sday|nesday|rsday|urday)?"
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DATEISH = re.compile(
    rf"\b{_WEEKDAY}\b|\b{_MONTH}\b\.?\s*\d|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\b|\b\d{{1,2}}[/.-]\d{{1,2}}(?:[/.-]\d{{2,4}})?\b|"
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b(?:today|tomorrow|tonight|eod|cob|asap|end of (?:day|week|month)|next week|this week)\b", re.I)
_DATEWORD = re.compile(rf"^(?:{_WEEKDAY}|{_MONTH}|today|tomorrow|tonight|\d+(?:st|nd|rd|th)?|eod|cob|week|month|weekend)$", re.I)
_ACTIONISH = re.compile(
    r"\b(?:please|kindly|could you|can you|would you|need(?:s)? to|must|should|required?|ensure|remember|make sure|asap|urgent|"
    r"rsvp|reply|respond|confirm|submit|send|provide|pay|approve|sign|review|complete|attend|bring|let me know|let us know)\b", re.I)


def stem(w: str) -> str:
    w = w.lower()
    for suf in ("ing", "ed", "es", "s", "ly"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _canon(w: str) -> str:
    s = stem(w)
    return f"g{_GROUP[w.lower()]}" if w.lower() in _GROUP else (f"g{_GROUP[s]}" if s in _GROUP else s)


def parse_focus(notes: str, cfg: dict) -> tuple[str, list[str], bool]:
    """Pull [focus] phrases (and [full]) out of the notes you typed above a forward.

    Known symbol tags are left in place for the symbol parser; everything else in brackets is a focus request."""
    table = icon_lib.lookup(cfg)
    focus: list[str] = []
    full = False

    def take(m: re.Match) -> str:
        nonlocal full
        inner = m.group(1).strip()
        key = icon_lib.norm(inner)
        if key in FULL_WORDS and key not in table:
            full = True
            return ""
        if key in table or not re.search(r"[A-Za-z0-9]", inner):
            return m.group(0)              # a symbol ([school], [!]) - not ours
        if inner.lower() not in [f.lower() for f in focus]:
            focus.append(inner)
        return ""

    clean = _TAG.sub(take, notes or "")
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    return clean.strip(), focus, full


_PIC = r"(?:images?|photos?|pictures?|pics?|attachments?)"
_IMAGE_REQUEST = re.compile(
    rf"\[\s*(?:(?:include|add|with|attach|print|show)\s+)?(?:the\s+|any\s+|all\s+)?{_PIC}\s*\]"          # [image]  [include photos]
    rf"|\b(?:please\s+)?(?:include|add|attach|print|show)\s+(?:the\s+|any\s+|all\s+|its\s+|an?\s+)?(?:attached\s+|original\s+)?{_PIC}\b",
    re.I)


def detect_image_request(notes: str) -> tuple[bool, str]:
    """Did you say "include image" (anywhere in your note, or as [image])?  Returns (yes/no, note without that phrase)."""
    if not _IMAGE_REQUEST.search(notes or ""):
        return False, notes
    clean = _IMAGE_REQUEST.sub("", notes)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = re.sub(r"[ \t]*([,.;:])[ \t]*(?=[,.;:]|\n|$)", r"\1", clean)             # tidy ", ," or " ." left behind
    clean = re.sub(r"(?m)^[ \t]*[,.;:\-]+[ \t]*$", "", clean)
    clean = re.sub(r"(?m)^[ \t,.;:\-]+", "", clean)                                         # ". ring Dave" -> "ring Dave"
    clean = re.sub(r"(?mi)^(?:and|then|also|plus|but)\s+", "", clean)                         # "and reply by friday" -> "reply by friday"
    clean = re.sub(r"(?mi)[ \t]+(?:and|then|also|plus|but)[ \t]*$", "", clean)
    clean = re.sub(r"(?m)[ \t]*[,;:\-]+[ \t]*$", "", clean)                                   # "chase Dave," -> "chase Dave"
    return True, re.sub(r"\n{3,}", "\n\n", clean).strip()


def has_date(sentence: str) -> bool:
    return bool(_DATEISH.search(sentence))


def action_score(sentence: str) -> float:
    """How much a sentence looks like a request or a deadline (0 = neither, ~3 = both strongly)."""
    s = 0.0
    if _ACTIONISH.search(sentence):
        s += 1.0
    if has_date(sentence):
        s += 1.0
    if re.search(r"\b(?:by|before|until|due|deadline|no later than|expires?)\b", sentence, re.I):
        s += 0.7
    if re.search(r"\$\s?\d|\b\d[\d,]*\s?(?:dollars|aud|usd)\b", sentence, re.I):
        s += 0.3
    if sentence.rstrip().endswith("?"):
        s += 0.3
    return s


def find_focus(phrase: str, sentences: list[str], limit: int = 2) -> list[str]:
    """The sentences of the email that best answer a focus phrase (in original order)."""
    key = icon_lib.norm(phrase)
    if key in ACTION_WORDS:
        scored = [(action_score(s) - (0.5 if has_date(s) and not _ACTIONISH.search(s) else 0), i) for i, s in enumerate(sentences)]
        need = 1.0
    elif key in DEADLINE_WORDS:
        scored = [(action_score(s) if has_date(s) else 0.0, i) for i, s in enumerate(sentences)]
        need = 1.0
    else:
        phrase = _REPLYISH.sub("reply", phrase)
        words = [w for w in re.findall(r"[A-Za-z0-9$]+", phrase) if w.lower() not in STOP]
        topic_words = [w for w in words if not _DATEWORD.match(w)]
        dates = {w.lower() for w in words if _DATEWORD.match(w)}
        money_q = any(w.lower() in _MONEY_WORDS or stem(w) in _MONEY_WORDS for w in words)
        if not words:
            return []
        scored = []
        for i, s in enumerate(sentences):
            s2 = _REPLYISH.sub("reply", s)
            toks = re.findall(r"[A-Za-z0-9$]+", s2)
            stems = {stem(t) for t in toks}
            canon = {_canon(t) for t in toks}
            raw = {t.lower() for t in toks}
            # an exact word beats a synonym ("invoice" in the sentence beats "payment")
            weights = [1.0 if stem(w) in stems else 0.6 if _canon(w) in canon else 0.0 for w in topic_words]
            hit, n_hit = sum(weights), sum(1 for x in weights if x)
            dhit = min(1.5, len(dates & raw) + (0.5 if dates and has_date(s) else 0.0))
            act = 0.3 if _ACTIONISH.search(s2) else 0.0
            amount = bool(_MONEY.search(s))
            if hit:
                score = hit / math.sqrt(max(1, len(topic_words))) + 0.8 * dhit + act + (0.4 if n_hit == len(topic_words) else 0.0)
                if money_q and amount:
                    score += 0.8                        # "how much?" is answered by a sentence with an amount in it
            elif money_q and amount:
                score = 1.0 + act
            elif dates and dhit >= 1 and act:
                score = 0.6 * dhit + 0.5                # no matching topic word, but the right date and a request
            else:
                score = 0.0
            scored.append((score, i))
        need = 0.9
    best = sorted([x for x in scored if x[0] >= need], key=lambda x: -x[0])[:limit]
    return [sentences[i] for _, i in sorted(best, key=lambda x: x[1])]
