"""Emails written as  "Label: value"  lines (what the iPhone Shortcut / Apple Intelligence sends for movies, shows and Pink Panther episodes):

    Movie/Show: Pink 8-Ball
    Relevant Information: Pink 8-Ball is an animated comedy short ...

    Movie Name: Sound of Freedom
    Description: Sound of Freedom follows Tim Ballard ...
    Additional Information:
    Release Date: July 4, 2023
    Director: Alejandro Monteverde

Each title label starts a record; the lines after it belong to that record until the next title label.  From a record we keep only what
the slips use (title, description -> plot, release date / year, type, season, where to watch); Director, Lead Actor, Based on, Themes
and everything else are dropped.  A description can run over several lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# label (lower case, letters and "/" only) -> what it means
TITLE_STRONG = {"movie/show", "show/movie", "movie or show", "movie or tv show", "movie/tv show", "movie/tv", "movie name", "movie title", "movie", "film", "film name", "film title",
                "show", "show name", "show title", "tv show", "tv show name", "tv series", "tv", "series", "series name", "cartoon", "cartoon name", "watch", "to watch"}
TITLE_WEAK = {"title", "name", "episode", "episode name", "episode title"}              # only trusted when the subject says it's about things to watch
DESC = {"relevant information", "relevant info", "description", "plot", "summary", "overview", "synopsis", "about", "storyline", "story", "details", "info", "information", "plot overview",
        "plot summary"}
DATE = {"release date", "released", "release", "date", "first aired", "first air date", "air date", "aired", "premiere", "premiered", "premiere date", "date released", "originally released"}
YEAR = {"year", "release year"}
TYPE = {"type", "format", "category", "kind"}
SERVICE = {"where to watch", "streaming on", "available on", "platform", "streaming", "service", "watch on", "streaming service", "network", "channel"}
SEASON = {"season", "volume", "vol"}

_LABEL = re.compile(r"^\s*(?:[-*•>]+\s*)?([A-Za-z][A-Za-z /&'’]{1,34}?)\s*[:：]\s*(.*)$")
_MD = re.compile(r"\*\*|__|`")
_PAREN = re.compile(r"\s*[\(\[]([^()\[\]]{1,40})[\)\]]\s*$")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_TV_HINT = re.compile(r"\b(?:tv|television)\b|\bseries\b|\bmini-?series\b|\bsitcom\b|\bseason\b|\bshow\b|\banime\b", re.I)
_MOVIE_HINT = re.compile(r"\b(?:movie|film|feature)\b", re.I)
_UNKNOWN = re.compile(r"^(?:n/?a|none|unknown|tba|tbd|not\s+(?:available|specified)|-+|\?+)\.?$", re.I)


def norm_label(s: str) -> str:
    return re.sub(r"\s*/\s*", "/", re.sub(r"[^a-z/ ]", "", s.lower().replace("&", " and "))).strip()


@dataclass
class Record:
    title: str = ""            # as cleaned: no "(1992)" or "(TV series)"
    desc: str = ""
    date: str = ""             # a release date as written ("July 4, 2023"); blank if only a year is known
    year: str = ""
    type: str = ""             # "movie" | "tv" | ""
    on: str = ""
    season: str = ""
    label: str = ""            # the title label that was used ("movie name"), for the type
    raw: list = field(default_factory=list)      # the original lines of this record (to hand on what isn't ours)


def clean_title(value: str) -> tuple[str, str, str, str]:
    """"Death Becomes Her (1992)" -> (title, year, type, season).  Quotes, bold marks and a trailing "(TV series)" / "(2019 film)" are taken out."""
    t = _MD.sub("", value).strip().strip("\"“”'‘’ ").strip()
    year = kind = season = ""
    for _ in range(3):
        m = _PAREN.search(t)
        if not m:
            break
        inner = m.group(1)
        y = _YEAR.search(inner)
        if not (y or _TV_HINT.search(inner) or _MOVIE_HINT.search(inner) or re.search(r"\bs(?:eason)?\s*\d", inner, re.I)):
            break                                                             # "(Part Two)" belongs to the title
        year = year or (y.group(0) if y else "")
        if _TV_HINT.search(inner):
            kind = "tv"
        elif _MOVIE_HINT.search(inner):
            kind = kind or "movie"
        sm = re.search(r"\bs(?:eason)?\s*(\d{1,2})\b", inner, re.I)
        season = season or (sm.group(1) if sm else "")
        t = t[:m.start()].strip()
    m = re.search(r"\s*[-–—:,]\s*(?:season|series)\s*(\d{1,2})\s*$", t, re.I)
    if m:
        season, kind = season or m.group(1), kind or "tv"
        t = t[:m.start()].strip()
    return t.strip(" -–—:,"), year, kind, season


def parse(body: str, allow_weak: bool = False) -> list[Record]:
    """The records in the body (empty if it isn't written as labelled lines).  ``allow_weak``: also treat "Title:" / "Name:" / "Episode:" as a title label."""
    recs: list[Record] = []
    cur: Record | None = None
    role = ""
    for ln in (body or "").splitlines():
        s = _MD.sub("", ln).rstrip()
        if not s.strip():
            role = ""
            if cur is not None:
                cur.raw.append(ln)
            continue
        m = _LABEL.match(s)
        label = norm_label(m.group(1)) if m else ""
        value = m.group(2).strip() if m else ""
        if m and (label in TITLE_STRONG or (allow_weak and label in TITLE_WEAK)) and value and not (label == "episode" and value.isdigit()) and len(value) <= 140:
            title, year, kind, season = clean_title(value)
            if title:
                cur = Record(title=title, year=year, type=kind, season=season, label=label, raw=[ln])
                recs.append(cur)
                role = "title"
                continue
        if cur is None:
            continue
        cur.raw.append(ln)
        is_field = bool(m) and 2 <= len(label) <= 30 and (len(label.split()) <= 2 or label in DESC | DATE | YEAR | TYPE | SERVICE | SEASON)    # "Caught in a bitter feud: ..." is the description going on
        if is_field:
            role = ""
            if _UNKNOWN.match(value):
                value = ""
            if label in DESC:
                cur.desc, role = value, "desc"
            elif label in DATE and value and not cur.date and not cur.year:
                y = _YEAR.search(value)
                if y and re.search(r"[A-Za-z]{3,}\s+\d{1,2}|\d{1,2}[/.\- ]\d{1,2}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,}", value):
                    cur.date, cur.year = re.sub(r"\s*\([^)]*\)", "", value).strip(" .,"), y.group(0)
                elif y:
                    cur.year = y.group(0)
            elif label in YEAR and value:
                y = _YEAR.search(value)
                cur.year = cur.year or (y.group(0) if y else "")
            elif label in TYPE and value:
                cur.type = cur.type or ("tv" if _TV_HINT.search(value) else "movie" if _MOVIE_HINT.search(value) else "")
            elif label in SERVICE and value:
                cur.on = cur.on or value[:30]
            elif label in SEASON and value:
                sm = re.match(r"\s*(\d{1,2})\b", value)
                cur.season = cur.season or (sm.group(1) if sm else "")
            # any other label (Director, Lead Actor, Based on, Themes, Cast, Genre ...) is simply dropped
        elif role == "desc":
            cur.desc = (cur.desc + " " + s.strip()).strip()                    # a description carried over onto the next line
    for r in recs:
        r.desc = re.sub(r"\s+", " ", r.desc).strip()
        if not r.type:
            label_kind = ("movie" if re.search(r"movie|film", r.label) and not re.search(r"show|series|tv", r.label)
                          else "tv" if re.search(r"tv|series|show", r.label) and not re.search(r"movie|film", r.label) else "")
            r.type = label_kind
    return recs
