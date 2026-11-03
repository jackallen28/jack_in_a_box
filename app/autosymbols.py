"""Pick 1-3 hand-drawn symbols for a forwarded-email printout - but only when it is clearly right.

"Leave it out if unsure" is the rule, so a symbol needs real EVIDENCE in the email, whatever the AI says:
  * every symbol has keywords (strong ones count 2, weak ones 1; a keyword in the subject counts again);
  * with the AI on, it may propose symbols ("SYMBOLS: school, calendar" or "SYMBOLS: none"), and a proposal is only accepted
    when the email also contains evidence for it.  If the AI says "none", none are attached.  It can never add a symbol that
    has no support in the email;
  * with no AI answer (built-in summary, or the AI didn't follow the format) keywords alone must be convincing:
    score >= 4 from at least two different keywords;
  * at most 3, and a second or third symbol must score at least 60% of the best one;
  * abstract symbols (!, ?, star, idea, email, clock, done) are never chosen automatically;
  * if you put your own symbols on the message, none are added.
"""
from __future__ import annotations

import re

from . import icons as icon_lib

NEVER_AUTO = {"exclamation", "question", "star", "idea", "email", "clock", "tick"}
SOLO_SCORE, SOLO_TERMS = 4, 2          # keywords alone (no AI answer)
AI_SCORE = 2                           # the AI proposed it: the email must still back it up
MAX_SYMBOLS, RELATIVE = 3, 0.6

# id -> (strong, weak).  Plain words and phrases, matched as whole words in any case ("blood test" also matches "blood-test";
# a trailing * is any ending: celebrat* = celebrate/celebration; most words also match their plural).  "re:..." is a raw regex.
# Strong = on its own a good sign (2 points); weak = only a hint (1 point).  Phrases go in strong because they are specific.
KEYWORDS: dict[str, tuple[list[str], list[str]]] = {
    "work": (["meeting", "team meeting", "client meeting", "staff meeting", "board meeting", "stand-up", "performance review", "annual leave", "sick leave",
              "leave request", "purchase order", "job offer", "job application", "interview", "cover letter", "resume", "colleague", "client", "customer",
              "boss", "manager", "supervisor", "project", "office", "workplace", "employer", "employee", "shift", "roster", "rostered", "salary", "promotion",
              "onboarding", "human resources", "tender", "proposal", "deliverable", "stakeholder", "work from home", "workshop", "site visit", "work order",
              "business meeting", "quarterly review", "statement of work", "conference"],
             ["report", "presentation", "supplier", "contract", "staff", "team", "order", "quote", "candidate", "application", "review", "next steps", "follow up", "hr"]),
    "school": (["school", "primary school", "high school", "kindergarten", "kinder", "preschool", "teacher", "principal", "excursion", "school camp", "homework",
                "assignment", "classroom", "re:year \\d+", "student", "parent teacher", "parent-teacher interview", "report card", "school fees", "school uniform",
                "canteen", "term dates", "pupil free day", "sports carnival", "swimming carnival", "school assembly", "university", "uni", "lecture", "tutorial",
                "exam", "semester", "enrolment", "enrol*", "tuition", "p&c", "curriculum", "permission slip", "class teacher", "school bus"],
               ["class", "term", "parents", "grade", "learning", "study", "camp", "reading", "tutor"]),
    "fun": (["party", "celebrat*", "concert", "festival", "picnic", "game night", "movie night", "pizza night", "barbecue", "bbq", "fundraiser", "sleepover",
             "playdate", "day out", "family day", "gig", "tickets", "theatre", "sports day", "christmas party", "end of year party", "social club", "trivia",
             "karaoke", "camping", "beach day", "show", "garden show", "street party", "block party", "come along", "join us"],
            ["invited", "invitation", "rsvp", "dinner", "drinks", "fun", "event", "community", "get together", "catch up", "lunch", "brunch", "games"]),
    "holiday": (["flight", "holiday", "vacation", "hotel", "airline", "airport", "boarding pass", "itinerary", "resort", "cruise", "passport", "visa application",
                 "check-in opens", "travel insurance", "car hire", "hire car", "rental car", "booking confirmation", "your trip", "departure", "airbnb",
                 "accommodation", "getaway", "long weekend", "school holidays", "public holiday", "overseas", "tour", "baggage", "e-ticket", "frequent flyer"],
                ["travel", "trip", "booking", "luggage", "arrive", "depart*", "destination", "flying", "check-in", "check in"]),
    "calendar": (["appointment", "reschedul*", "save the date", "agenda", "calendar", "retimed", "meeting invite", "calendar invite", "moved to", "date and time",
                  "your appointment", "see you on", "important dates", "term dates", "timetable", "schedule change", "event details", "time change", "new time",
                  "rsvp by", "confirm your attendance", "venue and time", "session time"],
                 ["booking", "session", "deadline", "closes", "opens", "reminder", "due date", "date", "attend*"]),
    "3dprinter": (["3d print*", "3d printer", "filament", "gcode", "g-code", "stl", "stl file", "print farm", "nozzle", "hotend", "build plate", "print bed",
                   "prusa", "bambu", "creality", "ender 3", "pla", "petg", "slicer", "cura", "resin printer", "additive manufacturing", "printed part",
                   "print time", "layer height", "infill", "bed levelling", "bed leveling", "print job"],
                  ["print", "resin", "spool", "maker", "prototype", "cad", "printer"]),
    "accountant": (["invoice", "tax invoice", "overdue invoice", "accountant", "tax", "tax return", "gst", "bas", "bas statement", "ato", "bookkeeper", "bookkeeping",
                    "payroll", "receipt", "audit*", "ledger", "remittance", "remittance advice", "accounts payable", "accounts receivable", "profit and loss",
                    "p&l", "balance sheet", "financial year", "end of financial year", "eofy", "superannuation", "depreciation", "xero", "myob", "quickbooks",
                    "payment advice", "credit note", "statement of account", "lodgement", "lodge your", "chart of accounts"],
                   ["payment", "overdue", "balance", "statement", "bill", "premium", "refund", "expenses", "account", "fees"]),
    "bookwork": (["bookwork", "reconcil*", "timesheet", "bank reconciliation", "data entry", "bookkeeping", "expense report", "expense claim", "mileage log",
                  "logbook", "paperwork", "admin work", "spreadsheet", "monthly figures", "end of month", "update the books", "filing"],
                 ["forms", "form", "admin", "records", "files", "documents", "enter the", "figures"]),
    "home": (["lease", "landlord", "tenant", "rent", "rental", "mortgage", "real estate", "real estate agent", "property manager", "rental inspection", "plumber",
              "plumbing", "electrician", "renovat*", "maintenance", "repair", "council rates", "body corporate", "strata", "builder", "handyman", "gutter",
              "leak", "blocked drain", "hot water", "air con*", "pest control", "removalist", "moving house", "back door", "front door", "fence", "tradesperson",
              "landscap*", "quote for your deck"],
             ["house", "apartment", "property", "garden", "deck", "door", "kitchen", "roof", "gas", "electricity", "lawn", "bathroom", "unit", "street"]),
    "shopping": (["order", "order confirmation", "deliver*", "parcel", "shipped", "shipping", "tracking", "tracking number", "cart", "checkout", "shopping",
                  "groceries", "supermarket", "click and collect", "pick up your order", "dispatch*", "courier", "australia post", "auspost", "return policy",
                  "gift card", "voucher", "sold out", "out for delivery", "your purchase"],
                 ["sale", "discount", "purchase", "store", "stock", "special offer", "free shipping", "items"]),
    "money": (["refund", "deposit", "loan", "mortgage", "premium", "price", "amount due", "budget", "payment due", "pay by", "outstanding balance", "direct debit",
               "bank transfer", "bank details", "account number", "bsb", "credit card", "interest rate", "invest*", "savings", "centrelink", "late fee",
               "pay now", "total due", "overdue", "price rise", "price increase", "per week", "per month", "per year"],
              ["re:\\$\\d[\\d,.]*", "cost", "fee", "payment", "pay", "bank", "credit", "quote", "insurance", "rent", "annual", "charge"]),
    "phone": (["phone call", "give me a call", "give us a call", "call me back", "call us", "call you", "ring me", "ring you", "ring back", "voicemail", "missed call",
               "phone number", "call back", "ring", "phone", "text message", "sms", "whatsapp", "on the phone"],
              ["call", "mobile", "text me", "contact number", "dial"]),
    "birthday": (["birthday", "bday", "cake", "re:turning \\d+", "re:\\d+(?:st|nd|rd|th) birthday", "happy birthday", "birthday party", "candles", "birthday present"],
                 ["present", "gift", "balloons", "surprise"]),
    "heart": (["anniversary", "wedding", "valentine*", "engagement", "condolence", "sympathy", "get well", "get well soon", "thinking of you", "with love",
               "in loving memory", "funeral", "baby shower", "new baby", "congratulations", "love you", "wedding invitation"],
              ["love", "best wishes", "thoughts", "celebration of life"]),
    "health": (["doctor", "dentist", "dental", "medical", "clinic", "hospital", "prescription", "pharmacy", "chemist", "physio*", "specialist", "vaccin*", "blood test",
                "optometrist", "optician", "gp", "test results", "x-ray", "ultrasound", "check-up", "checkup", "immunis*", "psycholog*", "counsell*", "chiropract*",
                "medicare", "health fund", "medication", "referral", "surgery", "emergency department", "physiotherapy"],
               ["health", "patient", "appointment", "wellbeing", "symptoms", "fever", "scan"]),
    "car": (["car", "vehicle", "rego", "registration", "mechanic", "roadworthy", "tyre", "driver's licence", "driver's license", "fuel", "parking fine", "car service",
             "logbook service", "car insurance", "traffic fine", "speeding fine", "toll", "e-tag", "number plate", "windscreen", "car wash", "ute", "motorbike",
             "roadside assistance", "wheel alignment", "oil change"],
            ["drive", "driving", "garage", "towing", "servicing", "insurance", "licence", "kilometres"]),
    "pet": (["dog", "cat", "puppy", "kitten", "vet", "veterinar*", "groomer", "grooming", "pet", "dog walker", "kennel", "cattery", "flea", "worming", "microchip*",
             "pet food", "heartworm", "dog park", "pet insurance"],
            ["walk", "vaccination", "animal", "paws", "treats"]),
}


def _compile(term: str) -> str:
    """A readable phrase -> a regex: words separated by space or hyphen, trailing * = any ending, optional plural."""
    if term.startswith("re:"):
        return term[3:]
    words = []
    for w in term.lower().split():
        star = w.endswith("*")
        w = re.escape(w.rstrip("*")).replace("\\-", "[-\\s]?").replace("'", "'?")
        words.append(w + (r"\w*" if star else (r"(?:s|es)?" if len(w) > 3 and not w.endswith("s") else "")))
    return r"[\s\-]+".join(words)


def _hits(terms: list[str], text: str) -> set[str]:
    return {t for t in terms if re.search(rf"(?<![\w'])(?:{_compile(t)})(?![\w])", text, re.I)}


def describe(cfg: dict, per_symbol: int = 7) -> list[str]:
    """What each symbol covers, in plain words, for the AI's instructions: 'school (school, teacher, excursion, ...)'."""
    out = []
    for c in icon_lib.catalogue(cfg):
        if not c["enabled"] or c["id"] in NEVER_AUTO:
            continue
        strong = KEYWORDS.get(c["id"], ([], []))[0] if c["builtin"] else [t for t in c["tags"] if t]
        plain = [t for t in strong if not t.startswith("re:")]
        plain = [t.rstrip("*") for t in plain if " " in t][: per_symbol // 2] + [t.rstrip("*") for t in plain if " " not in t]
        out.append(f"{c['name'].lower()} ({', '.join(dict.fromkeys(plain[:per_symbol] + list(c.get('extra', []))))})")
    return out


def scores(text: str, subject: str, cfg: dict) -> dict[str, tuple[int, int]]:
    """symbol id -> (score, number of different keywords found), for the symbols that may be chosen automatically."""
    out: dict[str, tuple[int, int]] = {}
    enabled = {c["id"]: c for c in icon_lib.catalogue(cfg) if c["enabled"]}
    for iid, item in enabled.items():
        if iid in NEVER_AUTO:
            continue
        if iid in KEYWORDS:
            strong, weak = [*KEYWORDS[iid][0], *item.get("extra", [])], KEYWORDS[iid][1]      # + any aliases you added to this symbol
        else:                                                    # your own symbol: its name and tags are its keywords
            strong, weak = [t for t in item["tags"] if t and len(t) > 2], []
        score, terms = 0, 0
        for pats, w in ((strong, 2), (weak, 1)):
            body, subj = _hits(pats, text), _hits(pats, subject)
            score += w * len(body | subj) + w * len(subj)
            terms += len(body | subj)
        if score:
            out[iid] = (score, terms)
    return out


def names_for_prompt(cfg: dict) -> list[str]:
    """The symbol names the AI may propose (only ones that can be chosen automatically)."""
    return [c["name"].lower() for c in icon_lib.catalogue(cfg) if c["enabled"] and c["id"] not in NEVER_AUTO]


def resolve(names: list[str], cfg: dict) -> list[str]:
    """What the AI wrote ("School", "3d printer", "calendar") -> symbol ids; anything unknown is ignored."""
    table = {}
    for c in icon_lib.catalogue(cfg):
        if c["enabled"] and c["id"] not in NEVER_AUTO:
            table[icon_lib.norm(c["name"])] = c["id"]
            table[icon_lib.norm(c["id"])] = c["id"]
    out = []
    for n in names:
        iid = table.get(icon_lib.norm(n))
        if iid and iid not in out:
            out.append(iid)
    return out


def choose(text: str, subject: str, cfg: dict, ai: list[str] | None = None) -> list[str]:
    """``ai`` = symbol ids the AI proposed ([] = it said "none", None = it gave no answer).  Returns 0-3 symbol ids."""
    if ai is not None and not ai:
        return []                                                # the AI wasn't sure -> neither are we
    sc = scores(text, subject, cfg)
    if ai is not None:
        ok = {i: sc[i][0] for i in ai if i in sc and sc[i][0] >= AI_SCORE}
    else:
        ok = {i: s for i, (s, t) in sc.items() if s >= SOLO_SCORE and t >= SOLO_TERMS}
    if not ok:
        return []
    ranked = sorted(ok, key=lambda i: -ok[i])
    best = ok[ranked[0]]
    return [i for i in ranked if ok[i] >= RELATIVE * best][:MAX_SYMBOLS]


def pick(text: str, subject: str, cfg: dict, info: dict | None) -> tuple[list[str], list[str] | None]:
    """The symbols for an email, given the AI's run info.  Returns (chosen ids, the AI's own proposals as ids or None)."""
    info = info or {}
    ai = resolve(info["symbols_raw"], cfg) if info.get("used_ai") and info.get("symbols_raw") is not None else None
    return choose(text, subject, cfg, ai), ai
