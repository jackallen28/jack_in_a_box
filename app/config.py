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

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
DATA_DIR = Path(os.environ.get("JACK_IN_A_BOX_DATA", ROOT / "data"))

BUILTIN_TEMPLATES = ("reminder", "calendar", "email", "message")

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
        "min_height_mm": 80,
        "max_height_mm": 700,
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
            "size_mm": 20,
            "max": 6,
            "gap_mm": 3,
            "align": "center",
        },
        "graphic": {
            "enabled": kind == "calendar",
            "file": "",
            "max_height_mm": 25,
            "align": "center",
            "dither": "floyd",
        },
        "heading": {
            "enabled": True,
            "font": "courier-prime",
            "bold": True,
            "size_pt": 15,
            "align": "left",
            "show_subline": True,
        },
        "body": {
            "font": "courier-prime",
            "size_pt": 11,
            "align": "left",
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
        "poll_seconds": 30,
        "after": "mark_read",  # mark_read | move
        "move_folder": "Printed",
        "allowed_senders": [],       # YOU (admins): reminders print immediately
        "require_auth_pass": True,
        "max_cards_per_email": 10,
        "max_images": 3,
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
        "show_original_sender": True,
    },
    "llm": {
        "enabled": False,
        "url": "http://127.0.0.1:11434",
        "model": "qwen2.5:0.5b",
        "timeout": 180,
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
    cfg = merge(DEFAULTS, cfg)
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
    if not cfg["web"].get("secret_key"):
        cfg["web"]["secret_key"] = secrets.token_hex(32)
    return cfg


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DATA_DIR / "config.json"
        self._lock = threading.RLock()
        self._data: dict = {}
        self.load()

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
            self._data = normalise(saved)
            if not self.path.exists():
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
