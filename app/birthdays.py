"""Birthdays.

Two things, both with their own BIRTHDAY template (cake symbol, the name big, "Turns 40 today"):

  1. YOUR LIST.  Add people (name + day + month, and optionally the year they were born) on the Birthdays tab.  At the print time
     (default 07:00) on the day, a BIRTHDAY slip prints for each of them.  If the Pi was off at 07:00 it prints as soon as it is back
     that same day (switchable).  29 February people are remembered on 28 February in years that have no 29th.  Each person prints once
     a day, however many times the app restarts (the `seen` table remembers).

  2. RECOGNISING BIRTHDAY REMINDERS.  An ordinary reminder (email, Google Form row, iPhone Shortcut, web "Print") or a calendar event whose
     text talks about a birthday ("Sarah's birthday on Friday", "birthday: Mum", "Tom turns 8") is printed with the BIRTHDAY template instead
     of the plain reminder one.  Nothing is removed from what you wrote.  Optionally ("learn") a birthday that comes with a date
     ("Sarah's birthday is 14 March") is also added to your list so it comes round every year.
"""
from __future__ import annotations

import calendar
import logging
import re
import threading
import uuid
from datetime import date, datetime, time, timedelta

from . import features, icons as icon_lib
from .dates import MONTH_MAP
from .models import Card, Section

log = logging.getLogger("birthdays")

BIRTHDAY = re.compile(r"\bbirth\s*days?\b|\bb-?days?\b|\bturn(?:s|ing)\s+\d{1,3}\b", re.I)
_MONTH_RE = "|".join(sorted(MONTH_MAP, key=len, reverse=True))
_NAME = r"[A-Za-z][\w'’\-]*"
_STOP_NAME = {"my", "our", "the", "your", "his", "her", "their", "this", "that", "next", "a", "an", "on", "is", "it", "its", "for", "of", "to", "party", "day", "today", "tomorrow", "and",
              "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "birthday", "bday", "present", "gift", "card", "cake", "reminder", "remind", "me", "dont", "don't",
              "forget", "buy", "get", "wish", "happy", "with", "at", "in", "by", "from", "week", "weekend", "month", "year", "someone", "everyone", "friend", "mate", "colleague"}


# ---------------------------------------------------------------------------- names and dates in text
def _tidy_name(raw: str) -> str:
    words = [w for w in re.split(r"\s+", raw.strip(" -–—:,.!?'\"()")) if w]
    while words and words[0].lower() in _STOP_NAME:
        words.pop(0)
    while words and words[-1].lower() in _STOP_NAME:
        words.pop()
    words = [w for i, w in enumerate(words) if i == 0 or w.lower() != words[i - 1].lower()]        # "Sarah Sarah" (heading + text) -> Sarah
    if len(words) == 2 and words[0][0].islower() and words[1][0].isupper():
        words = words[1:]                                                  # "friend Sarah's birthday" -> Sarah
    name = " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in words[:3])
    return name if name and name.lower() not in _STOP_NAME else ""


def extract_name(text: str) -> str:
    """Who the birthday is for, if the text says: "Sarah's birthday", "birthday: Mum", "Happy birthday Tom", "Tom turns 8" (else blank)."""
    t = re.sub(r"\s+", " ", text or "")
    pats = [
        rf"((?:{_NAME}\s+)?{_NAME})['’]s?\s+(?:\d{{1,3}}(?:st|nd|rd|th)\s+)?(?:birth\s*day|b-?day)",
        rf"(?:happy\s+)?(?:birth\s*day|b-?day)\s*(?:party\s+)?(?:for|of|:|-|–|—)\s*((?:{_NAME}\s+)?{_NAME})",
        rf"happy\s+(?:birth\s*day|b-?day)\s+(?:to\s+)?((?:{_NAME}\s+)?{_NAME})",
        rf"((?:{_NAME}\s+)?{_NAME})\s+(?:turns|is\s+turning|turning)\s+\d{{1,3}}",
        rf"((?:{_NAME}\s+)?{_NAME})\s+(?:birth\s*day|b-?day)\b",
    ]
    for p in pats:
        m = re.search(p, t, re.I)
        if m:
            name = _tidy_name(m.group(1))
            if name:
                return name
    return ""


def _valid(day: int, month: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= calendar.monthrange(2024, month)[1]      # 2024 is a leap year: 29 Feb is allowed


def find_date(text: str, date_order: str = "dmy", today: date | None = None) -> tuple[int, int, int, tuple[int, int]] | None:
    """(day, month, birth year or 0, (start, end) of the date in the text).  Needs a month name or a numeric d/m: "14 March", "March 14th 1985", "14/3",
    "3/4/1960" (day first unless date_order is "mdy"), "1985-03-14".  A year that isn't in the past isn't a birth year (it is "her 40th on 14 March 2027")."""
    today = today or date.today()
    t = text or ""

    def year_of(g: str | None) -> int:
        if not g:
            return 0
        y = int(g)
        y = y + (1900 if y > today.year % 100 + 1 else 2000) if y < 100 else y
        return y if 1900 <= y < today.year else 0

    m = re.search(rf"\b((?:19|20)\d{{2}})[-/.](\d{{1,2}})[-/.](\d{{1,2}})\b", t)
    if m and _valid(int(m.group(3)), int(m.group(2))):
        return int(m.group(3)), int(m.group(2)), year_of(m.group(1)), m.span()
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b\.?(?:,?\s+((?:19|20)\d{{2}}))?", t, re.I)
    if m and _valid(int(m.group(1)), MONTH_MAP[m.group(2).lower()]):
        return int(m.group(1)), MONTH_MAP[m.group(2).lower()], year_of(m.group(3)), m.span()
    m = re.search(rf"\b({_MONTH_RE})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+((?:19|20)\d{{2}}))?", t, re.I)
    if m and _valid(int(m.group(2)), MONTH_MAP[m.group(1).lower()]):
        return int(m.group(2)), MONTH_MAP[m.group(1).lower()], year_of(m.group(3)), m.span()
    m = re.search(r"(?<![\d/.\-])(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?(?![\d/.\-]*\d)", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        day, month = (b, a) if date_order == "mdy" and a <= 12 else (a, b)
        if not _valid(day, month) and _valid(b, a) and a <= 12:
            day, month = b, a                                              # "3/14" can only be March 14th
        if _valid(day, month):
            return day, month, year_of(m.group(3)), m.span()
    return None


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def clean_person(d: dict, today: date | None = None) -> dict | None:
    """A person from the browser or a backup, made safe (None if it isn't a usable birthday)."""
    today = today or date.today()
    try:
        day, month = int(d.get("day")), int(d.get("month"))
        year = int(d.get("year") or 0)
    except (TypeError, ValueError):
        return None
    name = re.sub(r"\s+", " ", str(d.get("name", ""))).strip()[:60]
    if not name or not _valid(day, month):
        return None
    if year and not 1900 <= year <= today.year:
        year = 0
    pid = re.sub(r"[^a-z0-9]", "", str(d.get("id", "")).lower())[:16] or new_id()
    return {"id": pid, "name": name, "day": day, "month": month, "year": year, "note": str(d.get("note", "")).strip()[:120], "enabled": bool(d.get("enabled", True))}


def clean_people(people) -> list[dict]:
    out, ids = [], set()
    for d in people if isinstance(people, list) else []:
        p = clean_person(d) if isinstance(d, dict) else None
        if p:
            while p["id"] in ids:
                p["id"] = new_id()
            ids.add(p["id"])
            out.append(p)
    return out[:500]


def parse_person(text: str, date_order: str = "dmy", today: date | None = None) -> dict:
    """"Sarah 14 March", "Mum 3/4/1960", "14 Mar Dad", "Tom - March 9th 2018" -> a person.  Raises ValueError with a message for the person."""
    found = find_date(text, date_order, today)
    if not found:
        raise ValueError("I couldn't find a date. Try “Sarah 14 March” or “Sarah 14/3/1985”.")
    day, month, year, (s, e) = found
    rest = (text[:s] + " " + text[e:])
    rest = re.sub(r"(?i)\b(?:'s|’s)?\s*(?:birth\s*day|b-?day)s?\b", " ", rest)
    name = _tidy_name(re.sub(r"(?i)\b(?:on|is|born|turns?|the)\b", " ", rest)) or _tidy_name(rest)
    if not name:
        raise ValueError("I couldn't find a name. Try “Sarah 14 March”.")
    p = clean_person({"name": name, "day": day, "month": month, "year": year}, today)
    if p is None:
        raise ValueError("That doesn't look like a real date.")
    return p


def occurs_on(p: dict, d: date) -> bool:
    """Is it this person's birthday on day ``d``?  29 February is kept on 28 February in a year with no 29th."""
    if p["month"] == 2 and p["day"] == 29 and not calendar.isleap(d.year):
        return d.month == 2 and d.day == 28
    return d.month == p["month"] and d.day == p["day"]


def next_birthday(p: dict, today: date) -> date:
    for y in (today.year, today.year + 1):
        for cand in ((y, p["month"], p["day"]), (y, 2, 28)):
            try:
                d = date(*cand)
            except ValueError:
                continue
            if d >= today and occurs_on(p, d):
                return d
    return today


def upcoming(cfg: dict, today: date, limit: int = 6) -> list[dict]:
    rows = []
    for p in cfg["birthdays"].get("people", []):
        if not p.get("enabled", True):
            continue
        d = next_birthday(p, today)
        rows.append({"id": p["id"], "name": p["name"], "date": d.isoformat(), "days": (d - today).days, "turns": (d.year - p["year"]) if p.get("year") else 0})
    return sorted(rows, key=lambda r: (r["days"], r["name"].lower()))[:limit]


# ---------------------------------------------------------------------------- the slip
def symbol_id(cfg: dict) -> str:
    want = cfg.get("birthdays", {}).get("symbol") or "birthday"
    return icon_lib.lookup(cfg).get(icon_lib.norm(want)) or ("birthday" if "birthday" in icon_lib.BUILTIN else "")


def person_card(p: dict, cfg: dict, now: datetime) -> Card:
    age = now.year - p["year"] if p.get("year") else 0
    secs = [Section("text", text=f"Turns {age} today" if age > 0 else "Birthday today", strong=True)]
    if p.get("note"):
        secs.append(Section("notes", text=p["note"]))
    sym = symbol_id(cfg)
    return Card(kind="birthday", template="birthday", heading=p["name"], sections=secs, icons=[sym] if sym else [], source="birthday", created=now, stamp="",
                vars={"date": now.strftime("%a %d %b"), "name": p["name"], "age": str(age or "")})


# ---------------------------------------------------------------------------- recognising birthday reminders
def card_text(card: Card, written_only: bool = False) -> str:
    """The card's words.  ``written_only`` leaves out the lines the app worked out itself (the DUE / WHEN date), so only what you typed is read."""
    parts = [card.heading or ""]
    for s in card.sections:
        if written_only and s.key in ("deadline", "when"):
            continue
        parts.append(s.text or "")
        parts.extend(s.items or [])
    return "\n".join(x for x in parts if x)


def restyle(card: Card, cfg: dict) -> Card:
    """An ordinary reminder or calendar event that is about a birthday gets the BIRTHDAY template and symbol.  Nothing you wrote is removed."""
    b = cfg.get("birthdays", {})
    if not b.get("enabled", True) or not b.get("recognise", True) or not features.on(cfg, "birthdays"):
        return card
    if card.kind not in ("reminder", "calendar") or (card.template and card.template not in ("reminder", "calendar")):
        return card                                                      # friends' messages, email summaries, your own chosen templates: left alone
    text = card_text(card)
    if not BIRTHDAY.search(text):
        return card
    card.kind, card.template = "birthday", "birthday"
    card.vars.setdefault("date", (card.created or datetime.now()).strftime("%a %d %b"))        # the template's subtitle (reminders don't carry one)
    sym = symbol_id(cfg)
    if sym and sym not in card.icons:
        card.icons = [sym] + list(card.icons)
    if not card.heading:
        card.heading = extract_name(text)
    card.notes.append("recognised as a birthday")
    return card


def learn(card_or_text, cfgstore) -> dict | None:
    """With "learn" on: a birthday in a reminder that comes with a date is added to your list (once).  Returns the person added, or None."""
    c = cfgstore.data
    b = c.get("birthdays", {})
    if not (b.get("enabled", True) and b.get("learn", False) and features.on(c, "birthdays")):
        return None
    if isinstance(card_or_text, str):
        text = card_or_text
    else:                                                                # what was typed (the date may have been moved out of it into DUE), not what the app worked out
        text = f"{card_or_text.heading}\n{card_or_text.vars['written']}" if card_or_text.vars.get("written") else card_text(card_or_text, written_only=True)
    if not BIRTHDAY.search(text):
        return None
    name = extract_name(text)
    found = find_date(text, c["general"].get("date_order", "dmy"))
    if not name or not found:
        return None
    day, month, year, _ = found
    people = list(b.get("people", []))
    if any(p["name"].lower() == name.lower() and p["day"] == day and p["month"] == month for p in people) or len(people) >= 500:
        return None
    p = clean_person({"name": name, "day": day, "month": month, "year": year, "note": "added from an email"})
    if p is None:
        return None
    d = cfgstore.data
    d["birthdays"]["people"] = people + [p]
    cfgstore.replace(d)
    log.info("Added %s (%d/%d) to the birthday list from a reminder", name, day, month)
    return p


# ---------------------------------------------------------------------------- the 7am printer
def parse_hhmm(s: str) -> time:
    try:
        h, m = str(s).strip().split(":")
        return time(max(0, min(23, int(h))), max(0, min(59, int(m))))
    except Exception:
        return time(7, 0)


class BirthdayService(threading.Thread):
    """Prints today's birthdays once, at the print time (or when the Pi comes back that day)."""

    def __init__(self, cfg, svc):
        super().__init__(name="birthdays", daemon=True)
        self.cfg, self.svc = cfg, svc
        self.kick = threading.Event()
        self.status = {"last_check": "", "printed": 0}

    def run(self):
        while not self.svc.stop.is_set():
            try:
                self.check()
            except Exception:
                log.exception("Birthday check failed")
            self.kick.wait(30)
            self.kick.clear()

    def check(self, now: datetime | None = None) -> int:
        from .pipeline import local_now
        c = self.cfg.data
        b = c["birthdays"]
        now = now or local_now(c)
        self.status["last_check"] = now.strftime("%d %b %H:%M:%S")
        if not b.get("enabled", True) or not b.get("people") or not features.on(c, "birthdays"):
            return 0
        at = datetime.combine(now.date(), parse_hhmm(b.get("print_time", "07:00")), tzinfo=now.tzinfo)
        if now < at or (not b.get("catch_up", True) and now > at + timedelta(minutes=10)):
            return 0
        n = 0
        for p in b["people"]:
            if not p.get("enabled", True) or not occurs_on(p, now.date()):
                continue
            key = f"bday:{p['id']}:{now.date().isoformat()}"
            if self.svc.db.one("SELECT 1 FROM seen WHERE key=?", (key,)):
                continue
            self.svc.submit(person_card(p, c, now), 1)
            self.svc.db.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?)", (key, now.isoformat(timespec="seconds"), "birthday", p["name"]))
            n += 1
        self.status["printed"] += n
        return n
