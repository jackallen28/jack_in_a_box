"""Features: what this Jack in a Box is set up to do.

On first use a short guide asks which features you want.  Only the chosen ones get a tab in the top menu and only they run
(an unchosen "Google Form" isn't polled, an unchosen AirPrint isn't advertised ...).  More can be added, or taken away, any
time on the Settings tab.  Turning one off only hides it: nothing you set up is deleted, so turning it back on finds it all as it was.

``on(cfg, id)`` is the one question the rest of the app asks.  Until the guide has been finished (``setup_complete``) everything
counts as on, and installs from before this existed were moved to all-on, so nothing that already worked stops working.
"""
from __future__ import annotations

# id, title, what it does (one plain sentence), tabs it adds to the top menu, recommended in the guide, a step in the guide (None = a tab to finish later)
FEATURES = [
    {"id": "email", "title": "Print from email", "blurb": "Email a reminder to the printer's own address and it prints.", "tabs": ["email", "text", "rules"], "recommended": True, "step": "email"},
    {"id": "calendar", "title": "Calendar events", "blurb": "Prints your Google Calendar events the morning of (or the day before).", "tabs": ["calendar"], "recommended": True, "step": "calendar"},
    {"id": "symbols", "title": "Symbols", "blurb": "Little pictures on your slips, picked with tags like [school] or [!].", "tabs": ["symbols"], "recommended": True, "step": None},
    {"id": "messages", "title": "Messages from friends + the button", "blurb": "Friends can email you a message that waits for you to press a button.", "tabs": ["messages"], "recommended": False, "step": None},
    {"id": "birthdays", "title": "Birthdays", "blurb": "A slip on the day (7 am) for each birthday you add; birthday reminders get their own look.", "tabs": ["birthdays"], "recommended": False, "step": None},
    {"id": "form", "title": "Google Form", "blurb": "Fill in a Google Form on your phone and it prints, with an optional schedule-send.", "tabs": ["form", "text", "rules"], "recommended": False, "step": None},
    {"id": "shortcuts", "title": "iPhone Shortcuts", "blurb": "Send a phrase or a screenshot to the printer from an iPhone Shortcut.", "tabs": ["shortcuts", "text"], "recommended": False, "step": None},
    {"id": "airprint", "title": "AirPrint", "blurb": "Print from a phone or computer on your network as if it were a normal printer.", "tabs": ["airprint"], "recommended": False, "step": None},
    {"id": "pinkpanther", "title": "Pink Panther episodes", "blurb": "Note an episode to watch and get a PINK PANTHER slip with its season, episode and plot.", "tabs": ["pinkpanther"], "recommended": False, "step": None},
    {"id": "watchlist", "title": "Watch list (movies and TV)", "blurb": "Note a movie or show and get a WATCH LIST slip.", "tabs": ["watchlist"], "recommended": False, "step": None},
    {"id": "ai", "title": "AI summaries", "blurb": "A small AI on the Pi summarises long forwarded emails (slow on a Pi 3B+; optional).", "tabs": ["text", "aireview"], "recommended": False, "step": None},
    {"id": "power", "title": "Power and fan", "blurb": "Fan control and idle power-off, for a Pi in an enclosure.", "tabs": ["power"], "recommended": False, "step": None},
    {"id": "integrations", "title": "Integrations", "blurb": "Webhooks and plugins, for tinkerers.", "tabs": ["integrations"], "recommended": False, "step": None},
]
FEATURE_IDS = [f["id"] for f in FEATURES]
CORE_TABS = ["dashboard", "print", "templates", "printer", "playground", "backup", "settings", "account"]       # always there


def on(cfg: dict, fid: str) -> bool:
    """Is this feature in use?  (Everything is on until the setup guide is finished.)"""
    return not cfg.get("setup_complete") or bool((cfg.get("features") or {}).get(fid))


def clean(selection) -> dict:
    """A {feature id: bool} for every feature, from whatever the browser sent."""
    sel = selection if isinstance(selection, dict) else {}
    return {fid: bool(sel.get(fid, False)) for fid in FEATURE_IDS}


def visible_tabs(cfg: dict) -> list[str]:
    tabs = list(CORE_TABS)
    for f in FEATURES:
        if on(cfg, f["id"]):
            tabs += [t for t in f["tabs"] if t not in tabs]
    return tabs


def public() -> list[dict]:
    return [dict(f) for f in FEATURES]
