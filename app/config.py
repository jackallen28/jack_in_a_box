"""Configuration store.

Everything the user can change from the web page lives in one JSON file
(``data/config.json``).  Defaults live here; anything missing from the saved
file is filled in from the defaults, so upgrades never break an old config.
"""
from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path

from .features import FEATURE_IDS

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
DATA_DIR = Path(os.environ.get("JACK_IN_A_BOX_DATA", ROOT / "data"))

BUILTIN_TEMPLATES = ("reminder", "calendar", "email", "message", "pinkpanther", "watchlist", "birthday")

KIND_DEFAULTS = {
    "reminder": {
        "label": "Reminder",
        "title": "REMINDER",
        "subtitle": "",
        "order": ["title", "icons", "heading", "body", "images", "footer"],
    },
    "calendar": {
        "label": "Calendar event",
        "title": "EVENT",
        "subtitle": "{day}",
        "order": ["title", "icons", "graphic", "heading", "body", "footer"],
    },
    "message": {
        "label": "Message from a friend",
        "title": "MESSAGE",
        "subtitle": "from {name}",
        "order": ["title", "icons", "heading", "body", "images", "footer"],
    },
    "pinkpanther": {
        "label": "Pink Panther episode",
        "title": "PINK PANTHER",
        "subtitle": "",
        "order": ["title", "graphic", "heading", "body", "footer"],
    },
    "birthday": {
        "label": "Birthday",
        "title": "BIRTHDAY",
        "subtitle": "{date}",
        "order": ["title", "icons", "heading", "body", "footer"],
    },
    "watchlist": {
        "label": "Watch list (movies and TV shows)",
        "title": "WATCH LIST",
        "subtitle": "",
        "order": ["title", "icons", "heading", "body", "footer"],
    },
    "email": {
        "label": "Email summary",
        "title": "EMAIL",
        "subtitle": "",
        "order": ["title", "icons", "heading", "body", "images", "footer"],
    },
}


def default_template(kind: str = "reminder") -> dict:
    k = KIND_DEFAULTS.get(kind, KIND_DEFAULTS["reminder"])
    return {
        "kind": kind,
        "label": k["label"],
        # Paper is 80 mm; the TM-T82II can only print 72 mm (576 dots) of it.
        "width_mm": 72,
        "min_height_mm": 30,       # slips are as short as their content, but never shorter than this (it used to be 80)
        "max_height_mm": 700,
        "margin_top_mm": 0,        # blank paper ABOVE the printout (outside the border)
        "margin_bottom_mm": 0,     # blank paper BELOW it
        "padding_mm": 4,
        "gap_mm": 3,
        "border": {"enabled": True, "width_pt": 1.0, "radius_mm": 0},
        "order": list(k["order"]),
        "title": {
            "enabled": True,
            "text": k["title"],
            "subtitle": k["subtitle"],
            "font": "bungee",
            "size_pt": 30,
            "style": "outline",
            "align": "center",
        },
        "icons": {
            "enabled": True,
            "size_mm": 32 if kind == "birthday" else 20,
            "max": 6,
            "gap_mm": 3,
            "align": "center",
        },
        "graphic": {
            "enabled": kind in ("calendar", "pinkpanther"),
            "file": "builtin:pink_panther" if kind == "pinkpanther" else "",
            "max_height_mm": 30 if kind == "pinkpanther" else 25,
            "align": "center",
            "dither": "floyd",
        },
        "heading": {
            "enabled": True,
            "font": "courier-prime",
            "bold": True,
            "size_pt": 22 if kind == "birthday" else 15,
            "align": "center" if kind == "birthday" else "left",
            "show_subline": True,
        },
        "body": {
            "font": "courier-prime",
            "size_pt": 10 if kind == "pinkpanther" else 11,
            "align": "center" if kind == "birthday" else "left",
            "box": "single",
            "box_width_pt": 1.0,
            "radius_mm": 2,
            "show_labels": True,
            "hide_sections": [],
            "checkbox_actions": True,
            "priority_marker": True,
            "labels": {
                "text": "",
                "deadline": "DUE",
                "when": "WHEN",
                "where": "WHERE",
                "notes": "NOTES",
                "summary": "SUMMARY",
                "actions": "ACTIONS",
                "focus": "",
                "full": "FULL EMAIL",
            },
        },
        "images": {
            "enabled": kind != "calendar",
            "max_height_mm": 100,
            "dither": "floyd",
            "boxed": False,
            "contrast": 1.15,
        },
        "footer": {
            "enabled": False,
            "text": "Jack in a Box",
            "show_timestamp": False,
            "size_pt": 8,
            "align": "center",
        },
    }


DEFAULTS: dict = {
    "setup_complete": False,
    # What this box is set up to do (app/features.py).  The first-run guide fills it in; Settings changes it later.
    "features": {},
    "web": {"port": 8080, "password_hash": "", "secret_key": ""},
    "general": {
        "timezone": "",  # blank = the Pi's own timezone (set with raspi-config)
        "date_order": "dmy",  # 14/11 = 14 November. Use "mdy" for US style.
        "time_format": "12h",
        "history_keep_days": 60,
    },
    "email": {
        "enabled": False,
        "host": "imap.gmail.com",
        "port": 993,
        "username": "",
        "password": "",
        "folder": "INBOX",
        "poll_seconds": 5,          # how often the inbox is checked over one kept-open connection (a check is one quick round trip)
        "after": "delete",     # delete | mark_read | move   (delete = Gmail Trash, kept 30 days)
        "trash_folder": "[Gmail]/Trash",   # blank on non-Gmail providers = delete for good
        "move_folder": "Printed",
        "allowed_senders": [],       # YOU (admins): reminders print immediately
        "require_auth_pass": True,
        "max_cards_per_email": 10,
        "max_images": 3,
        "max_all_images": 8,        # "[images attached]": how many pictures at most (every picture in the email)
        "min_image_px": 150,
        "subject_only_as_task": True,
    },
    "printer": {
        "backend": "auto",  # auto | file | usb | network | dummy
        "device": "/dev/usb/lp0",
        "usb_vendor": "0x04b8",
        "usb_product": "",
        "host": "",
        "port": 9100,
        "width_dots": 576,
        "threshold": 150,
        "cut": "partial",  # partial | full | none | line
        "feed_lines": 3,
        "max_prints_per_hour": 40,
        "retry_seconds": 30,
        "max_attempts": 20,
    },
    "text": {
        "split": True,
        "sort": "smart",  # smart | deadline | original
        "fix_spelling": True,
        "fix_grammar": True,
        "trailing_period": False,
        "custom_words": [
            "wifi", "iphone", "ipad", "macbook", "whatsapp", "youtube", "zoom",
            "wfh", "arvo", "bbq", "servo", "ute", "okay", "ok", "app", "apps",
            "online", "login", "email", "emails", "gmail", "xero", "myob",
            "asap", "eod", "pdf", "invoice", "invoices",
        ],
        "priority_words": ["urgent", "asap", "important", "critical", "priority"],
        "deadline_words": True,
    },
    "summary": {
        "backend": "extractive",  # extractive | ollama
        "sentences": 4,
        "max_actions": 10,
        "max_full_chars": 2500,
        "show_original_sender": True,
        "auto_symbols": True,     # add 1-3 symbols to a forwarded-email printout, only when clearly relevant (see app/autosymbols.py)
    },
    "llm": {                        # used when summary.backend = "ollama" (see README: Local AI)
        "url": "http://127.0.0.1:11434",
        "model": "qwen2.5:0.5b",
        "timeout": 240,             # seconds; a Pi 3B+ can take 1-3 minutes
        "check_seconds": 20,        # the AI's "does this message make sense?" check may take this long; after that the slip prints without it
        "keep_alive": 0,            # unload the model right after each summary to free the Pi's RAM
        "num_ctx": 2048,
        "num_predict": 260,
        "max_input_chars": 3000,    # how much of the email the AI reads (the whole thing if it fits)
        "examples": 1,              # worked examples shown before your email (0 = none; see app/summary_examples.json)
        "keep_history": True,       # keep each forwarded email's summary for review (data/ only, never uploaded)
        "history_days": 90,
        "use_my_examples": True,    # your reviewed summaries (AI review tab) are shown to the model as worked examples
        "rules": [],                # your standing rules, e.g. "Always name who the email is from" (added to the AI's instructions)
    },
    "calendar": {
        "enabled": True,
        "sync_minutes": 15,
        "print_time": "07:00",
        "late_policy": "print_now",  # print_now | day_of_only | skip
        "also_day_of": False,
        "lookahead_days": 14,
        "skip_all_day": False,
        "notes_chars": 300,
    },
    "updates": {"enabled": True},
    # Google Form -> Google Sheet -> printouts (see app/sheet.py and app/apps_script.js)
    "sheet": {
        "enabled": False,
        "url": "",                  # the Apps Script "Web app URL"
        "token": "",                # secret code shared with that script
        "poll_seconds": 60,
        "timezone": "",             # timezone of the "Schedule Send" answers; blank = the one Google reports for the sheet
        "treat_as": "admin",        # admin: every form row is YOU - prints straight away | by_sender: use the verified email
        "unknown_policy": "hold",   # (by_sender only) someone not on your lists: hold (wait for the button) | print | ignore
    },
    # Messages about the Pink Panther: look the episode up on Wikipedia and print its real name, date, director ... (app/pinkpanther.py)
    "pinkpanther": {
        "enabled": True,
        "lookup": True,             # search the internet for the plot and an exact release date; off = only the season/episode from the built-in list
        "details": True,            # read the episode's own page (Pink Panther Wiki, then Wikipedia) for its plot text and release date
        "ai_plot": False,           # with AI mode on, the local AI writes the 2-4 sentence spoiler-free plot overview (else the opening of the plot text). OFF: on a Pi each one takes minutes
        "max_ai_plots": 3,          # at most this many AI overviews per message (each takes a minute or two on a Pi); the rest use the opening of the plot
        "show_image": True,         # the hand-drawn Pink Panther picture at the top
        "style": "shaded",          # shaded (outline + light stipple fill) | outline (clean line art)
        "max_episodes": 8,          # at most this many episodes from one message
    },
    # Birthdays (app/birthdays.py): your list prints at print_time on the day; reminders that talk about a birthday get the BIRTHDAY template
    "birthdays": {
        "enabled": True,
        "print_time": "07:00",      # HH:MM on the day
        "catch_up": True,           # the Pi was off at that time: print as soon as it is back the same day
        "recognise": True,          # a reminder / calendar event about a birthday is printed with the BIRTHDAY template
        "learn": True,              # a birthday reminder ("wish Tom a happy birthday [on 10 November]") is also added to the list, so it comes round every year
        "ai_check": True,           # with AI mode on, the local AI reads the reminder first and says whether it really is about a person's birthday (time-limited: llm.check_seconds)
        "symbol": "birthday",       # the cake; a name or tag from the Symbols page
        "people": [],               # [{id, name, day, month, year (0 = unknown), note, enabled}] - edited on the Birthdays tab
    },
    # Keeping the Pi healthy: a scheduled reboot done by a systemd timer (deploy/auto-reboot.sh), so it works even if the app has hung
    "maintenance": {
        "reboot_enabled": True,
        "reboot_every_days": 2,     # 2 = every 48 hours
        "reboot_hour": 3,           # at 3 am (the Pi's own clock/timezone)
    },
    # Backing up your settings (app/backup.py)
    "backup": {
        "local": True,              # keep the last few copies of your settings on the Pi (guards against a bad edit, not a dead SD card)
        "keep": 7,
        "email_enabled": False,     # email a copy to yourself (the SD card can die; your inbox won't)
        "email_to": "",             # blank = your first admin address
        "email_every_days": 7,      # only when something changed since the last one
        "email_secrets": True,      # include your passwords in the emailed copy (needed to restore the email connection)
        "last_email": "",
        "last_email_hash": "",
    },
    # Messages about a movie or TV show to watch: a WATCH LIST slip with the ACTION symbol (app/watchlist.py)
    "watchlist": {
        "enabled": True,
        "symbol": "action",         # the symbol at the top: a name or tag from the Symbols page (your own "action" symbol wins over the built-in clapperboard)
        "ai": False,                # with AI mode on, the local AI also picks the titles out of a long, chatty email (each title must appear in the text). OFF: on a Pi it takes minutes
        "plot": True,               # a short PLOT line from the email's own "Description:" (2-3 sentences; director, cast, themes are never printed)
        "plot_ai": False,           # with AI mode on, the local AI condenses a long description into 2-4 spoiler-free sentences (else the opening sentences). OFF: on a Pi it takes minutes
        "max_titles": 1,            # at most this many titles (a slip each) from one message: an email names one movie or show
        "ai_check": True,           # with AI mode on, the local AI reads the message and says whether the title found is really what it is about (time-limited: llm.check_seconds)
    },
    # Share the receipt printer on your network as an AirPrint / IPP Everywhere printer (see app/airprint.py)
    "airprint": {
        "enabled": False,
        "name": "Jack in a Box",
        "port": 631,                # if 631 isn't available (not root, or CUPS is there) 6310 is used; clients find it either way
        "allow_any_network": False, # False: only devices on your own network (private addresses) can print
        "hold_for_button": False,   # True: AirPrint pages wait for the message button, like friends' messages
        "raw_enabled": False,       # True: another computer can use the printer's OWN (Epson) driver; its data is passed through untouched
        "raw_port": 9100,           # ...also accepted on this raw TCP port, like a network receipt printer
        "max_pages": 10,            # pages printed from one document
        "max_copies": 5,
        "max_job_mb": 30,
        "max_queued": 20,           # busy (refuse more) when this many AirPrint pages are waiting
        "autocrop": "margins",      # none | vertical | margins: trim blank paper
        "dither": "auto",           # auto | text | photo
        "max_height_mm": 700,
    },
    "fan": {
        "enabled": False,
        "pin": 22,                  # BCM. Drive a transistor/MOSFET that switches the fan - never the fan itself.
        "mode": "auto",             # auto | always | off
        "on_temp": 60,              # C
        "off_temp": 50,
        "during_ai": True,          # run the fan for the whole of every AI summary
        "min_on_seconds": 60,
        "active_high": True,
    },
    "power": {
        "idle_shutdown_hours": 0,   # 0 = never (default). Otherwise power off after this long with nothing happening.
        "min_uptime_minutes": 30,
        "keep_awake_before_calendar_hours": 12,
    },
    # Symbols: [school], [!] ... see app/icons.py.  Built-ins are always available; this holds your changes.
    "icons": {
        "disabled": [],      # built-in ids you've switched off
        "aliases": {},       # {"school": ["sch", "homework"]}  extra tag names for built-ins
        "custom": [],        # [{"id","name","tags":[...],"file":"<upload>.png","mode":"line"|"photo"}]
    },
    "calendars": [],
    # People allowed to send you messages.  Their emails become MESSAGE printouts that wait
    # for the button (unless "bypass_queue" is on for that person).
    "friends": [],      # [{"name": "Sarah", "email": "sarah@x.com", "bypass_queue": False, "enabled": True}]
    "messages": {
        "hold_for_button": True,
        "template": "message",
        "tidy_text": False,         # fix spelling/grammar in friends' messages (off = print as written)
        "max_held": 50,
    },
    "button": {
        "enabled": False,
        "button_pin": 17,           # BCM numbering. Button wired between this pin and GND.
        "led_pin": 27,              # LED inside the button (through a resistor, see README)
        "led_active_high": True,
        "blink_seconds": 0.5,
    },
    "rules": [],
    "integrations": {
        "api_enabled": False,
        "api_token": "",
        "webhook_url": "",
        "plugins_enabled": True,
    },
    "templates": {k: default_template(k) for k in BUILTIN_TEMPLATES},
}


def merge(defaults, saved):
    """Fill gaps in ``saved`` from ``defaults`` (dicts recurse, others replace)."""
    if isinstance(defaults, dict) and isinstance(saved, dict):
        out = {}
        for k, v in defaults.items():
            out[k] = merge(v, saved[k]) if k in saved else copy.deepcopy(v)
        for k, v in saved.items():  # keep unknown keys (e.g. custom templates)
            if k not in defaults:
                out[k] = copy.deepcopy(v)
        return out
    return copy.deepcopy(saved)


def normalise(cfg: dict) -> dict:
    legacy = "features" not in cfg                 # a config from before the setup guide (or a raw config.json rescued from an old card)
    cfg = merge(DEFAULTS, cfg)
    if legacy and (cfg.get("setup_complete") or cfg.get("web", {}).get("password_hash")):
        cfg["features"] = {fid: True for fid in FEATURE_IDS}      # it was already in use: nothing that worked is hidden or switched off
        cfg["setup_complete"] = True
    else:
        cfg["features"] = {fid: bool((cfg.get("features") or {}).get(fid, False)) for fid in FEATURE_IDS}
    # Each template is merged against the defaults for its own kind so new
    # template options show up on old templates.
    tpls = {}
    for name, t in (cfg.get("templates") or {}).items():
        kind = t.get("kind", "reminder") if isinstance(t, dict) else "reminder"
        if kind not in KIND_DEFAULTS:
            kind = "reminder"
        tpls[name] = merge(default_template(kind), t if isinstance(t, dict) else {})
    for k in BUILTIN_TEMPLATES:
        tpls.setdefault(k, default_template(k))
    cfg["templates"] = tpls
    mig = cfg.setdefault("migrations", {})
    if not mig.get("min_height_30"):               # once: the old 80 mm minimum (the old default) becomes 30 mm; a value you set yourself afterwards is kept
        for t in tpls.values():
            if isinstance(t, dict) and t.get("min_height_mm") == 80:
                t["min_height_mm"] = 30
        mig["min_height_30"] = True
    if not cfg["web"].get("secret_key"):
        cfg["web"]["secret_key"] = secrets.token_hex(32)
    return cfg


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DATA_DIR / "config.json"
        self._lock = threading.RLock()
        self._data: dict = {}
        self.load()

    @staticmethod
    def _fast_prints(saved: dict) -> bool:
        """Once, for a config saved by an older version: check the inbox every 5 s (was 30) and don't make slips wait for the slow AI plot step
        (you can switch those back on).  Returns True if it changed anything."""
        mig = saved.setdefault("migrations", {})
        if mig.get("fast_prints"):
            return False
        e = saved.get("email")
        if isinstance(e, dict) and e.get("poll_seconds") in (30, 60):
            e["poll_seconds"] = 5
        for section, key in (("pinkpanther", "ai_plot"), ("watchlist", "plot_ai"), ("watchlist", "ai")):
            if isinstance(saved.get(section), dict):
                saved[section][key] = False
        mig["fast_prints"] = True
        return True

    @staticmethod
    def _one_slip(saved: dict) -> bool:
        """Once, for a config saved by an earlier version: a movie / TV email makes ONE slip, and birthday reminders are kept on the birthday list."""
        mig = saved.setdefault("migrations", {})
        if mig.get("one_slip"):
            return False
        if isinstance(saved.get("watchlist"), dict):
            saved["watchlist"]["max_titles"] = 1
        if isinstance(saved.get("birthdays"), dict):
            saved["birthdays"]["learn"] = True
        mig["one_slip"] = True
        return True

    def load(self) -> None:
        with self._lock:
            saved = {}
            if self.path.exists():
                try:
                    saved = json.loads(self.path.read_text("utf-8"))
                except (OSError, ValueError):
                    # Keep the broken file for inspection rather than losing it.
                    try:
                        self.path.rename(self.path.with_suffix(".json.broken"))
                    except OSError:
                        pass
            migrated = bool(saved) and (self._fast_prints(saved) | self._one_slip(saved))
            self._data = normalise(saved)
            if not self.path.exists() or migrated:
                self.save()

    @property
    def data(self) -> dict:
        return self._data

    def get(self, *keys, default=None):
        cur = self._data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def replace(self, new: dict) -> None:
        with self._lock:
            self._data = normalise(new)
            self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2)
                os.chmod(tmp, 0o600)  # holds the Gmail app password
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def template(self, name: str | None) -> dict:
        tpls = self._data["templates"]
        if name and name in tpls:
            return tpls[name]
        return tpls["reminder"]
