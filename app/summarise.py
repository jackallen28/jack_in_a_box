"""Extractive summariser: pick the 3-5 most informative sentences.

This is classic frequency-based sentence ranking (no model, no network), so it
runs in milliseconds on a Pi.  It cannot paraphrase; it quotes the best
sentences in their original order.  An optional local LLM (see ``llm.py``) can
be switched on in the config for abstractive summaries.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .textproc import _split_sentences, tidy_chars

STOP = set("""
a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has
have having he her here hers him his how i if in into is it its just me more most my no nor not
now of off on once only or other our out over own same she should so some such than that the
their them then there these they this those through to too under until up very was we were what
when where which while who whom why will with would you your hi hello dear regards thanks thank
please best kind sincerely cheers
""".split())

CUES = ("please", "need", "required", "deadline", "due", "urgent", "important", "meeting",
        "invoice", "payment", "confirm", "attached", "reply", "action", "approve", "approval",
        "schedule", "tomorrow", "today", "friday", "monday", "agenda", "decision", "outcome",
        "summary", "update", "reminder", "overdue", "cancel", "booking", "appointment")

_JUNK_LINE = re.compile(
    r"unsubscribe|view (?:this )?(?:email )?in (?:your )?browser|privacy policy|all rights reserved|"
    r"this e-?mail and any|confidential|do not reply|no-?reply|click here|manage (?:your )?preferences|"
    r"sent from my|get outlook|copyright|©|terms (?:of use|and conditions)|if you (?:are not|received)",
    re.I)
_GREETING = re.compile(r"^(?:hi|hello|hey|dear|good (?:morning|afternoon|evening)|g'?day)\b[^.!?\n]{0,40}[,!:]?$", re.I)
_SIGNOFF = re.compile(r"^(?:kind |best |warm |many |with )?(?:regards|thanks|thank you|cheers|sincerely|best|yours|"
                      r"talk soon|speak soon|ta)\b[^.!?]{0,30}[,.!]?$", re.I)
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)


def _prepare(text: str) -> list[str]:
    text = tidy_chars(text)
    text = _URL.sub("", text)
    lines: list[str] = []
    skip = 0
    for raw in text.split("\n"):
        line = raw.strip()
        if skip:
            skip -= 1
            if len(line.split()) <= 4:
                continue
        if not line or line.startswith(">") or _JUNK_LINE.search(line):
            continue
        if _GREETING.match(line):
            continue
        if _SIGNOFF.match(line):
            skip = 2  # the name under "Regards,"
            continue
        if re.fullmatch(r"[-_=*#~\s]{3,}", line):
            continue
        line = re.sub(r"^\s*(?:[-*+]|\d{1,2}[.)])\s+", "", line)
        lines.append(line)

    merged: list[str] = []
    for line in lines:  # re-join hard-wrapped lines; list items stay separate
        if merged and not re.search(r"[.!?:]$", merged[-1]) and line[:1].islower():
            merged[-1] += " " + line
        else:
            merged.append(line)
    sentences = [s for line in merged for s in _split_sentences(line)]
    return [re.sub(r"\s+", " ", s).strip() for s in sentences if len(s.split()) >= 4]


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z']+", s.lower()) if w not in STOP and len(w) > 2]


def summarise(text: str, n: int = 4, max_chars: int = 240) -> list[str]:
    sents = _prepare(text)
    # drop near-duplicates (quoted replies repeat themselves)
    uniq: list[str] = []
    seen: list[set] = []
    for s in sents:
        ws = set(_words(s))
        if any(ws and len(ws & o) / len(ws | o) > 0.7 for o in seen):
            continue
        uniq.append(s)
        seen.append(ws)
    sents = uniq
    if len(sents) <= n:
        chosen = list(range(len(sents)))
    else:
        tf = Counter(w for s in sents for w in _words(s))
        top = max(tf.values()) if tf else 1
        scores = []
        for i, s in enumerate(sents):
            ws = _words(s)
            if not ws:
                scores.append(0.0)
                continue
            base = sum(tf[w] / top for w in ws) / math.sqrt(len(ws))   # informative, not just long
            position = 1.0 / (1 + 0.25 * i)                           # openers matter most
            cue = 0.25 * sum(1 for c in CUES if c in s.lower())
            facts = 0.2 * len(re.findall(r"\$\s?\d|\d{1,2}[:/]\d{2}|\b\d{1,2}(?:st|nd|rd|th)\b|\b\d{4}\b", s))
            question = 0.2 if s.endswith("?") else 0.0
            length = 0.0 if 6 <= len(s.split()) <= 35 else -0.3
            scores.append(base + position + cue + facts + question + length)
        chosen = sorted(sorted(range(len(sents)), key=lambda i: scores[i], reverse=True)[:n])
    out = []
    for i in chosen:
        s = sents[i]
        if len(s) > max_chars:
            s = s[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "..."
        if s[-1] not in ".!?\"')":
            s += "."
        out.append(s)
    return out
