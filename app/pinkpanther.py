"""Pink Panther episodes: you note one in a message, the box looks it up and prints its real name, date, director ...

HOW IT WORKS
  * A message about the Pink Panther ("Pink Panther" in the subject, or a line that mentions it and sounds like an episode) is
    turned into PINK PANTHER slips: the hand-drawn picture, the episode's real title, and whatever facts could be found.
  * The facts come from Wikipedia (its public API, over the internet): the list page's tables give title, release date, number,
    directors, volume/season/series columns and notes - whichever columns the table has; then the episode's own article adds
    director, running time and a one-line summary when there is one.  Anything not found is simply left off.
  * The episode list is downloaded once and kept in data/pinkpanther.json (refreshed after ``cache_days``), so later lookups are
    instant and work offline.  No internet and no list yet?  The slip still prints, with what you wrote.
  * Your words are matched loosely ("pink fink", "the pink phink", lower case, a missing "The"): a clear best match is used; a
    doubtful one is shown as "Did you mean ...".
  * One message can hold several episodes, one per line.

Nothing here talks to the internet unless ``pinkpanther.lookup`` is on.  Everything that does goes through ``fetch_json``, which
the tests replace.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from . import icons as icon_lib
from .config import DATA_DIR
from .models import Card, Section

log = logging.getLogger("pinkpanther")

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "JackInABox/1.0 (personal receipt-printer project; episode lookup)"
CACHE_FILE = DATA_DIR / "pinkpanther.json"
MENTION = re.compile(r"pink[\s\-]*panther", re.I)
CONTEXT_WORDS = re.compile(r"\b(episodes?|cartoons?|shorts?|shows?|season|series|volume|vol|watch|ep)\b", re.I)
ACCEPT, CLEAR_LEAD = 0.62, 0.05
OFFLINE_RETRY_SECONDS = 300
_lock = threading.RLock()
_last_failure = 0.0


class LookupError_(Exception):
    """Something stopped the lookup (no internet, Wikipedia changed ...).  The message is for the person."""


def fetch_json(url: str, timeout: float = 12) -> dict:
    """GET a JSON document.  (Replaced in tests, so nothing there touches the network.)"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(8_000_000).decode("utf-8", "replace"))


FETCH = fetch_json


# =========================================================================== reading Wikipedia's HTML tables
class _Page(HTMLParser):
    """Collects the wikitables (with their section headings) and, outside tables, the infobox and the first paragraphs."""
    SKIP_CLASSES = ("reference", "mw-editsection", "sortkey", "noprint", "navbar", "hatnote", "mw-cite-backlink", "reflist")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self.section = ""
        self.paragraphs: list[str] = []
        self._skip: list[str] = []                    # tag names being skipped (nesting)
        self._tab: dict | None = None
        self._tab_depth = 0
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        self._head: list[str] | None = None
        self._para: list[str] | None = None
        self._kind = ""                               # "wikitable" | "infobox"

    # -- helpers
    def _skipping(self) -> bool:
        return bool(self._skip)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").lower()
        style = (a.get("style") or "").replace(" ", "").lower()
        if self._skip:
            if tag == self._skip[0]:
                self._skip.append(tag)
            return
        if tag in ("style", "script") or "display:none" in style or any(c in cls.split() or c in cls for c in self.SKIP_CLASSES) and tag in ("span", "sup", "div", "style", "a", "table"):
            if tag not in ("br", "img", "hr"):
                self._skip = [tag]
            return
        if tag == "table":
            if self._tab is not None:
                self._tab_depth += 1
                return
            kind = "wikitable" if "wikitable" in cls else "infobox" if "infobox" in cls else ""
            if kind:
                self._tab, self._tab_depth, self._kind = {"section": self.section, "rows": [], "kind": kind}, 1, kind
            return
        if tag in ("h2", "h3", "h4") and self._tab is None:
            self._head = []
            return
        if self._tab is not None:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None and self._tab_depth == 1:
                def num(k):
                    try:
                        return max(1, min(60, int(re.sub(r"\D", "", a.get(k, "1")) or 1)))
                    except ValueError:
                        return 1
                self._cell = {"th": tag == "th", "col": num("colspan"), "row": num("rowspan"), "text": []}
            elif tag in ("br", "li") and self._cell is not None:
                self._cell["text"].append(", ")
            return
        if tag == "p" and self._head is None:
            self._para = []

    def handle_endtag(self, tag):
        if self._skip:
            if tag == self._skip[0]:
                self._skip.pop()
            return
        if tag in ("h2", "h3", "h4") and self._head is not None:
            self.section = re.sub(r"\s+", " ", "".join(self._head)).strip()
            self._head = None
            return
        if self._tab is not None:
            if tag == "table":
                self._tab_depth -= 1
                if self._tab_depth == 0:
                    self.tables.append(self._tab)
                    self._tab = None
            elif tag in ("td", "th") and self._cell is not None and self._row is not None and self._tab_depth == 1:
                self._cell["text"] = _tidy("".join(self._cell["text"]))
                self._row.append(self._cell)
                self._cell = None
            elif tag == "tr" and self._row is not None and self._tab_depth == 1:
                if self._row:
                    self._tab["rows"].append(self._row)
                self._row = None
            return
        if tag == "p" and self._para is not None:
            t = _tidy("".join(self._para))
            if t:
                self.paragraphs.append(t)
            self._para = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._cell is not None:
            self._cell["text"].append(data)
        elif self._head is not None:
            self._head.append(data)
        elif self._para is not None:
            self._para.append(data)


def _tidy(t: str) -> str:
    t = re.sub(r"\[\s*(?:\d+|[a-z]|note \d+|citation needed)\s*\]", "", t, flags=re.I)         # [1] [a] footnote marks
    t = re.sub(r"\(\d{4}-\d{2}-\d{2}\)", "", t)                                                 # a visible sort key like (1964-12-18)
    t = t.replace("\xa0", " ").replace("\u200b", "")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(?:\s*,\s*)+$", "", t.strip())
    t = re.sub(r"^(?:\s*,\s*)+", "", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r",(?:\s*,)+", ",", t)
    return t.strip()


def parse_page(html: str) -> _Page:
    p = _Page()
    p.feed(html or "")
    p.close()
    return p


def _grid(rows: list[list[dict]]) -> list[list[tuple[bool, str]]]:
    """Expand colspan/rowspan so every row has a cell for every column.  Cells are (is_header, text)."""
    out: list[list[tuple[bool, str]]] = []
    pending: dict[int, list] = {}                     # column -> [rows still to fill, cell]
    for row in rows:
        cells: list[tuple[bool, str]] = []
        col = 0
        it = iter(row)
        cur = next(it, None)
        while cur is not None or col in pending:
            if col in pending:
                left, c = pending[col]
                cells.append((c["th"], c["text"]))
                if left <= 1:
                    del pending[col]
                else:
                    pending[col][0] = left - 1
                col += 1
                continue
            for _ in range(cur["col"]):
                cells.append((cur["th"], cur["text"]))
                if cur["row"] > 1:
                    pending[col] = [cur["row"] - 1, cur]
                col += 1
            cur = next(it, None)
        out.append(cells)
    return out


ROLES = [
    ("title", r"^(?:title|episode title|cartoon|short|name|episode)$|title"),
    ("release", r"release|air date|aired|original air|premiere|first aired|date"),
    ("number", r"^(?:no\.?|#|number|episode no\.?|ep\.?|no\. overall|overall|no\. in (?:season|series))$|^no\b"),
    ("directed", r"direct"),
    ("written", r"writ|story|screenplay"),
    ("volume", r"volume|\bvol\b|dvd|blu-?ray|disc"),
    ("season", r"season|series|collection|package"),
    ("notes", r"note|comment|summary|description|award|remark"),
]


def _roles(headers: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for role, pat in ROLES:
        for i, h in enumerate(headers):
            if i in found.values():
                continue
            if re.search(pat, h.strip().lower()):
                found[role] = i
                break
    return found


@dataclass
class Episode:
    title: str
    release: str = ""
    number: str = ""
    directed: str = ""
    written: str = ""
    volume: str = ""
    season: str = ""
    notes: str = ""
    section: str = ""
    position: int = 0                  # 1-based place in its table (release order)
    of: int = 0                        # how many rows that table has
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return self.__dict__.copy()


def episodes_from_html(html: str, page_title: str = "") -> list[Episode]:
    """Every episode-like table on the page -> episodes.  Columns are recognised by their header text, so a table with
    different columns, or extra ones (volume, season ...), still works; a table with no title column is ignored."""
    out: list[Episode] = []
    for tab in parse_page(html).tables:
        if tab["kind"] != "wikitable":
            continue
        grid = _grid(tab["rows"])
        if len(grid) < 3:
            continue
        hi = next((i for i, r in enumerate(grid) if r and all(th for th, _ in r)), None)
        if hi is None:
            continue
        headers = [t for _, t in grid[hi]]
        roles = _roles(headers)
        if "title" not in roles:
            continue
        rows = []
        for r in grid[hi + 1:]:
            texts = [t for _, t in r]
            if not any(texts) or len(texts) < len(headers) - 1:
                continue
            if len(set(texts)) == 1 and len(texts[0]) > 25 and rows:                     # a summary row spanning the table
                rows[-1]["notes_extra"] = texts[0]
                continue
            rows.append({"texts": texts, "notes_extra": ""})
        valid = [r for r in rows if r["texts"][roles["title"]] and r["texts"][roles["title"]].lower() != headers[roles["title"]].lower()]
        if len(valid) < 3:
            continue
        for pos, r in enumerate(valid, 1):
            texts = r["texts"]
            get = lambda role: texts[roles[role]] if role in roles and roles[role] < len(texts) else ""
            title = re.sub(r'^[“"\']+|[”"\']+$', "", get("title")).strip()
            title = re.sub(r"\s*\(.*?\)\s*$", "", title) if re.search(r"\(\s*(?:\d{4}|film|short)\s*\)\s*$", title) else title
            notes = get("notes") or r["notes_extra"]
            claimed = set(roles.values())
            extra = {headers[i]: texts[i] for i in range(min(len(headers), len(texts))) if i not in claimed and headers[i] and texts[i]}
            out.append(Episode(title=title, release=get("release"), number=get("number"), directed=get("directed"), written=get("written"),
                               volume=get("volume"), season=get("season"), notes=notes, section=tab["section"] or page_title,
                               position=pos, of=len(valid), extra=extra))
    return out


def details_from_html(html: str) -> dict:
    """An article's infobox (director, running time ...) and its opening sentence."""
    page = parse_page(html)
    info: dict = {}
    for tab in page.tables:
        if tab["kind"] != "infobox":
            continue
        for row in _grid(tab["rows"]):
            if len(row) >= 2 and row[0][0] and row[1][1]:
                info[row[0][1].lower()] = row[1][1]
    pick = lambda *keys: next((info[k] for k in keys if info.get(k)), "")
    d = {"directed": pick("directed by", "director"), "written": pick("story by", "written by", "screenplay by"),
         "length": pick("running time", "runtime"), "release": pick("release date", "release dates", "released"),
         "music": pick("music by"), "voices": pick("voices of", "voices by")}
    para = next((p for p in page.paragraphs if len(p) > 60), "")
    if para:
        first = re.split(r"(?<=[.!?])\s+(?=[A-Z])", para)[0]
        d["summary"] = (first if len(first) <= 260 else first[:257].rsplit(" ", 1)[0] + "...")
    return {k: v for k, v in d.items() if v}


# =========================================================================== fetching and caching
def _get_html(title: str, fetch=None) -> str:
    q = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": "2", "redirects": "1", "disableeditsection": "1"})
    data = (fetch or FETCH)(f"{API}?{q}")
    if "error" in data:
        raise LookupError_(f"Wikipedia has no page called “{title}”")
    text = data.get("parse", {}).get("text", "")
    return text if isinstance(text, str) else text.get("*", "")


def _load(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    except OSError as e:
        log.warning("Couldn't save the Pink Panther cache: %s", e)


def refresh_index(cfg: dict, path: Path | None = None, fetch=None) -> dict:
    """Download the episode tables and keep them.  Raises LookupError_ if nothing usable came back."""
    path = path or CACHE_FILE
    pages = [p for p in cfg["pinkpanther"].get("pages", []) if str(p).strip()] or ["List of The Pink Panther cartoons"]
    eps: list[dict] = []
    problems: list[str] = []
    for title in pages:
        try:
            found = episodes_from_html(_get_html(str(title), fetch), str(title))
        except LookupError_ as e:
            problems.append(str(e))
            continue
        except Exception as e:
            problems.append(f"couldn't read “{title}” ({type(e).__name__}: {e})")
            continue
        if not found:
            problems.append(f"“{title}” has no episode table I could read")
        eps += [e.to_json() for e in found]
    if not eps:
        raise LookupError_("; ".join(problems) or "no episodes found")
    old = _load(path)
    data = {"fetched": datetime.now().isoformat(timespec="seconds"), "pages": pages, "episodes": eps, "details": old.get("details", {}), "problems": problems}
    _save(path, data)
    return data


def get_index(cfg: dict, path: Path | None = None, fetch=None, force: bool = False) -> dict | None:
    """The cached episode list, refreshed when it is old.  None = nothing available (and why: see ``LAST_PROBLEM``)."""
    global _last_failure, LAST_PROBLEM
    path = path or CACHE_FILE
    with _lock:
        data = _load(path)
        age_days = 9999.0
        if data.get("fetched"):
            try:
                age_days = (datetime.now() - datetime.fromisoformat(data["fetched"])).total_seconds() / 86400
            except ValueError:
                pass
        stale = force or not data.get("episodes") or age_days > float(cfg["pinkpanther"].get("cache_days", 30)) \
            or sorted(data.get("pages", [])) != sorted(cfg["pinkpanther"].get("pages", []))
        if stale and cfg["pinkpanther"].get("lookup", True):
            if force or time.time() - _last_failure > OFFLINE_RETRY_SECONDS:
                try:
                    data = refresh_index(cfg, path, fetch)
                    LAST_PROBLEM = ""
                except Exception as e:
                    _last_failure = time.time()
                    LAST_PROBLEM = str(e) if isinstance(e, LookupError_) else f"{type(e).__name__}: {e}"
                    log.warning("Pink Panther episode list unavailable: %s", LAST_PROBLEM)
                    if force:
                        raise
        return data if data.get("episodes") else None


LAST_PROBLEM = ""


def fetch_details(ep: dict, cfg: dict, path: Path | None = None, fetch=None) -> dict:
    """The episode's own article (infobox + first sentence), if it has one.  Cached per title."""
    path = path or CACHE_FILE
    key = norm(ep["title"])
    with _lock:
        data = _load(path)
        if key in data.get("details", {}):
            return data["details"][key]
    result: dict = {}
    try:
        q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": f'"{ep["title"]}" Pink Panther cartoon', "format": "json",
                                    "formatversion": "2", "srlimit": "4"})
        hits = (fetch or FETCH)(f"{API}?{q}").get("query", {}).get("search", [])
        for h in hits:
            t = h.get("title", "")
            if norm(re.sub(r"\s*\(.*?\)\s*$", "", t)) == key and re.search(r"pink panther|depatie|freleng|cartoon|short", h.get("snippet", "") + t, re.I):
                html = _get_html(t, fetch)
                if MENTION.search(re.sub(r"<[^>]+>", " ", html)):
                    result = details_from_html(html)
                    break
    except Exception as e:
        log.info("No article details for %r (%s)", ep["title"], e)
        return {}                                                  # don't cache a network failure
    with _lock:
        data = _load(path)
        data.setdefault("details", {})[key] = result
        _save(path, data)
    return result


# =========================================================================== understanding what you wrote
def norm(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(?:the|a|an)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def score(query: str, title: str) -> float:
    nq, nt = norm(query), norm(title)
    if not nq or not nt:
        return 0.0
    if nq == nt:
        return 1.0
    qt, tt = set(nq.split()), set(nt.split())
    jac = len(qt & tt) / max(1, len(qt | tt))
    ratio = difflib.SequenceMatcher(None, nq, nt).ratio()
    contains = 0.92 if (len(nt) >= 4 and re.search(rf"\b{re.escape(nt)}\b", nq)) or (len(nq) >= 4 and re.search(rf"\b{re.escape(nq)}\b", nt)) else 0.0
    return max(contains, 0.55 * ratio + 0.45 * jac, ratio * 0.97 if ratio > 0.8 else 0.0)


def context_boost(ep: dict, query: str, context: str) -> float:
    """What the REST of the message says about the episode: a year, a director, a volume/season number, a detail from its notes.
    It can tip a close call but never creates a match on its own."""
    ctx = norm(MENTION.sub(" ", context).replace(query, " "))                    # "Pink Panther" itself says nothing about WHICH episode
    if not ctx:
        return 0.0
    boost = 0.0
    years = set(re.findall(r"\b(19[6-9]\d)\b", context))
    ep_years = set(re.findall(r"\b(19[6-9]\d)\b", ep.get("release", "") or ""))
    if years and ep_years:
        boost += 0.15 if years & ep_years else -0.06
    for name in re.findall(r"[A-Za-z]{4,}", ep.get("directed", "") or ""):
        if re.search(rf"\b{name.lower()}\b", ctx):
            boost += 0.10
            break
    for label, field in (("volume|vol", "volume"), ("season|series", "season")):
        m = re.search(rf"\b(?:{label})\s*(\d+)", context, re.I)
        if m and ep.get(field):
            boost += 0.12 if re.search(rf"\b0*{int(m.group(1))}\b", ep[field]) else -0.08
    words = {w for w in norm(ep.get("notes", "")).split() if len(w) >= 5} - set(norm(query).split())
    boost += min(0.09, 0.03 * sum(1 for w in words if re.search(rf"\b{w}\b", ctx)))
    return boost


def rank(query: str, episodes: list[dict], context: str = "") -> list[tuple[float, dict]]:
    out = []
    for e in episodes:
        base = score(query, e["title"])
        if base <= 0:
            continue
        out.append((min(1.0, base + (context_boost(e, query, context) if base >= 0.45 else 0.0)), e))
    return sorted(out, key=lambda x: -x[0])


def match(query: str, episodes: list[dict], context: str = "") -> tuple[dict | None, list[dict], float]:
    """(the episode if we are CONFIDENT, else None; the closest few as suggestions; the confidence 0..1).

    Confident = a good score and a clear lead over the runner-up (the message's year / director / volume / notes can tip it).
    Doubtful matches are never printed as fact: they come back as suggestions."""
    nq = norm(query)
    m = re.search(r"\b(?:episode|ep|no|number|#)\s*(\d{1,3})\b", query, re.I)
    if m and len(nq.split()) <= 4:                                                          # "episode 12": by number, if the list has numbers
        want = int(m.group(1))
        hit = [e for e in episodes if (re.sub(r"\D", "", e.get("number", "") or "") or "x").lstrip("0") == str(want)]
        if len(hit) == 1:
            return hit[0], [], 1.0
    ranked = rank(query, episodes, context)
    if not ranked:
        return None, [], 0.0
    best = ranked[0]
    second = next((x for x in ranked[1:] if norm(x[1]["title"]) != norm(best[1]["title"])), (0.0, None))
    sugg, seen = [], set()
    for sc, e in ranked:
        if sc >= 0.4 and norm(e["title"]) not in seen:
            seen.add(norm(e["title"]))
            sugg.append(e)
        if len(sugg) == 3:
            break
    if best[0] >= ACCEPT and best[0] - second[0] >= CLEAR_LEAD:
        return best[1], [], best[0]
    return None, sugg, best[0]


_FILLER_START = re.compile(r"^(?:(?:please|pls|watch|see|find|look up|lookup|the|episode|episodes|ep|cartoon|cartoons|short|shorts|show|series|season|volume|vol|called|named|titled|about|from|of|-|:|–|—)\b[\s:\-–—]*)+", re.I)
_FILLER_END = re.compile(r"(?:[\s:\-–—,]*\b(?:episode|episodes|ep|cartoon|cartoons|short|shorts|show|please|pls|to watch|on sunday|tonight)\b)+[\s.!]*$", re.I)


def clean_query(line: str) -> str:
    t = re.sub(r"^\s*(?:[-*•>]+|\d{1,2}[.)])\s+", "", line)                  # list bullets / "1." numbering
    t = MENTION.sub(" ", t)
    t = re.sub(r"\bshow\b", "show", t, flags=re.I)
    t = re.sub(r"[“”\"]", "", t)
    t = _FILLER_START.sub("", t.strip())
    t = _FILLER_END.sub("", t.strip())
    return re.sub(r"\s+", " ", t).strip(" -:–—,.!?")


def plain_line(line: str) -> str:
    """The line as you wrote it, minus bullets, quotes and the words "Pink Panther" (for showing when nothing matched)."""
    t = re.sub(r"^\s*(?:[-*•>]+|\d{1,2}[.)])\s+", "", line)
    t = re.sub(r"[“”\"]", "", MENTION.sub(" ", t))
    return re.sub(r"\s+", " ", t).strip(" -:–—,.!?")


def candidate_lines(inc, cfg: dict) -> tuple[list[tuple[str, str]], bool, list[str]]:
    """What in this message could be an episode?  Returns ([(original line, query)], strong context?, symbol ids from [tags])."""
    subj, sym = icon_lib.extract_tags(inc.subject or "", cfg)
    body, bsym = icon_lib.extract_tags(inc.body or "", cfg)
    icons = list(dict.fromkeys(sym + bsym))
    lines = [ln.strip() for ln in re.split(r"[\n;]+", body) if ln.strip()]
    strong = bool(MENTION.search(subj)) or any(MENTION.search(ln) and CONTEXT_WORDS.search(ln) for ln in lines)
    cands: list[tuple[str, str]] = []
    if MENTION.search(subj):
        rest = clean_query(subj)
        if rest and len(norm(rest).split()) <= 7 and not CONTEXT_WORDS.fullmatch(rest.lower()):
            cands.append((subj, rest))
    for ln in lines:
        if strong or MENTION.search(ln):
            q = clean_query(ln)
            if q:
                cands.append((ln, q))
    return cands, strong, icons


# =========================================================================== the slips
def fact_lines(ep: dict, details: dict, notes_ok: bool = True) -> list[str]:
    lines: list[str] = []
    add = lambda label, value: lines.append(f"{label}: {value}") if value else None
    add("RELEASED", ep.get("release") or details.get("release"))
    if ep.get("number"):
        add("EPISODE", f"No. {ep['number']}")
    elif ep.get("position") and ep.get("of"):
        add("EPISODE", f"#{ep['position']} of {ep['of']} in release order")
    add("SERIES", ep.get("season") or (ep.get("section") if ep.get("section") and not re.search(r"list of", ep["section"], re.I) else ""))
    add("VOLUME", ep.get("volume"))
    add("DIRECTOR", ep.get("directed") or details.get("directed"))
    add("WRITER", ep.get("written") or details.get("written"))
    add("LENGTH", details.get("length"))
    for k, v in (ep.get("extra") or {}).items():
        if len(v) < 40 and re.search(r"studio|company|network|production|code|format|distribut", k, re.I):
            add(k.upper()[:14], v)
    if notes_ok:
        add("NOTES", ep.get("notes") or details.get("summary"))
    return lines


def make_card(query: str, original: str, ep: dict | None, suggestions: list[dict], details: dict, cfg: dict, now: datetime,
              icons: list[str], problem: str = "") -> Card:
    pp = cfg["pinkpanther"]
    if ep:
        sections = [Section("text", text="\n".join(fact_lines(ep, details)) or "(No more details found.)")]
        heading = ep["title"]
        if norm(original) != norm(ep["title"]) and norm(clean_query(original)) != norm(ep["title"]):
            sections.append(Section("text", label="YOU WROTE", text=original))
        stamp = ""
    else:
        heading = (plain_line(original) or query)[:80].strip() or "Pink Panther"
        why = problem or ("Lookup is switched off." if not pp.get("lookup", True) else "I couldn't match this to an episode.")
        text = [why]
        if suggestions:
            text.append("Did you mean: " + "; ".join(s["title"] for s in suggestions) + "?")
        sections = [Section("text", text="\n".join(text))]
        stamp = ""
    card = Card(kind="pinkpanther", template="pinkpanther", heading=heading, sections=sections, icons=icons, source="email", created=now, stamp=stamp,
                vars={"graphic_style": pp.get("style", "shaded"), "graphic": "on" if pp.get("show_image", True) else "off", "date": now.strftime("%a %d %b")})
    return card


def build(inc, cfg: dict, now: datetime, path: Path | None = None, fetch=None):
    """(Pink Panther cards, the rest of the message to print normally or None).  No cards if the message isn't about an episode.

    An episode is only stated as fact when the match is confident; otherwise the slip says what you wrote and what it might be."""
    pp = cfg.get("pinkpanther", {})
    if not pp.get("enabled", True) or not MENTION.search(f"{inc.subject}\n{inc.body}"):
        return [], inc
    cands, strong, icons = candidate_lines(inc, cfg)
    if not cands and not strong:
        return [], inc
    context = f"{inc.subject}\n{inc.body}"
    index = get_index(cfg, path, fetch) if pp.get("lookup", True) else None
    episodes = (index or {}).get("episodes", [])
    problem = ""
    if pp.get("lookup", True) and not episodes:
        problem = f"I couldn't look it up just now ({LAST_PROBLEM or 'the episode list has not been downloaded yet'})."
    cards: list[Card] = []
    used: set[str] = set()
    seen_titles: set[str] = set()
    for original, query in cands[: int(pp.get("max_episodes", 8))]:
        ep, sugg, _conf = match(query, episodes, context) if episodes else (None, [], 0.0)
        if ep is None and not strong:
            continue                                                  # a passing mention (a toy, a film night), not an episode: leave it alone
        if ep is not None:
            if norm(ep["title"]) in seen_titles:
                used.add(original)
                continue
            seen_titles.add(norm(ep["title"]))
        details = fetch_details(ep, cfg, path, fetch) if (ep and pp.get("details", True) and pp.get("lookup", True)) else {}
        cards.append(make_card(query, original, ep, sugg, details, cfg, now, icons, problem))
        used.add(original)
    if not cards and strong:                                           # "Pink Panther" with nothing recognisable: still print the picture + the words
        shown = (inc.body or "").strip() or inc.subject
        cards.append(make_card("Pink Panther", shown, None, [], {}, cfg, now, icons,
                               problem or "Nothing here looked like an episode title."))
        return cards, None
    if not cards:
        return [], inc
    if strong:
        return cards, None
    rest = "\n".join(ln for ln in (inc.body or "").split("\n") if ln.strip() and not any(u in ln for u in used)).strip()
    return cards, (replace(inc, subject="", body=rest) if rest else None)
