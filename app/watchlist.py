"""Watch list: a message about a movie or TV show to watch prints a generic WATCH LIST slip with the ACTION symbol at the top.

(Pink Panther episodes are not handled here: they have their own slip, see pinkpanther.py, and get first pick of a message.)

HOW A MESSAGE IS READ  (no internet and no AI needed; AI only helps with long, chatty emails)
  * How sure are we that the message is about things to watch?
      LIST   the subject (or a line) says "watch list", "to watch", "watch next" ...: every short bare line is a title.
      TOPIC  the subject mentions movies, films, TV, series, shows, Netflix ...: quoted names, "Title (2021)", "watch Title" and
             short Title-Cased lines are titles.
      none   nothing in the subject: only lines that clearly name something count: a quoted name, "Title (2021)", "Title season 2",
             "the movie Title", "watch Title on Netflix".  "Watch Dave play" or "buy popcorn" stay normal reminders.
  * One title per slip: normally an email names one movie or show; if it names several, each gets its own slip.
  * Long emails are cleaned first (signature, disclaimers, quoted replies, "Thanks, Jack").
  * Each title can carry a type (MOVIE / TV SHOW, from words like "movie", "film", "season 2", "series"), a year ("(2021)") and where to
    watch it (Netflix, Stan, Disney+ ...), taken only from the same line.  Nothing is looked up or invented.
  * With AI mode on (and "watchlist.ai"), a LIST/TOPIC email is also given to the local AI to pick the titles out; a title is only kept if
    it appears in your text.
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime

from . import features, fields
from . import icons as icon_lib
from . import llm
from .models import Card, Section
from .pinkpanther import MENTION, drop_signoff, long_message, plot_from_text, sentences, tidy_body

log = logging.getLogger(__name__)

LIST_CUE = re.compile(r"\b(?:watch\s*-?\s*list|to[\s-]*watch|watch\s+next|must[\s-]*watch|things\s+to\s+watch|up\s+next|to\s+see|binge\s*list)\b", re.I)
TOPIC_CUE = re.compile(r"\b(?:movies?|films?|flicks?|tv|series|shows?|binge|cinema|netflix|stan|disney\+?|prime\s+video|binge|hbo|max|apple\s+tv\+?|paramount\+?|iview|sbs\s+on\s+demand|"
                       r"9now|7plus|hulu|foxtel|kayo|streaming)\b", re.I)
SERVICES = [("Netflix", r"netflix"), ("Stan", r"stan"), ("Disney+", r"disney\+?"), ("Prime Video", r"prime\s+video|amazon\s+prime|prime|amazon"),
            ("Binge", r"binge"), ("Apple TV+", r"apple\s+tv\+?"), ("Paramount+", r"paramount\+?"), ("ABC iview", r"abc\s+iview|iview"),
            ("SBS On Demand", r"sbs(?:\s+on\s+demand)?"), ("9Now", r"9\s*now"), ("7plus", r"7\s*plus"), ("Max", r"hbo(?:\s+max)?|max"),
            ("Hulu", r"hulu"), ("YouTube", r"youtube"), ("Foxtel", r"foxtel"), ("Kayo", r"kayo")]
_SERVICE_RE = [(name, re.compile(rf"(?<![A-Za-z0-9]){pat}(?![A-Za-z0-9])", re.I)) for name, pat in SERVICES]
_TV_WORDS = re.compile(r"\b(?:tv\s+(?:show|series)|tv|series|mini-?series|docuseries|seasons?\s*\d*|s\d{1,2}(?:e\d{1,2})?|episodes?)\b", re.I)
_MOVIE_WORDS = re.compile(r"\b(?:movie|film|flick)\b", re.I)
_VERB = re.compile(r"^(?:(?:please|pls|also|maybe|let'?s|lets|(?:we|i)\s+(?:should|could|must|want\s+to|wanna|need\s+to|have\s+to|gotta|will|would\s+like\s+to)|need\s+to|want\s+to|gotta|have\s+to|finally|add|put\s+on)\s+)*"
                   r"(?:(?:watch|see|catch|check\s+out|binge(?:-watch)?|rewatch|start|finish|try|movie|film|show|series|tv(?:\s+show)?|called|named|titled)(?:ing)?\b[\s:\-–—,]*)*", re.I)
_LOWER_THE = re.compile(r"^(?:the|a|an)\s+(?:new\s+)?(?:(?:movie|film|show|series|tv\s+show)\s+)?(?:called\s+|named\s+)?")
_TAIL = re.compile(r"(?:[\s,:\-–—]*\b(?:tonight|tomorrow|today|this\s+(?:weekend|week|friday|saturday|sunday|arvo|afternoon|evening)|on\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
                   r"with\s+\w+|for\s+(?:date|movie)\s+night|please|pls|asap|soon|sometime|someday|one\s+day)\b)+[\s.!?]*$", re.I)
_NOT_TITLE_END = {"tonight", "tomorrow", "today", "weekend", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march",
                  "april", "may", "june", "july", "august", "september", "october", "november", "december", "i", "we", "you", "he", "she", "they", "it", "my", "our", "and",
                  "but", "so", "then", "with", "if", "when", "after", "before", "on", "at", "this", "that", "please"}
_LOWER_OK = {"of", "the", "a", "an", "and", "in", "on", "to", "for", "at", "vs", "with", "from", "by", "or", "is", "no", "not"}
_CAP = r"[A-Z0-9][\w'’&\-]*"
_ABBR = r"(?:Mr|Mrs|Ms|Dr|St|Jr|Sr)\."
# a title run: capitalised words (and small lowercase joiners) up to a capitalised last word, which may end the sentence ("Airplane!")
_TITLE_RUN = rf"(?:(?:{_CAP}:?|{_ABBR}|{'|'.join(sorted(_LOWER_OK))})\s+)*{_CAP}(?:[:.!?])?"
_YEAR_AT_END = re.compile(r"\s*[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]\s*|\s+((?:19|20)\d{2})\s*$")
_VERB_TITLE = re.compile(r"(?i:\b(?:watch(?:ing|ed)?|see(?:ing)?|seen|saw|catch(?:ing)?|binge(?:-watch(?:ing)?)?|rewatch|start(?:ed)?|finish(?:ed)?|recommend(?:ed)?|try)\s+(?:out\s+)?)"
                         rf"(?:the\s+(?:new\s+)?(?:(?:movie|film|show|series|tv\s+show)\s+)?)?({_TITLE_RUN}(?:\s*,\s*(?:and\s+)?{_TITLE_RUN})*)")
_TITLE_YEAR = re.compile(rf"({_TITLE_RUN})\s*\(\s*((?:19|20)\d{{2}})\s*\)")
_TITLE_SEASON = re.compile(rf"({_TITLE_RUN})\s*[,:\-–—]?\s*(?i:\b(?:season|series)\s*\d+|\bs\d{{1,2}}\b)")
_MOVIE_TITLE = re.compile(rf"(?i:\b(?:the\s+)?(?:movie|film|show|series|tv\s+show)[:\s]+(?:called\s+|named\s+)?)(?:“|\"|')?({_TITLE_RUN})")
_SEASON_LINE = re.compile(r"^(?:watch\s+)?(.{2,50}?)\s*[,:\-–—]?\s*(?:season|series)\s*\d+$", re.I)
_SEASON = re.compile(r"\b(?:season|series)\s*(\d{1,2})\b|\bS(\d{1,2})(?:E\d{1,2})?\b", re.I)
_SEASON_TAIL = re.compile(r"[\s,:\-–—]*\b(?:season|series)\s*\d+\s*$|[\s,:\-–—]*\bS\d{1,2}(?:E\d{1,2})?\s*$", re.I)
_GREETING = re.compile(r"^\s*(?:hi|hello|hey|dear|g'?day|morning|evening)\b", re.I)
_QUOTED = re.compile(r"[“\"]([^“”\"\n]{2,70})[”\"]")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and ")).strip()


def _trim(title: str) -> str:
    """Drop trailing words that can't end a title ("Dune Saturday" -> "Dune") and surrounding punctuation."""
    words = title.strip(" -:–—,.!?\"“”'").split()
    while words and words[-1].lower().strip(".,!?") in _NOT_TITLE_END:
        words.pop()
    while len(words) > 1 and words[0] in _LOWER_OK - {"the", "a", "an"}:                # "and Severance" -> "Severance"
        words.pop(0)
    return " ".join(words).strip(" -:–—,;")


def _context(title: str, line: str, others: list[str]) -> str:
    """When one line names several titles, the words around THIS title only (so one title's year or service isn't given to another)."""
    i = line.find(title)
    if i < 0:
        return line
    head, tail = re.split(r"[,.;!?]|\band\b", line[max(0, i - 14):i])[-1], line[i + len(title):i + len(title) + 36]
    cut = len(tail)
    for o in others:
        if o != title:
            j = tail.find(o)
            cut = min(cut, j) if j >= 0 else cut
            k = head.rfind(o)
            head = head[k + len(o):] if k >= 0 else head
    m = re.search(r"[.!?;]\s", tail + " ")
    if m:
        cut = min(cut, m.start() + 1)
    return head + title + tail[:cut]


def _item(title: str, line: str, others: list[str] | None = None) -> dict | None:
    """A title plus the type, year and service the line mentions (just the words around it when the line holds several titles)."""
    title = _trim(title)
    if not title or len(title) > 70 or not re.search(r"[A-Za-z0-9]", title):
        return None
    if others and len(others) > 1:
        line = _context(title, line, others)
    year = ""
    m = re.search(r"[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]", line) or re.search(re.escape(title) + r"\s+((?:19|20)\d{2})\b", line)
    if m:
        year = m.group(1)
    on = next((name for name, rx in _SERVICE_RE if rx.search(line) and not rx.fullmatch(title)), "")
    kind = "tv" if _TV_WORDS.search(line) and not _MOVIE_WORDS.search(line) else "movie" if _MOVIE_WORDS.search(line) and not _TV_WORDS.search(line) else ""
    sm = _SEASON.search(line)
    return {"title": title, "type": kind, "year": year, "on": on, "season": (sm.group(1) or sm.group(2)) if sm else "", "date": "", "plot": ""}


def _clean_line(line: str) -> str:
    t = re.sub(r"^\s*(?:[-*•>☐□▢]+|\[\s?[xX ]?\s?\]|\d{1,2}[.)])\s+", "", line.strip())
    return re.sub(r"\s+", " ", t).strip()


def _strip_services(t: str) -> str:
    """"watch Dune on Netflix" -> "watch Dune": the service is recorded separately, it isn't part of the title."""
    for _name, rx in _SERVICE_RE:
        t = re.sub(rf"(?:\b(?:on|via|from|at)\s+|[\(\-–]\s*){rx.pattern}\)?", " , ", t, flags=re.I)      # a comma: it also separates "Bear on Disney+ and Dune"
    return re.sub(r"\s*,(?:\s*,)+", ",", re.sub(r"\s+", " ", t)).strip()


def _bare_title(line: str) -> str:
    """A whole short line as a title: strip "watch", the year, the service, "season 2", the day ..."""
    t = _strip_services(re.sub(r"[“”\"]", "", _clean_line(line)))
    t = _YEAR_AT_END.sub(" ", t)
    if len(t.split()) > 2:
        t = _SEASON_TAIL.sub("", t)
        t = _MOVIE_WORDS.sub(" ", t) if re.search(r"\b(?:movie|film)\b\s*$", t, re.I) else t
    stripped = _VERB.sub("", t.strip())
    if stripped != t.strip():
        t = _LOWER_THE.sub("", stripped) or stripped
    t = _TAIL.sub("", t.strip())
    return re.sub(r"\s+", " ", t).strip(" -:–—,.!?()[]")


def _is_title_cased(t: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z0-9][\w'’]*", t) if w.lower() not in _LOWER_OK]
    return bool(words) and all(w[0].isupper() or w[0].isdigit() for w in words)


def strength(inc, cfg: dict) -> str:
    """"list" | "topic" | "" : how clearly the subject (or a header line) says this is about things to watch."""
    subj = inc.subject or ""
    if LIST_CUE.search(subj):
        return "list"
    lines = [ln.strip() for ln in (inc.body or "").split("\n")[:3] if ln.strip()]
    if any(LIST_CUE.search(ln) and len(ln.split()) <= 6 for ln in lines):
        return "list"
    if TOPIC_CUE.search(subj):
        return "topic"
    return ""


def extract_line(line: str, level: str) -> list[dict]:
    """Titles named in one line (see the module doc for what counts at each level)."""
    raw = _clean_line(line)
    if not raw or len(raw) > 400:
        return []
    text = _strip_services(raw)                                              # titles are looked for with "on Netflix" taken out
    found: list[tuple[str, str, bool]] = []                                  # (title, year, is a TV show)

    def add(title: str, year: str = "", tv: bool = False) -> None:
        t = _trim(title)
        k = norm(t)
        if not k or len(t) > 70 or any(k == norm(o[0]) or f" {k} " in f" {norm(o[0])} " or f" {norm(o[0])} " in f" {k} " for o in found):
            return
        found.append((t, year, tv))

    cue = bool(level) or bool(TOPIC_CUE.search(raw) or LIST_CUE.search(raw))
    sure = cue or bool(_MOVIE_WORDS.search(raw) or _TV_WORDS.search(raw) or any(r.search(raw) for _n, r in _SERVICE_RE))
    for q in _QUOTED.findall(text):                                          # "Severance"
        if sure or re.search(r"\b(?:watch|see|seen|saw|catch)\b", raw, re.I):
            add(q.strip())
    for m in _TITLE_YEAR.finditer(text):                                     # Dune (2021)
        add(_LOWER_THE.sub("", _VERB.sub("", m.group(1).strip())) or m.group(1), m.group(2))
    for m in _MOVIE_TITLE.finditer(text):                                    # the movie Dune
        add(m.group(1))
    for m in _VERB_TITLE.finditer(text):                                     # watch The Bear, Severance
        pieces = re.split(r"\s*,\s*(?:and\s+)?", m.group(1))
        for title in pieces:
            if len(title.split()) > 1 or sure or len(pieces) > 1:
                add(title)
    if True:
        for m in _TITLE_SEASON.finditer(text):                               # Severance season 2
            add(_LOWER_THE.sub("", _VERB.sub("", m.group(1).strip())) or m.group(1), tv=True)
    if not found:
        m = _SEASON_LINE.match(text)                                         # severance season 2  (all lower case)
        if m and len(m.group(1).split()) <= 6:
            add(_bare_title(m.group(1)) or m.group(1), tv=True)
    if not found and (level == "list" or (level == "topic" and _is_title_cased(_bare_title(raw)))):
        t = _bare_title(raw)
        parts = [x.strip() for x in t.split(",")]
        pieces = parts if len(parts) > 1 and all(x and _is_title_cased(x) and len(x) > 2 and len(x.split()) <= 6 for x in parts) else [t]     # "Dune, Barbie" but not "I, Robot"
        for piece in pieces:
            if piece and len(piece.split()) <= 8 and not re.search(r"[?]$|[.!]\s+\w", piece) and not re.match(r"^(?:hi|hello|hey|dear|thanks|thank you|cheers|regards)\b", piece, re.I):
                add(piece)
    names = [f[0] for f in found]
    out = []
    for title, year, tv in found:
        it = _item(title, raw, names)
        if it:
            it["year"] = year or it["year"]
            it["type"] = it["type"] or ("tv" if tv else "")
            out.append(it)
    return out


AI_SYSTEM = ("You find the titles of movies and TV shows that a person wants to watch in their note. Reply with one line per title, exactly: "
             "Title | movie or tv | year (leave the year empty if the note does not give it). Only titles that appear in the note, spelled as in the note. "
             "Do not add titles, do not explain. If there are none, reply NONE.")


def ai_titles(text: str, cfg: dict) -> list[dict]:
    """Titles picked out by the local AI.  Each must appear in the text (and a year must too) or it's dropped."""
    if cfg.get("summary", {}).get("backend") != "ollama" or not cfg.get("watchlist", {}).get("ai", True):
        return []
    try:
        reply, info = llm.generate(AI_SYSTEM, text[:3000], cfg["llm"], num_predict=200)
    except Exception as e:                                                   # noqa: BLE001
        log.info("watch list AI unavailable: %s", e)
        return []
    if not reply:
        log.info("watch list AI not used (%s)", info.get("reason"))
        return []
    n_text = norm(text)
    out = []
    for ln in (reply or "").splitlines():
        parts = [p.strip() for p in ln.strip(" -*•").split("|")]
        if not parts or not parts[0] or parts[0].upper() == "NONE":
            continue
        title = parts[0].strip("\"“”")
        if not norm(title) or f" {norm(title)} " not in f" {n_text} ":
            continue
        kind = (parts[1].lower() if len(parts) > 1 else "")
        year = parts[2] if len(parts) > 2 and re.fullmatch(r"(?:19|20)\d{2}", parts[2]) and parts[2] in text else ""
        out.append({"title": title, "type": "tv" if kind.startswith("tv") else "movie" if kind.startswith("mov") or kind.startswith("film") else "", "year": year, "on": "", "season": "", "date": "", "plot": ""})
    return out


def find_titles(inc, cfg: dict) -> dict:
    """{"items": [...], "icons": [...], "level": "list|topic|", "used": [lines], "body": cleaned text}."""
    subj, sym = icon_lib.extract_tags(inc.subject or "", cfg)
    body0, bsym = icon_lib.extract_tags(inc.body or "", cfg)
    icons = list(dict.fromkeys(sym + bsym))
    body = tidy_body(body0)
    probe = replace(inc, subject=subj, body=body)
    level = strength(probe, cfg)
    lines = drop_signoff([ln.strip() for ln in re.split(r"[\n;]+", body) if ln.strip()], getattr(inc, "sender_name", "") or "")
    items: list[dict] = []
    used: list[str] = []
    seen: set[str] = set()

    def add(it: dict) -> None:
        k = norm(it["title"])
        if k and k not in seen:
            seen.add(k)
            items.append(it)

    for ln in lines:
        if LIST_CUE.search(ln) and len(ln.split()) <= 6 and not extract_line(ln, ""):
            used.append(ln)                                                  # a header line like "Watch list:"
            continue
        got = extract_line(ln, level)
        if got:
            used.append(ln)
            for it in got:
                add(it)
    if level and len(items) < 1 + (len(lines) // 4):                          # a chatty email: let the AI help (grounded in the text)
        for it in ai_titles("\n".join(lines), cfg):
            add(it)
    return {"items": items, "icons": icons, "level": level, "used": used, "body": body}


def symbol_id(cfg: dict) -> str:
    """The ACTION symbol: whatever the setting names (your own symbol wins over the built-in clapperboard)."""
    want = (cfg.get("watchlist", {}).get("symbol") or "action")
    table = icon_lib.lookup(cfg)
    return table.get(icon_lib.norm(want)) or ("action" if "action" in icon_lib.BUILTIN else "")


def describe(it: dict) -> str:
    bits = [it["title"] + (f" ({it['year']})" if it["year"] else "") + (f" S{it['season']}" if it.get("season") else "")]
    if it["type"]:
        bits.append("TV show" if it["type"] == "tv" else "movie")
    if it["on"]:
        bits.append(it["on"])
    return " - ".join(bits)


def make_card(it: dict, icons: list[str], cfg: dict, now: datetime) -> Card:
    """One slip per title: the title as the heading, then TYPE / SEASON / DATE (or YEAR) / ON and a short PLOT: only what the message said."""
    sym = symbol_id(cfg)
    ids = list(dict.fromkeys(([sym] if sym else []) + icons))
    when = f"DATE: {it['date']}" if it.get("date") else f"YEAR: {it['year']}" if it["year"] else ""
    facts = [f"TYPE: {'TV SHOW' if it['type'] == 'tv' else 'MOVIE'}" if it["type"] else "", f"SEASON: {it['season']}" if it.get("season") else "", when,
             f"ON: {it['on']}" if it["on"] else ""]
    sections = [Section("text", text="\n".join(f for f in facts if f))] if any(facts) else []
    if it.get("plot"):
        sections.append(Section("text", label="PLOT", text=it["plot"]))
    return Card(kind="watchlist", template="watchlist", heading=it["title"], sections=sections, icons=ids, source="email", created=now, stamp="",
                vars={"date": now.strftime("%a %d %b"), "item": it})


def infer_type(desc: str) -> str:
    """The first sentence of a description often says it: "... is a 2019 TV series ...", "a dark comedy film ..." (blank if it doesn't)."""
    first = (sentences(desc) or [""])[0]
    if re.search(r"\b(?:tv|television)\b|\b(?:mini-?series|sitcom|series|anime|docuseries)\b", first, re.I):
        return "tv"
    if re.search(r"\b(?:film|movie)\b", first, re.I):
        return "movie"
    return ""


def build_records(cfg: dict, now: datetime, recs: list, icons: list[str]):
    """The labelled-lines version: "Movie Name: ... Description: ... Release Date: ...".  Director, cast, themes and the rest are dropped."""
    wl = cfg["watchlist"]
    budget = {"ai": 3}
    cards = []
    seen: set[str] = set()
    for r in recs[: max(1, int(wl.get("max_titles", 5)))]:
        if norm(r.title) in seen:
            continue
        seen.add(norm(r.title))
        it = {"title": r.title, "type": r.type or infer_type(r.desc), "year": r.year, "on": r.on, "season": r.season, "date": r.date,
              "plot": plot_from_text(r.title, r.desc, cfg, bool(wl.get("plot_ai", False)), budget, "movie or TV show", 2, 330) if wl.get("plot", True) else ""}
        cards.append(make_card(it, icons, cfg, now))
    return cards


def build(inc, cfg: dict, now: datetime):
    """([one WATCH LIST slip per title] or [], the rest of the message to print normally or None).  Same contract as pinkpanther.build."""
    wl = cfg.get("watchlist", {})
    if not wl.get("enabled", True) or not features.on(cfg, "watchlist"):
        return [], inc
    subj, sym = icon_lib.extract_tags(inc.subject or "", cfg)
    body0, bsym = icon_lib.extract_tags(inc.body or "", cfg)
    recs = fields.parse(body0, allow_weak=bool(strength(replace(inc, subject=subj, body=body0), cfg)))
    if recs:                                                                 # "Movie Name: ... Description: ..." emails
        return build_records(cfg, now, recs, list(dict.fromkeys(sym + bsym))), None
    if MENTION.search(f"{inc.subject}\n{inc.body}"):
        return [], inc                                                       # Pink Panther messages have their own slip
    res = find_titles(inc, cfg)
    items = res["items"][: max(1, int(wl.get("max_titles", 5)))]
    if not items:
        return [], inc
    cards = [make_card(it, res["icons"], cfg, now) for it in items]
    if res["level"]:
        return cards, None
    used = set(res["used"])
    left = [ln.strip() for ln in (inc.body or "").split("\n") if ln.strip() and not any(u in ln for u in used)]
    left = drop_signoff(left, getattr(inc, "sender_name", "") or "")
    left = [ln for ln in left if not (_GREETING.match(ln) and len(ln.split()) <= 5)]                 # "Hi Jack," is not a reminder
    rest = "\n".join(left).strip()
    return cards, (replace(inc, subject="", body=rest) if rest else None)
