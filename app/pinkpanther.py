"""Pink Panther episodes: you note one in a message, the box prints a PINK PANTHER slip with its season, episode, date and plot.

WHERE THE FACTS COME FROM
  * The episode list is built in (app/pinkpanther_episodes.json, made from the Pink Panther Wiki's "The Pink Panther Show" guide by
    tools/pinkpanther_from_pdf.py): every short, and which season and episode number of the TV show it was in (the same short is often
    repeated in later seasons).  So recognising an episode, its season and its number need no internet.
  * The internet (Wikipedia/Fandom, only if "lookup" is on) adds what the list doesn't have: the plot text and an exact release date.
    The plot is turned into a 2-4 sentence, spoiler-free overview by the local AI when AI mode is on (checked so it can't make things
    up), otherwise it is the opening of the plot text.  Anything not found is simply left off.

HOW A MESSAGE IS READ
  * "Pink Panther" in the subject says what the message is about; the body, however long, is then read for the episodes in it: exact
    titles anywhere in the text, quoted titles, and short title-like lines (matched loosely).  Signatures, disclaimers and quoted
    replies are ignored first.
  * An episode is only stated as fact when the match is CONFIDENT (a clear best match, helped by a year or season in the message).
    Otherwise the slip just shows the name as you wrote it: your best guess is your own words, never a wrong episode.
  * "Pink Panther" mentioned in passing ("buy a pink panther toy") is left alone unless it names an episode.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from . import icons as icon_lib
from .config import APP_DIR, DATA_DIR
from .models import Card, Section

log = logging.getLogger("pinkpanther")

BUNDLED = APP_DIR / "pinkpanther_episodes.json"
CACHE_FILE = DATA_DIR / "pinkpanther.json"          # what was learned online (plots, dates), kept so it is looked up once
FANDOM_API = "https://pinkpanther.fandom.com/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "JackInABox/1.0 (personal receipt-printer project; episode lookup)"
MENTION = re.compile(r"pink[\s\-]*panther", re.I)
CONTEXT_WORDS = re.compile(r"\b(episodes?|cartoons?|shorts?|shows?|season|series|volume|vol|watch|ep)\b", re.I)
ACCEPT, CLEAR_LEAD = 0.62, 0.05
_lock = threading.RLock()
LAST_PROBLEM = ""                                   # why the last online lookup failed (for the settings page)


class LookupError_(Exception):
    """An online lookup failed; the message is for the person."""


def fetch_json(url: str, timeout: float = 12) -> dict:
    """GET a JSON document.  (Replaced in tests, so nothing there touches the network.)"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(8_000_000).decode("utf-8", "replace"))


FETCH = fetch_json


# =========================================================================== the built-in episode list
@lru_cache(maxsize=1)
def _bundled() -> dict:
    try:
        return json.loads(BUNDLED.read_text("utf-8"))
    except (OSError, ValueError):
        return {"seasons": [], "shorts": []}


def load_episodes() -> list[dict]:
    """Every short: {title, appearances: [{season, show, years, network, episode}], years}.  First showing first."""
    out = []
    for e in _bundled().get("shorts", []):
        years = " ".join(dict.fromkeys(a["years"] for a in e["appearances"]))
        out.append({**e, "years": years})
    return out


# =========================================================================== reading a web page (plot text, release date)
class _Page(HTMLParser):
    """The paragraphs (with the section heading each sits under) and the infobox facts of an article, from Fandom or Wikipedia HTML."""
    SKIP_CLASSES = ("reference", "mw-editsection", "sortkey", "noprint", "navbar", "hatnote", "mw-cite-backlink", "reflist", "toc")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section = ""
        self.para_info: list[tuple[str, str]] = []
        self.infobox: dict[str, str] = {}
        self.tables: list[list[list[tuple[bool, str]]]] = []     # infobox tables (Wikipedia): rows of (is_header, text)
        self._skip: list[str] = []
        self._head: list[str] | None = None
        self._para: list[str] | None = None
        self._pi: list | None = None                     # [kind, tag, depth, text parts]  (Fandom's "portable infobox")
        self._pi_label = ""
        self._tab: list | None = None
        self._tab_depth = 0
        self._row: list | None = None
        self._cell: list | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").lower()
        style = (a.get("style") or "").replace(" ", "").lower()
        if self._skip:
            if tag == self._skip[0]:
                self._skip.append(tag)
            return
        if tag in ("style", "script") or "display:none" in style or (any(c in cls for c in self.SKIP_CLASSES) and tag in ("span", "sup", "div", "style", "a", "table", "ul")):
            if tag not in ("br", "img", "hr"):
                self._skip = [tag]
            return
        if self._pi is not None:
            if tag == self._pi[1]:
                self._pi[2] += 1
            elif tag in ("br", "li"):
                self._pi[3].append(", ")
            return
        if "pi-data-label" in cls or "pi-data-value" in cls:
            self._pi = ["label" if "pi-data-label" in cls else "value", tag, 1, []]
            return
        if tag == "table":
            if self._tab is not None:
                self._tab_depth += 1
            elif "infobox" in cls:
                self._tab, self._tab_depth = [], 1
            return
        if self._tab is not None:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None and self._tab_depth == 1:
                self._cell = [tag == "th", []]
            elif tag in ("br", "li") and self._cell is not None:
                self._cell[1].append(", ")
            return
        if tag in ("h2", "h3", "h4"):
            self._head = []
        elif tag == "p" and self._head is None:
            self._para = []

    def handle_endtag(self, tag):
        if self._skip:
            if tag == self._skip[0]:
                self._skip.pop()
            return
        if self._pi is not None:
            if tag == self._pi[1]:
                self._pi[2] -= 1
                if self._pi[2] == 0:
                    text = _tidy("".join(self._pi[3]))
                    if self._pi[0] == "label":
                        self._pi_label = text
                    elif self._pi_label and text:
                        self.infobox.setdefault(self._pi_label.lower(), text)
                    self._pi = None
            return
        if self._tab is not None:
            if tag == "table":
                self._tab_depth -= 1
                if self._tab_depth == 0:
                    self.tables.append(self._tab)
                    self._tab = None
            elif tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append((self._cell[0], _tidy("".join(self._cell[1]))))
                self._cell = None
            elif tag == "tr" and self._row is not None and self._tab_depth == 1:
                if self._row:
                    self._tab.append(self._row)
                self._row = None
            return
        if tag in ("h2", "h3", "h4") and self._head is not None:
            self.section = re.sub(r"\s+", " ", "".join(self._head)).strip()
            self._head = None
        elif tag == "p" and self._para is not None:
            t = _tidy("".join(self._para))
            if t:
                self.para_info.append((self.section, t))
            self._para = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._pi is not None:
            self._pi[3].append(data)
        elif self._cell is not None:
            self._cell[1].append(data)
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
    return re.sub(r",(?:\s*,)+", ",", t).strip()


_DATE = re.compile(r"(?:[A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Z][a-z]+ \d{4})")
_RELEASED = re.compile(r"(?:released|premiered|aired|debuted|first shown)(?: theatrically)?(?: on| in)? (" + _DATE.pattern + ")")


def details_from_html(html: str) -> dict:
    """{"release": the exact date, "plot_source": the text a plot overview is made from} - whichever the page has."""
    p = _Page()
    p.feed(html or "")
    p.close()
    info = dict(p.infobox)
    for tab in p.tables:
        for row in tab:
            if len(row) >= 2 and row[0][0] and row[1][1]:
                info.setdefault(row[0][1].lower(), row[1][1])
    plot = [t for sec, t in p.para_info if re.search(r"plot|synopsis|summary|premise|story|overview", sec, re.I) and len(t) > 30]
    intro = [t for sec, t in p.para_info if not re.search(r"trivia|reception|production|cast|home media|references|see also", sec, re.I) and len(t) > 30]
    src = " ".join(plot or intro)[:1800]
    out: dict = {}
    if src:
        out["plot_source"] = src
    raw = next((info[k] for k in ("release date", "released", "release", "premiere", "air date", "first aired", "original air date", "date") if info.get(k)), "")
    m = _DATE.search(raw)
    if m:
        out["release"] = m.group(0)
    else:
        m = _RELEASED.search(src)
        if m:
            out["release"] = m.group(1)
        elif raw and len(raw) <= 30:
            out["release"] = raw
    return out


def _get_html(api: str, title: str, fetch=None) -> str:
    q = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": "2", "redirects": "1", "disableeditsection": "1"})
    data = (fetch or FETCH)(f"{api}?{q}")
    if "error" in data:
        raise LookupError_(f"no page called “{title}”")
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


def _remember(key: str, path: Path | None, **values) -> None:
    with _lock:
        data = _load(path or CACHE_FILE)
        data.setdefault("details", {}).setdefault(key, {}).update(values)
        _save(path or CACHE_FILE, data)


def fetch_details(ep: dict, cfg: dict, path: Path | None = None, fetch=None) -> dict:
    """The plot text and release date from the web: the Pink Panther Wiki (Fandom) page named like the short, else Wikipedia.
    Cached per title (including "nothing found"), so a short is looked up once."""
    global LAST_PROBLEM
    path = path or CACHE_FILE
    key = norm(ep["title"])
    with _lock:
        cached = _load(path).get("details", {}).get(key)
        if cached is not None:
            return cached
    result: dict = {}
    try:
        try:
            result = details_from_html(_get_html(FANDOM_API, re.sub(r"\s*\((?:Ant|Inspector)? ?[Ss]hort\)\s*$", "", ep["title"]), fetch))
        except LookupError_:
            result = {}
        if not result.get("plot_source"):
            q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": f'"{ep["title"]}" Pink Panther cartoon', "format": "json",
                                        "formatversion": "2", "srlimit": "4"})
            for h in (fetch or FETCH)(f"{WIKIPEDIA_API}?{q}").get("query", {}).get("search", []):
                t = h.get("title", "")
                if norm(re.sub(r"\s*\(.*?\)\s*$", "", t)) == key and re.search(r"pink panther|depatie|freleng|cartoon|short", h.get("snippet", "") + t, re.I):
                    html = _get_html(WIKIPEDIA_API, t, fetch)
                    if MENTION.search(re.sub(r"<[^>]+>", " ", html)):
                        result = {**details_from_html(html), **{k: v for k, v in result.items() if v}}
                        break
        LAST_PROBLEM = ""
    except Exception as e:
        LAST_PROBLEM = f"{type(e).__name__}: {e}"
        log.info("No online details for %r (%s)", ep["title"], LAST_PROBLEM)
        return {}                                                     # don't remember a network failure as "nothing exists"
    _remember(key, path, **result)
    return result


# =========================================================================== matching what you wrote
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


def _season_covers(season: str, want: int) -> bool:
    m = re.fullmatch(r"(\d+)(?:–(\d+))?", season)
    return bool(m) and int(m.group(1)) <= want <= int(m.group(2) or m.group(1))


def context_boost(ep: dict, query: str, context: str) -> float:
    """What the REST of the message says about the episode: a year it aired, a season number.  It can tip a close call but
    never creates a match on its own."""
    ctx = MENTION.sub(" ", context).replace(query, " ")
    boost = 0.0
    years = set(re.findall(r"\b(19[6-9]\d)\b", ctx))
    ep_years: set[str] = set()
    for a, b in re.findall(r"(\d{4})(?:–(\d{4}))?", ep.get("years", "") or ""):
        ep_years |= set(map(str, range(int(a), int(b or a) + 1)))
    if years and ep_years:
        boost += 0.15 if years & ep_years else -0.06
    m = re.search(r"\b(?:season|series)\s*(\d+)\b", ctx, re.I)
    if m:
        boost += 0.12 if any(_season_covers(a["season"], int(m.group(1))) for a in ep.get("appearances", [])) else -0.08
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
    """(the episode if we are CONFIDENT, else None; the closest few; the confidence 0..1).

    Confident = a good score and a clear lead over the runner-up (the message's year / season can tip it).  A doubtful match is
    never used: the slip shows your own wording instead."""
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


# =========================================================================== understanding what you wrote
_FILLER_START = re.compile(r"^(?:(?:please|pls|watch|see|find|look up|lookup|the|episode|episodes|ep|cartoon|cartoons|short|shorts|show|series|season|volume|vol|called|named|titled|about|from|of|-|:|–|—)\b[\s:\-–—]*)+", re.I)
_FILLER_END = re.compile(r"(?:[\s:\-–—,]*\b(?:episode|episodes|ep|cartoon|cartoons|short|shorts|show|please|pls|to watch|on sunday|tonight)\b)+[\s.!]*$", re.I)
_STOP = set("""to watch see the a an some few list of episodes episode cartoons cartoon shorts short show please for my on this that these those want like i me
sunday saturday monday tuesday wednesday thursday friday tonight tomorrow today weekend with kids and or in at it is are be about from""".split())
_GREETING = re.compile(r"^\s*(?:hi|hello|hey|dear|thanks|thank you|cheers|regards|kind regards|best|sent from)\b", re.I)


def clean_query(line: str) -> str:
    t = re.sub(r"^\s*(?:[-*•>]+|\d{1,2}[.)])\s+", "", line)                  # list bullets / "1." numbering
    t = MENTION.sub(" ", t)
    t = re.sub(r"[“”\"]", "", t)
    t = _FILLER_START.sub("", t.strip())
    t = _FILLER_END.sub("", t.strip())
    t = re.sub(r"\s+", " ", t).strip(" -:–—,.!?")
    return "" if t and all(w in _STOP for w in norm(t).split()) else t               # "episodes to watch" says nothing about which one


def plain_line(line: str) -> str:
    """The line as you wrote it, minus bullets, quotes and the words "Pink Panther"."""
    t = re.sub(r"^\s*(?:[-*•>]+|\d{1,2}[.)])\s+", "", line)
    t = re.sub(r"[“”\"]", "", MENTION.sub(" ", t))
    return re.sub(r"\s+", " ", t).strip(" -:–—,.!?")


def looks_like_a_title(q: str) -> bool:
    """A short bare phrase, not a sentence or a greeting."""
    words = q.split()
    return 1 <= len(words) <= 7 and not _GREETING.match(q) and not re.search(r"[:?]\s*$|[.!]\s+\w", q)


_SIGNOFF = re.compile(r"^\s*(?:thanks|thank you|thx|cheers|regards|kind regards|best regards|best wishes|best|warm regards|ta|love|talk soon|sent from)\b[\s,!.]*", re.I)


def drop_signoff(lines: list[str], sender: str = "") -> list[str]:
    """"Thanks,\nJack" at the end is a sign-off, not an episode: cut from the sign-off word onwards (only when it is in the last few lines)."""
    for i, ln in enumerate(lines):
        if i >= len(lines) - 4 and _SIGNOFF.match(ln) and len(ln.split()) <= 4:
            return lines[:i]
    first = (sender or "").split()[:1]
    if lines and first and len(lines[-1].split()) <= 2 and lines[-1].strip(" ,.!-").lower() == first[0].lower():
        return lines[:-1]
    return lines


def long_message(body: str) -> bool:
    return len([ln for ln in re.split(r"[\n;]+", body or "") if ln.strip()]) > 6 or len(body or "") > 500


def tidy_body(body: str) -> str:
    """A long email: drop the signature, disclaimers, quoted replies and links first, so only what you wrote is read."""
    if not long_message(body):
        return body or ""
    from .emailclean import clean_forwarded
    try:
        return clean_forwarded(body) or body
    except Exception:
        return body


def scan_titles(line: str, titles: list[tuple[str, dict]]) -> list[dict]:
    """Episodes whose exact title appears in the line (a longer title swallows a shorter one inside it)."""
    n = norm(line)
    hits = []
    for nt, ep in titles:
        m = re.search(rf"(?<![a-z0-9]){re.escape(nt)}(?![a-z0-9])", n)
        if m:
            hits.append((m.start(), m.end(), ep))
    keep = [h for h in hits if not any(o is not h and o[0] <= h[0] and h[1] <= o[1] and (o[1] - o[0]) > (h[1] - h[0]) for o in hits)]
    return [ep for _, _, ep in sorted(keep, key=lambda h: h[0])]


def find_episodes(inc, cfg: dict, episodes: list[dict]) -> dict:
    """Read the message.  Returns {"found": [(line, episode, confidence)], "guesses": [(line, name as written)], "strong": bool,
    "icons": [...], "long": bool, "body": cleaned text}.  Only confident matches go in "found"; "guesses" are names that
    look like episode titles but aren't in the list."""
    subj, sym = icon_lib.extract_tags(inc.subject or "", cfg)
    body0, bsym = icon_lib.extract_tags(inc.body or "", cfg)
    icons = list(dict.fromkeys(sym + bsym))
    long_ = long_message(body0)
    body = tidy_body(body0)
    lines = drop_signoff([ln.strip() for ln in re.split(r"[\n;]+", body) if ln.strip()], getattr(inc, "sender_name", "") or "")
    strong = bool(MENTION.search(subj)) or any(MENTION.search(ln) and CONTEXT_WORDS.search(ln) for ln in lines)
    context = f"{subj}\n{body}"
    titles = [(norm(e["title"]), e) for e in episodes if len(norm(e["title"])) >= 6 or len(norm(e["title"]).split()) >= 2]
    work: list[str] = []
    if MENTION.search(subj):
        work.append(subj)                                              # "Pink Panther - The Pink Phink"
    work += [ln for ln in lines if strong or MENTION.search(ln)]       # a weak mention only counts for the lines that mention it
    found: list[tuple[str, dict, float]] = []
    guesses: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(original, ep, conf):
        if norm(ep["title"]) not in seen:
            seen.add(norm(ep["title"]))
            found.append((original, ep, conf))

    for line in work:
        hits = scan_titles(line, titles)
        for ep in hits:                                                # the exact title is in the text: confident
            add(line, ep, 1.0)
        if hits:
            continue
        quoted = [q.strip() for q in re.findall(r"[“\"']([^“”\"']{3,70})[”\"']", line) if clean_query(q)]
        q = clean_query(line)
        queries = quoted + ([q] if q and looks_like_a_title(q) else [])
        got = False
        for query in queries:
            ep, _sugg, conf = match(query, episodes, context)
            if ep is not None:
                add(line, ep, conf)
                got = True
        if not got and strong and queries:
            # not in the list: the name as written is the best guess.  In a long email only quoted names or short lines with "pink" in them count.
            name = queries[0]
            if not long_ or quoted or re.search(r"pink", name, re.I):
                guesses.append((line, name))
    return {"found": found, "guesses": guesses, "strong": strong, "icons": icons, "long": long_, "body": body}


# =========================================================================== the plot overview (no spoilers)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
PLOT_SYSTEM = ("You write the plot overview of a classic cartoon for someone deciding whether to watch it. Use ONLY facts stated in the text you are given. "
               "Write 2 to 4 short, plain sentences describing the set-up: who is in it and what the situation is. NEVER reveal how it ends, any twist or the "
               "punchline. No bullet points, no title, no remarks about the text.")


def sentences(text: str) -> list[str]:
    return [x.strip() for x in _SENT.split(re.sub(r"\s+", " ", text or "")) if len(x.strip()) > 2]


def extractive_plot(source: str, max_sentences: int = 3, limit: int = 420) -> str:
    """No AI: the opening sentences of the plot. The set-up comes first and the ending last, so this keeps clear of spoilers."""
    out, total = [], 0
    for sent in sentences(source):
        sent = re.sub(r"\s*\([^)]*\)", "", sent).strip()
        if len(sent) < 25:
            continue
        if out and total + len(sent) > limit:
            break
        out.append(sent)
        total += len(sent)
        if len(out) == max_sentences:
            break
    return " ".join(out)


def ai_plot(title: str, source: str, cfg: dict) -> str | None:
    """The local model's 2-4 sentence overview, or None if it isn't available or made things up (everything it says must come from the text)."""
    from . import llm
    text, info = llm.generate(PLOT_SYSTEM, f"Cartoon: {title}\nText:\n{source[:1500]}\n\nPlot overview (2 to 4 sentences, no spoilers):", cfg["llm"], num_predict=170)
    if not text:
        log.info("Plot overview: AI not used (%s)", info.get("reason"))
        return None
    t = re.sub(r"\*\*|__|`", "", text)
    t = re.sub(r"^\s*(?:plot overview|overview|plot|summary)\s*[:\-]\s*", "", t, flags=re.I)
    t = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", "", t)
    sents = sentences(t)[:4]
    if not sents or sum(len(x) for x in sents) < 50:
        return None
    ok, bad = llm.grounded(sents, f"{title} {source}")
    if not ok:
        log.info("Plot overview: AI answer rejected (%s)", ", ".join(bad[:5]))
        return None
    return " ".join(sents)


def plot_for(ep: dict, details: dict, cfg: dict, path: Path | None, budget: dict) -> str:
    """2-4 sentences, spoiler-free.  The AI writes it when AI mode is on (and it is kept for next time); otherwise, or if it fails, the
    opening of the plot text.  Nothing to go on = nothing printed."""
    src = details.get("plot_source") or ""
    if not src.strip():
        return ""
    pp = cfg["pinkpanther"]
    if pp.get("ai_plot", True) and cfg["summary"].get("backend") == "ollama":
        if details.get("plot_ai"):
            return details["plot_ai"]
        if budget.get("ai", 0) > 0:
            budget["ai"] -= 1
            t = ai_plot(ep["title"], src, cfg)
            if t:
                _remember(norm(ep["title"]), path, plot_ai=t)
                return t
    return extractive_plot(src)


# =========================================================================== the slips
def short_season(a: dict) -> str:
    return f"S{a['season']} #{a['episode']}"


def fact_lines(ep: dict, details: dict) -> list[str]:
    """Only these, in this order, and only the ones known: SEASON, DATE, EPISODE (and where else it was shown)."""
    lines: list[str] = []
    aps = ep.get("appearances", [])
    if aps:
        first = aps[0]
        lines.append(f"SEASON: {first['season']} - {first['show']}")
        lines.append(f"DATE: {details['release']}" if details.get("release") else f"DATE: {first['years']} (TV)")
        lines.append(f"EPISODE: {first['episode']}")
        if len(aps) > 1:
            lines.append("ALSO: " + ", ".join(short_season(a) for a in aps[1:4]) + (" ..." if len(aps) > 4 else ""))
    elif details.get("release"):
        lines.append(f"DATE: {details['release']}")
    return lines


def make_card(ep: dict | None, name: str, plot: str, details: dict, cfg: dict, now: datetime, icons: list[str]) -> Card:
    """A matched episode: real title, season, date, episode, plot.  Not in the list: just the name as you wrote it."""
    pp = cfg["pinkpanther"]
    sections: list[Section] = []
    if ep:
        facts = fact_lines(ep, details)
        if facts:
            sections.append(Section("text", text="\n".join(facts)))
        if plot:
            sections.append(Section("text", label="PLOT", text=plot))
    return Card(kind="pinkpanther", template="pinkpanther", heading=(ep["title"] if ep else name) or "Pink Panther", sections=sections, icons=icons,
                source="email", created=now, stamp="",
                vars={"graphic_style": pp.get("style", "shaded"), "graphic": "on" if pp.get("show_image", True) else "off",
                      "date": now.strftime("%a %d %b"), "matched": bool(ep)})


def build(inc, cfg: dict, now: datetime, path: Path | None = None, fetch=None):
    """(Pink Panther cards, the rest of the message to print normally or None).  No cards if the message isn't about an episode.

    The subject ("Pink Panther") says what it's about; the body, however long, is read for the episodes in it.  An episode is only
    stated as fact when the match is confident; a name that isn't in the list is printed as you wrote it."""
    pp = cfg.get("pinkpanther", {})
    if not pp.get("enabled", True) or not MENTION.search(f"{inc.subject}\n{inc.body}"):
        return [], inc
    res = find_episodes(inc, cfg, load_episodes())
    strong, icons = res["strong"], res["icons"]
    if not res["found"] and not strong:
        return [], inc
    cap = int(pp.get("max_episodes", 8))
    lookup = pp.get("lookup", True)
    budget = {"ai": int(pp.get("max_ai_plots", 3))}
    cards: list[Card] = []
    used: set[str] = set()
    for original, ep, _conf in res["found"][:cap]:
        details = fetch_details(ep, cfg, path, fetch) if (lookup and pp.get("details", True)) else {}
        cards.append(make_card(ep, "", plot_for(ep, details, cfg, path, budget), details, cfg, now, icons))
        used.add(original)
    for original, name in res["guesses"][: max(0, cap - len(cards))]:
        cards.append(make_card(None, name[:80], "", {}, cfg, now, icons))
        used.add(original)
    if not cards:                                                      # about Pink Panther, but no episode could be pinned down
        body = res["body"].strip()
        first = next((s for s in sentences(body) if CONTEXT_WORDS.search(s) or MENTION.search(s)), "") or (sentences(body) or [inc.subject or ""])[0]
        cards.append(make_card(None, (plain_line(first) or "Pink Panther")[:80], "", {}, cfg, now, icons))
        return cards, None
    if strong:
        return cards, None
    rest = "\n".join(ln for ln in (inc.body or "").split("\n") if ln.strip() and not any(u in ln for u in used)).strip()
    return cards, (replace(inc, subject="", body=rest) if rest else None)
