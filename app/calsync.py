"""Google Calendar (public / secret iCal URL) -> timed event printouts.

When an event is printed
------------------------
Let D be the event's date and T the configured print time (default 07:00).

  * If we learn about the event before T on the day before (D-1):  print at D-1 T.
  * If we learn about it later, but before T on the day itself:    print at D T.
  * If it turns up even later and hasn't started yet:               print straight
    away (``late_policy = print_now``) or never (``skip``).

``also_day_of`` additionally prints a second copy at D T for events that got
the day-before print.  "Learn about" means the moment *this app* first saw the
event in the feed - Google does not expose creation time reliably.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import urllib.request
from datetime import date, datetime, time, timedelta

from .config import ConfigStore
from .models import Card, Section
from .pipeline import local_now, local_tz
from . import features, icons as icon_lib, plugins, rules
from .service import Service

log = logging.getLogger("calendar")


def _parse_hhmm(s: str) -> time:
    try:
        h, m = str(s).split(":")
        return time(int(h), int(m))
    except Exception:
        return time(7, 0)


def fetch_ics(url: str) -> bytes:
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.startswith(("https://", "http://")):
        raise ValueError("Calendar URL must start with https:// (or webcal://)")
    req = urllib.request.Request(url, headers={"User-Agent": "jack-in-a-box/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = r.read(20_000_001)
    if len(data) > 20_000_000:
        raise ValueError("Calendar file is larger than 20 MB")
    if b"BEGIN:VCALENDAR" not in data[:4096]:
        raise ValueError("That URL did not return a calendar (.ics) file - check it is the public/secret iCal address")
    return data


def parse_events(ics: bytes, start: datetime, end: datetime, tz) -> list[dict]:
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(ics)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        s = ev.decoded("DTSTART")
        e = ev.decoded("DTEND") if ev.get("DTEND") else None
        all_day = not isinstance(s, datetime)
        if all_day:
            s_local = datetime.combine(s, time(0, 0), tzinfo=tz)
            e_local = datetime.combine(e, time(0, 0), tzinfo=tz) if e else s_local + timedelta(days=1)
        else:
            s_local = s.astimezone(tz) if s.tzinfo else s.replace(tzinfo=tz)
            e_local = (e.astimezone(tz) if e.tzinfo else e.replace(tzinfo=tz)) if isinstance(e, datetime) else s_local
        out.append({
            "uid": str(ev.get("UID", "")), "summary": str(ev.get("SUMMARY", "") or "(no title)"),
            "location": str(ev.get("LOCATION", "") or ""),
            "description": str(ev.get("DESCRIPTION", "") or ""),
            "start": s_local.isoformat(), "end": e_local.isoformat(), "all_day": all_day,
        })
    return out


def compute_due(first_seen: datetime, start: datetime, cfg: dict) -> list[datetime | None]:
    """Return the list of print times for an event (usually one)."""
    c = cfg["calendar"]
    t = _parse_hhmm(c.get("print_time", "07:00"))
    d = start.date()
    a = datetime.combine(d - timedelta(days=1), t, tzinfo=start.tzinfo)
    b = datetime.combine(d, t, tzinfo=start.tzinfo)
    if first_seen <= a:
        return [a, b] if c.get("also_day_of") else [a]
    if first_seen <= b:
        return [b]
    if c.get("late_policy", "print_now") == "print_now" and start > first_seen:
        return [first_seen]
    return [None]


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>|</div>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def fmt_range(ev: dict, cfg: dict) -> str:
    s, e = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])
    f24 = cfg["general"].get("time_format", "12h") == "24h"
    tf = (lambda t: t.strftime("%H:%M")) if f24 else (lambda t: t.strftime("%I:%M %p").lstrip("0"))
    day = lambda d: f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    if ev["all_day"]:
        last = e - timedelta(days=1)
        return f"{day(s)} - {day(last)} (all day)" if last.date() > s.date() else f"{day(s)} (all day)"
    if e.date() != s.date():
        return f"{day(s)} {tf(s)} -> {day(e)} {tf(e)}"
    return f"{day(s)}, {tf(s)}" + (f" - {tf(e)}" if e > s else "")


def card_for_event(ev: dict, cfg: dict, now: datetime, template: str = "calendar", cal_name: str = "") -> Card:
    s = datetime.fromisoformat(ev["start"])
    delta = (s.date() - now.date()).days
    day = "TODAY" if delta == 0 else "TOMORROW" if delta == 1 else s.strftime("%A").upper()
    n = int(cfg["calendar"].get("notes_chars", 300))
    title, t_ids = icon_lib.extract_tags(ev["summary"], cfg)          # "[school] Parent night" -> school symbol
    notes, n_ids = icon_lib.extract_tags(_strip_html(ev.get("description", "")), cfg)
    if n and len(notes) > n:
        notes = notes[:n].rsplit(" ", 1)[0] + "..."
    secs = [Section("when", text=fmt_range(ev, cfg), strong=True)]
    if ev.get("location"):
        secs.append(Section("where", text=ev["location"].replace("\\,", ",")))
    if notes and n:
        secs.append(Section("notes", text=notes))
    return Card(kind="calendar", heading=title, sections=secs, template=template, source="calendar",
                icons=[*t_ids, *[i for i in n_ids if i not in t_ids]],
                created=now, vars={"day": day, "date": f"{s.strftime('%a')} {s.day} {s.strftime('%b')}",
                                   "calendar": cal_name})


class CalendarSync(threading.Thread):
    def __init__(self, cfg: ConfigStore, svc: Service):
        super().__init__(name="calendar", daemon=True)
        self.cfg, self.svc = cfg, svc
        self.last_sync = 0.0
        self.status = {"last_sync": "", "error": "", "events": 0}
        self.kick = threading.Event()

    def run(self):
        import time as _t
        while not self.svc.stop.is_set():
            try:
                c = self.cfg.data
                if c["calendar"].get("enabled", True) and c["calendars"]:
                    if _t.time() - self.last_sync >= max(1, int(c["calendar"].get("sync_minutes", 15))) * 60:
                        self.sync()
                    self.tick()
            except Exception:
                log.exception("Calendar loop error")
            self.kick.wait(30)
            self.kick.clear()

    # ------------------------------------------------------------- sync
    def sync(self) -> None:
        import time as _t
        c = self.cfg.data
        tz, now = local_tz(c), local_now(c)
        self.last_sync = _t.time()
        if not features.on(c, "calendar"):
            return
        window_end = now + timedelta(days=int(c["calendar"].get("lookahead_days", 14)))
        errors = []
        total = 0
        for cal in c["calendars"]:
            if not cal.get("enabled", True) or not cal.get("url"):
                continue
            cal_id = cal.get("id") or cal["url"][-24:]
            try:
                events = parse_events(fetch_ics(cal["url"]), now - timedelta(days=1), window_end, tz)
            except Exception as e:
                errors.append(f"{cal.get('name', 'calendar')}: {e}")
                log.warning("Calendar '%s' sync failed: %s", cal.get("name"), e)
                continue
            seen_keys = set()
            for ev in events:
                if c["calendar"].get("skip_all_day") and ev["all_day"]:
                    continue
                total += 1
                self._upsert(cal_id, cal, ev, now, seen_keys)
            # events that vanished from the feed before printing were cancelled/moved
            for row in self.svc.db.all("SELECT key FROM cal_events WHERE cal_id=? AND status='pending'", (cal_id,)):
                base = row["key"].split("#")[0]
                if base not in seen_keys:
                    self.svc.db.execute("UPDATE cal_events SET status='removed' WHERE key=?", (row["key"],))
        self.status = {"last_sync": now.strftime("%d %b %H:%M"), "error": "; ".join(errors), "events": total}

    def _upsert(self, cal_id: str, cal: dict, ev: dict, now: datetime, seen_keys: set) -> None:
        key = f"{cal_id}|{ev['uid']}|{ev['start']}"
        seen_keys.add(key)
        ev["_template"] = cal.get("template") or "calendar"
        ev["_cal"] = cal.get("name", "")
        db = self.svc.db
        row = db.one("SELECT key, status FROM cal_events WHERE key=?", (key,))
        if row:
            if row["status"] == "pending":   # keep text up to date if the title/notes were edited
                for k in (key, key + "#2"):
                    db.execute("UPDATE cal_events SET data=?, end=? WHERE key=?", (json.dumps(ev), ev["end"], k))
            return
        start = datetime.fromisoformat(ev["start"])
        dues = compute_due(now, start, self.cfg.data)
        for i, due in enumerate(dues):
            k = key if i == 0 else f"{key}#{i + 1}"
            db.execute(
                "INSERT OR IGNORE INTO cal_events(key, cal_id, start, end, first_seen, due, status, data) VALUES (?,?,?,?,?,?,?,?)",
                (k, cal_id, ev["start"], ev["end"], now.isoformat(), due.isoformat() if due else None,
                 "pending" if due else "skipped", json.dumps(ev)))

    # ------------------------------------------------------------- print
    def tick(self) -> None:
        c = self.cfg.data
        now = local_now(c)
        for row in self.svc.db.all("SELECT * FROM cal_events WHERE status='pending' AND due IS NOT NULL ORDER BY due"):
            if datetime.fromisoformat(row["due"]) > now:
                continue
            ev = json.loads(row["data"])
            if datetime.fromisoformat(row["end"]) <= now:
                self.svc.db.execute("UPDATE cal_events SET status='missed' WHERE key=?", (row["key"],))
                log.info("Skipped '%s' - it had already finished", ev["summary"])
                continue
            res = rules.evaluate(c.get("rules", []), {"subject": ev["summary"], "body": ev["description"],
                                                       "sender": ev.get("_cal", ""), "calendar": ev.get("_cal", "")})
            if res["skip"]:
                self.svc.db.execute("UPDATE cal_events SET status='skipped' WHERE key=?", (row["key"],))
                continue
            card = card_for_event(ev, c, now, ev.get("_template", "calendar"), ev.get("_cal", ""))
            rules.apply_to_card(card, res)
            cards = plugins.process([card], {"incoming": None, "config": c, "source": "calendar"}) \
                if c["integrations"].get("plugins_enabled", True) else [card]
            for cd in cards:
                self.svc.submit(cd, res["copies"])
            self.svc.db.execute("UPDATE cal_events SET status='printed', printed_at=? WHERE key=?",
                                (now.isoformat(), row["key"]))

    def upcoming(self) -> list[dict]:
        rows = self.svc.db.all("SELECT * FROM cal_events WHERE status IN ('pending','printed') AND end>=? ORDER BY start LIMIT 60",
                               (local_now(self.cfg.data).isoformat(),))
        out = []
        for r in rows:
            ev = json.loads(r["data"])
            out.append({"title": ev["summary"], "start": r["start"], "due": r["due"], "status": r["status"],
                        "calendar": ev.get("_cal", "")})
        return out
