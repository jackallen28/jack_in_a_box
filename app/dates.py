"""Small, dependency-free natural-language date/time finder.

``extract_deadline("walk the dog at 5pm tomorrow", ref)`` returns the text with
the date phrase removed plus the resolved deadline.  It understands the
phrases people actually type in a quick note to themselves; anything it does
not understand is simply left in the text (never guessed).

Conventions (documented in the README):
  * 14/11 is 14 November (day first) unless ``date_order="mdy"``.
  * a bare weekday ("Friday") is the *next* one; "this Friday" includes today;
    "next Friday" is the Friday of next calendar week.
  * a time with no date ("call Sam at 3pm") is today, or tomorrow if that time
    has already passed.
  * "at 5" (no am/pm) is read as a working-day time: 1-6 = pm, 7-11 = am.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WD_FULL = {n: i for i, n in enumerate(WEEKDAYS)}
# Short forms are only trusted after a cue word ("on mon", "by fri"), because
# "sat", "sun" and "mon" are also ordinary words.
WD_SHORT = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
            "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
MONTH_MAP = {n: i + 1 for i, n in enumerate(MONTHS)}
MONTH_MAP.update({"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
                  "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12})
NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
             "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "sixty": 60}

_MONTH_RE = "|".join(sorted(MONTH_MAP, key=len, reverse=True))
_WD_FULL_RE = "|".join(WEEKDAYS)
_WD_SHORT_RE = "|".join(sorted(WD_SHORT, key=len, reverse=True))
_NUM_RE = "|".join(sorted(NUM_WORDS, key=len, reverse=True))
_FLAGS = re.IGNORECASE

# Words that may sit between a date token and a time token ("Friday at 5pm").
_GLUE = re.compile(
    r"^[\s,]*(?:(?:at|@|by|on|before|around|for|in the|in|of|the|until|till|from)[\s,]*)?$",
    _FLAGS)
# Cue words swallowed in front of a deadline so they vanish with it.
_PREFIX = re.compile(
    r"(?:\b(?:is\s+)?(?:due(?:\s+(?:on|by|for))?|deadline(?:\s+is)?|by|before|until|till|on|at|for|around|from)\s*:?\s*|@\s*)$",
    _FLAGS)


@dataclass
class Tok:
    start: int
    end: int
    kind: str                      # "date" | "time" | "both"
    d: date | None = None
    t: time | None = None
    soft: bool = False             # soft times ("tonight") yield to explicit ones
    needs_date: bool = False       # bare "morning" only counts next to a date
    delta: timedelta | None = None  # "in 2 hours"


@dataclass
class Deadline:
    when: datetime
    has_time: bool
    phrase: str


def _next_occurrence(month: int, day: int, ref: date) -> date | None:
    for year in (ref.year, ref.year + 1, ref.year + 2):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= ref:
            return d
    return None


def _weekday_date(ref: date, wd: int, mod: str) -> date:
    if mod == "next":
        monday_next = ref - timedelta(days=ref.weekday()) + timedelta(days=7)
        return monday_next + timedelta(days=wd)
    diff = (wd - ref.weekday()) % 7
    if mod == "this":
        return ref + timedelta(days=diff)
    return ref + timedelta(days=diff or 7)


def _hour12(h: int, ap: str) -> int:
    ap = ap.lower().replace(".", "")
    if ap == "pm" and h < 12:
        return h + 12
    if ap == "am" and h == 12:
        return 0
    return h


def _overlaps(toks: list[Tok], s: int, e: int) -> bool:
    return any(s < t.end and e > t.start for t in toks)


def _find_tokens(text: str, ref: datetime, date_order: str) -> list[Tok]:
    toks: list[Tok] = []
    rd = ref.date()

    def add(m, **kw):
        if not _overlaps(toks, m.start(), m.end()):
            toks.append(Tok(m.start(), m.end(), **kw))

    # ISO 2026-11-14
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            add(m, kind="date", d=date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            pass

    # in 2 hours / in a week / in 3 days
    for m in re.finditer(rf"\bin\s+(\d+|{_NUM_RE})\s+(minute|min|hour|hr|day|week|month)s?\b", text, _FLAGS):
        n = int(m[1]) if m[1].isdigit() else NUM_WORDS[m[1].lower()]
        unit = m[2].lower()
        if unit in ("minute", "min"):
            add(m, kind="both", delta=timedelta(minutes=n))
        elif unit in ("hour", "hr"):
            add(m, kind="both", delta=timedelta(hours=n))
        elif unit == "day":
            add(m, kind="date", d=rd + timedelta(days=n))
        elif unit == "week":
            add(m, kind="date", d=rd + timedelta(weeks=n))
        else:
            add(m, kind="date", d=rd + timedelta(days=30 * n))

    # 14 November 2026 / 14th Nov / November 14th
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b(?:,?\s+(\d{{4}}))?", text, _FLAGS):
        mon = MONTH_MAP[m[2].lower()]
        if m[3]:
            try:
                add(m, kind="date", d=date(int(m[3]), mon, int(m[1])))
            except ValueError:
                pass
        else:
            d = _next_occurrence(mon, int(m[1]), rd)
            if d:
                add(m, kind="date", d=d)
    for m in re.finditer(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}})\b)?", text, _FLAGS):
        mon = MONTH_MAP[m[1].lower()]
        if m[1].lower() == "may" and not m[0][0].isupper() and not m[3]:
            continue  # "may 5 items" is a verb, "May 5" is a date
        if m[3]:
            try:
                add(m, kind="date", d=date(int(m[3]), mon, int(m[2])))
            except ValueError:
                pass
        else:
            d = _next_occurrence(mon, int(m[2]), rd)
            if d:
                add(m, kind="date", d=d)

    # 14/11, 14/11/26, 14-11-2026, 14.11.2026
    for m in re.finditer(r"(?<![\d/.\-:])(\d{1,2})([/\-.])(\d{1,2})(?:\2(\d{2,4}))?(?![\d/:]|[.\-]\d)", text):
        sep, a, b, y = m[2], int(m[1]), int(m[3]), m[4]
        if sep != "/" and not y:
            continue  # "3-5 apples", "10.30" are not dates
        day, mon = (a, b) if date_order == "dmy" else (b, a)
        if mon > 12 and day <= 12:
            day, mon = mon, day
        if not (1 <= mon <= 12 and 1 <= day <= 31):
            continue
        try:
            if y:
                yy = int(y) + (2000 if len(y) == 2 else 0)
                d = date(yy, mon, day)
            else:
                d = _next_occurrence(mon, day, rd)
        except ValueError:
            continue
        if d:
            add(m, kind="date", d=d)

    # on the 14th
    for m in re.finditer(r"\b(?:the\s+)(\d{1,2})(?:st|nd|rd|th)\b", text, _FLAGS):
        n = int(m[1])
        if 1 <= n <= 31:
            for add_months in (0, 1, 2):
                y, mo = rd.year, rd.month + add_months
                y += (mo - 1) // 12
                mo = (mo - 1) % 12 + 1
                try:
                    d = date(y, mo, n)
                except ValueError:
                    continue
                if d >= rd:
                    add(m, kind="date", d=d)
                    break

    # relative days
    for m in re.finditer(r"\bday after tomorrow\b", text, _FLAGS):
        add(m, kind="date", d=rd + timedelta(days=2))
    for m in re.finditer(r"\b(?:tomorrow|tmrw|tmr|tmw)\b", text, _FLAGS):
        add(m, kind="date", d=rd + timedelta(days=1))
    for m in re.finditer(r"\btonight\b", text, _FLAGS):
        add(m, kind="both", d=rd, t=time(19, 0), soft=True)
    for m in re.finditer(r"\btoday\b", text, _FLAGS):
        add(m, kind="date", d=rd)
    for m in re.finditer(r"\bthis\s+weekend\b", text, _FLAGS):
        add(m, kind="date", d=_weekday_date(rd, 5, "this"))
    for m in re.finditer(r"\bnext\s+week\b", text, _FLAGS):
        add(m, kind="date", d=_weekday_date(rd, 0, "next"))
    for m in re.finditer(r"\b(?:end\s+of\s+(?:the\s+)?week|eow)\b", text, _FLAGS):
        add(m, kind="both", d=_weekday_date(rd, 4, "this"), t=time(17, 0), soft=True)
    for m in re.finditer(r"\b(?:end\s+of\s+(?:the\s+)?month|eom)\b", text, _FLAGS):
        last = calendar.monthrange(rd.year, rd.month)[1]
        add(m, kind="both", d=date(rd.year, rd.month, last), t=time(17, 0), soft=True)

    # weekdays
    for m in re.finditer(rf"\b(?:(this|next|coming)\s+)?({_WD_FULL_RE})\b", text, _FLAGS):
        mod = (m[1] or "").lower()
        mod = "this" if mod == "coming" else mod
        add(m, kind="date", d=_weekday_date(rd, WD_FULL[m[2].lower()], mod))
    for m in re.finditer(rf"\b(?:(this|next)\s+|(?:on|by|before|until|due|every)\s+)({_WD_SHORT_RE})\b\.?", text, _FLAGS):
        mod = (m[1] or "").lower()
        add(m, kind="date", d=_weekday_date(rd, WD_SHORT[m[2].lower()], mod))

    # times
    for m in re.finditer(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", text, _FLAGS):
        h, mi = int(m[1]), int(m[2] or 0)
        if 1 <= h <= 12 and mi < 60:
            add(m, kind="time", t=time(_hour12(h, m[3]), mi))
    for m in re.finditer(r"(?<![\d:.])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])", text):
        add(m, kind="time", t=time(int(m[1]), int(m[2])))
    for m in re.finditer(r"\b(noon|midday|midnight)\b", text, _FLAGS):
        add(m, kind="time", t=time(0, 0) if m[1].lower() == "midnight" else time(12, 0))
    for m in re.finditer(r"\b(?:eod|cob|end\s+of\s+(?:the\s+)?day|close\s+of\s+business)\b", text, _FLAGS):
        add(m, kind="time", t=time(17, 0), soft=True)
    for m in re.finditer(r"\bat\s+(\d{1,2})\b(?![:.\-/]\d)", text, _FLAGS):
        h = int(m[1])
        tail = text[m.end():m.end() + 14]
        # "at 5 Smith St", "at 3 of them", "at 2 x 4": not a time.
        if re.match(r"\s*(?:[A-Z]|st\b|street\b|ave\b|road\b|rd\b|of\b|x\b|people\b|items?\b|%|\d)", tail):
            continue
        if 1 <= h <= 12:
            hour = h + 12 if h <= 6 else h  # 1-6 pm, 7-11 am, 12 noon
            add(m, kind="time", t=time(hour, 0))
    for m in re.finditer(r"\b(?:this|in\s+the)\s+(morning|afternoon|evening)\b", text, _FLAGS):
        hour = {"morning": 9, "afternoon": 15, "evening": 18}[m[1].lower()]
        add(m, kind="both" if m[0].lower().startswith("this") else "time",
            d=rd if m[0].lower().startswith("this") else None, t=time(hour, 0), soft=True)
    for m in re.finditer(r"\b(morning|afternoon|evening)\b", text, _FLAGS):
        hour = {"morning": 9, "afternoon": 15, "evening": 18}[m[1].lower()]
        add(m, kind="time", t=time(hour, 0), soft=True, needs_date=True)

    toks.sort(key=lambda t: t.start)
    return toks


def _clusters(text: str, toks: list[Tok]) -> list[list[Tok]]:
    clusters: list[list[Tok]] = []
    cur: list[Tok] = []

    def has(c, kinds):
        return any(t.kind in kinds for t in c)

    for tok in toks:
        if cur:
            gap = text[cur[-1].end:tok.start]
            clash = (
                (tok.kind in ("date", "both") and has(cur, ("date", "both")) and not tok.soft)
                or (tok.kind in ("time", "both") and has(cur, ("time", "both")) and not (tok.soft or any(t.soft for t in cur)))
            )
            if _GLUE.match(gap) and not clash:
                cur.append(tok)
                continue
            clusters.append(cur)
        cur = [tok]
    if cur:
        clusters.append(cur)
    out = []
    for c in clusters:
        if any(t.needs_date for t in c) and not any(t.d for t in c):
            c = [t for t in c if not t.needs_date]
        if c:
            out.append(c)
    return out


def _resolve(cluster: list[Tok], ref: datetime) -> tuple[datetime, bool]:
    # Explicit values beat "soft" ones ("tonight", "this morning", "EOD"), and
    # the first of each kind wins.
    def pick(attr):
        vals = [(t.soft, getattr(t, attr)) for t in cluster if getattr(t, attr) is not None]
        vals.sort(key=lambda v: v[0])  # stable: explicit first, then soft
        return vals[0][1] if vals else None

    delta = next((t.delta for t in cluster if t.delta is not None), None)
    if delta is not None:
        return ref + delta, True
    d, t = pick("d"), pick("t")
    had_date = d is not None
    d = d or ref.date()
    if t is None:
        return datetime.combine(d, time(0, 0), tzinfo=ref.tzinfo), False
    when = datetime.combine(d, t, tzinfo=ref.tzinfo)
    if not had_date and when <= ref:
        when += timedelta(days=1)
    return when, True


def extract_deadline(text: str, ref: datetime, date_order: str = "dmy") -> tuple[str, Deadline | None]:
    """Find the first date/time phrase in ``text``; return (text without it, deadline)."""
    toks = _find_tokens(text, ref, date_order)
    clusters = _clusters(text, toks)
    if not clusters:
        return text, None
    cluster = clusters[0]
    when, has_time = _resolve(cluster, ref)
    start, end = cluster[0].start, cluster[-1].end
    phrase = text[start:end]
    m = _PREFIX.search(text[:start])
    if m:
        start = m.start()
    before, after = text[:start], text[end:]
    # Tidy the join: drop dangling separators / conjunctions left behind.
    before = re.sub(r"[\s,;:\-–(]+$", "", before)
    after = re.sub(r"^[\s,;:\-–)]+", "", after)
    after = re.sub(r"^(?:please|thanks|thank you)\b[\s.!]*$", "", after, flags=_FLAGS)
    cleaned = f"{before} {after}".strip() if before and after else (before or after)
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(r"\b(and|then|also|but|or)\s*$", "", cleaned, flags=_FLAGS).strip(" ,;")
    return cleaned, Deadline(when=when, has_time=has_time, phrase=phrase.strip())


def fmt_time(t: time, time_format: str = "12h") -> str:
    if time_format == "24h":
        return t.strftime("%H:%M")
    return t.strftime("%I:%M %p").lstrip("0")


def fmt_when(when: datetime, has_time: bool, ref: datetime, time_format: str = "12h") -> str:
    """Human wording: "Today, 5:00 PM", "Tomorrow", "Fri 7 Nov, 9:00 AM"."""
    d, rd = when.date(), ref.date()
    delta = (d - rd).days
    if delta == 0:
        day = "Today"
    elif delta == 1:
        day = "Tomorrow"
    elif delta == -1:
        day = "Yesterday"
    elif 0 < delta < 7:
        day = when.strftime("%A")
    else:
        day = f"{when.strftime('%a')} {d.day} {when.strftime('%b')}"
        if d.year != rd.year:
            day += f" {d.year}"
    if 1 < delta < 7:
        day = f"{day} {d.day} {when.strftime('%b')}"
    return f"{day}, {fmt_time(when.time(), time_format)}" if has_time else day
