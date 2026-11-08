"""Print queue, history and background workers' shared services."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from . import aifeedback, birthdays, plugins
from .config import DATA_DIR, ConfigStore
from .db import DB
from .models import Card
from .printer import PrinterError, print_image, send_raw
from .render import render_card

log = logging.getLogger("service")
LOGBUF: deque = deque(maxlen=400)


class RingHandler(logging.Handler):
    def emit(self, record):
        LOGBUF.append({"ts": datetime.fromtimestamp(record.created).strftime("%d %b %H:%M:%S"),
                       "level": record.levelname, "name": record.name, "msg": record.getMessage()})


class Service:
    def __init__(self, cfg: ConfigStore, data_dir: Path | None = None):
        self.cfg = cfg
        self.data_dir = Path(data_dir or DATA_DIR)
        self.hist_dir = self.data_dir / "history"
        self.hist_dir.mkdir(parents=True, exist_ok=True)
        self.db = DB(self.data_dir / "reminders.db")
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.printer_error = ""
        self.last_print = ""
        self.last_activity = time.time()      # last print/email/button/web action (for idle power-off)
        self._rate_logged = False
        self._worker: threading.Thread | None = None

    def touch(self) -> None:
        self.last_activity = time.time()

    # ---------------------------------------------------------------- submit
    def render(self, card: Card) -> Image.Image:
        c = self.cfg.data
        name = card.template or card.kind
        return render_card(card, c["templates"].get(name) or c["templates"]["reminder"], c["printer"], c["icons"])

    def _recent_duplicate(self, card: Card, minutes: int = 10) -> bool:
        """The same movie / episode slip already queued a moment ago (an email sent twice by the phone): one print is enough."""
        since = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        title = (card.heading or card.summary())[:120]
        return bool(self.db.one("SELECT 1 FROM jobs WHERE kind=? AND title=? AND source='email' AND created>=? AND status!='failed'", (card.kind, title, since)))

    def submit(self, card: Card, copies: int = 1, hold: bool = False, due: datetime | None = None) -> list[int]:
        """Queue a printout.  ``hold`` waits for the button; ``due`` (a timezone-aware time in the future) waits until then.
        Either way it is in the queue straight away, so you can see it, cancel it, or press Print now."""
        self.touch()
        try:
            card = birthdays.restyle(card, self.cfg.data)            # a reminder about a birthday gets the BIRTHDAY template
            if card.kind == "birthday" and card.source != "birthday":
                added = birthdays.learn(card, self.cfg)              # ...and the birthday is kept on your list so it comes round every year
                if added:
                    from .models import Section
                    card.sections.append(Section("notes", text=f"Added {added['name']} to your birthday list: {added['day']} {birthdays.MONTH_NAMES[added['month'] - 1]} every year."))
        except Exception:
            log.exception("Birthday recognition failed (printing the slip as it was)")
        if card.kind in ("watchlist", "pinkpanther") and card.source == "email" and self._recent_duplicate(card):
            log.info("Skipped a repeat of %s '%s' (the same slip was queued in the last 10 minutes)", card.kind, (card.heading or "")[:40])
            return []
        later = due is not None and due > datetime.now(timezone.utc)
        status = "held" if hold else "scheduled" if later else "pending"
        img = self.render(card)
        ids = []
        for _ in range(max(1, copies)):
            now = datetime.now()
            cur = self.db.execute(
                "INSERT INTO jobs(created, kind, source, title, notes, status, due) VALUES (?,?,?,?,?,?,?)",
                (now.isoformat(timespec="seconds"), card.kind, card.source,
                 (card.heading or card.summary())[:120], json.dumps(card.notes), status,
                 due.astimezone(timezone.utc).isoformat(timespec="seconds") if later else None))
            jid = cur.lastrowid
            path = self.hist_dir / f"{jid}.png"
            img.save(path)
            self.db.execute("UPDATE jobs SET png=? WHERE id=?", (path.name, jid))
            ids.append(jid)
        if card.trace and self.cfg.data["llm"].get("keep_history", True):
            try:
                aifeedback.record(self.db, card.trace, ids[0])
            except Exception:
                log.exception("Couldn't keep the AI summary for review")
        log.info("%s %s '%s'%s", "Holding for button:" if hold else (f"Scheduled for {due.astimezone():%a %d %b %H:%M}:" if later else "Queued"), card.kind,
                 (card.heading or card.summary())[:50], f" x{copies}" if copies > 1 else "")
        self.wake.set()
        return ids

    # ---------------------------------------------------------------- held messages (button queue)
    def held_count(self) -> int:
        return self.db.one("SELECT COUNT(*) n FROM jobs WHERE status='held'")["n"]

    def release_held(self) -> int:
        """Button pressed: let every waiting message print (oldest first)."""
        self.touch()
        n = self.held_count()
        if n:
            self.db.execute("UPDATE jobs SET status='pending' WHERE status='held'")
            log.info("Released %d waiting message(s) to the printer", n)
            self.wake.set()
        return n

    def promote_due(self, now: datetime | None = None) -> int:
        """Scheduled printouts whose time has come join the print queue."""
        now = now or datetime.now(timezone.utc)
        n = 0
        for j in self.db.all("SELECT id, due FROM jobs WHERE status='scheduled'"):
            try:
                due = datetime.fromisoformat(j["due"])
            except (TypeError, ValueError):
                due = now                                   # no readable time: don't leave it stuck
            if due <= now:
                n += self.db.execute("UPDATE jobs SET status='pending' WHERE id=? AND status='scheduled'", (j["id"],)).rowcount
        if n:
            log.info("%d scheduled printout(s) are due", n)
            self.wake.set()
        return n

    def release_one(self, job_id: int) -> bool:
        """Print this one now (a message waiting for the button, or a scheduled printout)."""
        cur = self.db.execute("UPDATE jobs SET status='pending' WHERE id=? AND status IN ('held','scheduled')", (job_id,))
        self.wake.set()
        return cur.rowcount > 0

    def discard_held(self, job_id: int | None = None) -> int:
        sql, args = ("UPDATE jobs SET status='failed', error='Discarded' WHERE status='held'", ())
        if job_id is not None:
            sql, args = sql + " AND id=?", (job_id,)
        return self.db.execute(sql, args).rowcount

    def submit_image(self, img: Image.Image, title: str, kind: str = "sheet", source: str = "web", hold: bool = False) -> int:
        """Queue an already-drawn bitmap (e.g. the symbol cheat-sheet, or a page sent by AirPrint).  ``hold`` waits for the button."""
        cur = self.db.execute("INSERT INTO jobs(created, kind, source, title, notes, status) VALUES (?,?,?,?,?,?)",
                              (datetime.now().isoformat(timespec="seconds"), kind, source, title[:120], "[]", "held" if hold else "pending"))
        jid = cur.lastrowid
        path = self.hist_dir / f"{jid}.png"
        img.save(path)
        self.db.execute("UPDATE jobs SET png=? WHERE id=?", (path.name, jid))
        self.wake.set()
        return jid

    def submit_raw(self, data: bytes, title: str, source: str = "driver", hold: bool = False) -> int:
        """Queue printer data made by another computer's own driver (ESC/POS).  It prints exactly as sent."""
        from .render import pt, text_block
        self.touch()
        cur = self.db.execute("INSERT INTO jobs(created, kind, source, title, notes, status) VALUES (?,?,?,?,?,?)",
                              (datetime.now().isoformat(timespec="seconds"), "raw", source, title[:120], "[]", "held" if hold else "pending"))
        jid = cur.lastrowid
        (self.hist_dir / f"{jid}.raw").write_bytes(data)
        label = text_block(f"Printed by another computer's driver\n{max(1, len(data) // 1024)} KB\n{title}", "courier-prime", pt(10), 540)
        card = Image.new("L", (576, label.height + 40), 255)         # a stand-in picture for the history page
        card.paste(label, (18, 20))
        ImageDraw.Draw(card).rectangle([0, 0, 575, card.height - 1], outline=0, width=2)
        path = self.hist_dir / f"{jid}.png"
        card.save(path)
        self.db.execute("UPDATE jobs SET png=? WHERE id=?", (path.name, jid))
        self.wake.set()
        return jid

    def reprint(self, job_id: int) -> int | None:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row and row["kind"] == "raw":
            raw = self.hist_dir / f"{job_id}.raw"
            return self.submit_raw(raw.read_bytes(), row["title"], "reprint") if raw.exists() else None
        if not row or not row["png"]:
            return None
        cur = self.db.execute(
            "INSERT INTO jobs(created, kind, source, title, notes, status, png) VALUES (?,?,?,?,?, 'pending', ?)",
            (datetime.now().isoformat(timespec="seconds"), row["kind"], "reprint", row["title"], row["notes"], row["png"]))
        self.wake.set()
        return cur.lastrowid

    # ---------------------------------------------------------------- worker
    def start(self) -> None:
        self._worker = threading.Thread(target=self._loop, name="print-worker", daemon=True)
        self._worker.start()

    def _printed_last_hour(self) -> int:
        since = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        r = self.db.one("SELECT COUNT(*) n FROM jobs WHERE status='printed' AND printed_at>=?", (since,))
        return r["n"] if r else 0

    def _loop(self) -> None:
        last_cleanup = 0.0
        while not self.stop.is_set():
            try:
                self.process_pending()
                if time.time() - last_cleanup > 6 * 3600:
                    self.cleanup()
                    last_cleanup = time.time()
            except Exception:
                log.exception("Print worker error")
            self.wake.wait(timeout=3)
            self.wake.clear()

    def process_pending(self) -> None:
        self.promote_due()
        pc = self.cfg.data["printer"]
        retry = timedelta(seconds=max(5, int(pc.get("retry_seconds", 30))))
        for job in self.db.all("SELECT * FROM jobs WHERE status='pending' ORDER BY id"):
            if job["last_try"] and datetime.now() - datetime.fromisoformat(job["last_try"]) < retry:
                return                      # printer was unhappy a moment ago; keep order and wait
            if self._printed_last_hour() >= int(pc.get("max_prints_per_hour", 40)):
                if not self._rate_logged:
                    log.warning("Hourly print limit reached - holding jobs until it clears")
                    self._rate_logged = True
                return
            self._rate_logged = False
            if not self.print_job(job):
                return

    def print_job(self, job: dict) -> bool:
        pc = self.cfg.data["printer"]
        path = self.hist_dir / (job["png"] or "")
        try:
            if job["kind"] == "raw":                         # another computer's printer driver: forward its bytes untouched
                send_raw((self.hist_dir / f"{job['id']}.raw").read_bytes(), pc)
            else:
                img = Image.open(path).convert("1")
                print_image(img, pc)
        except (PrinterError, OSError) as e:
            attempts = job["attempts"] + 1
            self.printer_error = str(e)
            status = "failed" if attempts >= int(pc.get("max_attempts", 20)) else "pending"
            self.db.execute("UPDATE jobs SET attempts=?, error=?, status=?, last_try=? WHERE id=?",
                            (attempts, str(e), status, datetime.now().isoformat(timespec="seconds"), job["id"]))
            if attempts in (1, 5) or status == "failed":
                log.error("Print failed (attempt %d): %s", attempts, e)
            return False
        now = datetime.now().isoformat(timespec="seconds")
        self.db.execute("UPDATE jobs SET status='printed', error=NULL, printed_at=?, attempts=attempts+1 WHERE id=?",
                        (now, job["id"]))
        self.printer_error = ""
        self.last_print = now
        try:
            waited = (datetime.fromisoformat(now) - datetime.fromisoformat(job["created"])).total_seconds()
        except (ValueError, TypeError, KeyError):
            waited = 0
        log.info("Printed #%s '%s' (%.0fs after it was queued)", job["id"], job["title"], waited)
        info = {"id": job["id"], "kind": job["kind"], "title": job["title"], "source": job["source"], "printed_at": now}
        plugins.on_printed(info)
        self._webhook(info)
        return True

    def _webhook(self, info: dict) -> None:
        url = self.cfg.data["integrations"].get("webhook_url", "").strip()
        if not url.startswith(("http://", "https://")):
            return

        def post():
            try:
                req = urllib.request.Request(url, data=json.dumps(info).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=8).read()
            except Exception as e:
                log.warning("Webhook failed: %s", e)
        threading.Thread(target=post, daemon=True).start()

    def cleanup(self) -> None:
        keep = int(self.cfg.data["general"].get("history_keep_days", 60))
        cutoff = (datetime.now() - timedelta(days=keep)).isoformat(timespec="seconds")
        for row in self.db.all("SELECT id, png FROM jobs WHERE created<? AND status NOT IN ('pending','scheduled','held')", (cutoff,)):
            still = self.db.one("SELECT COUNT(*) n FROM jobs WHERE png=? AND id!=?", (row["png"], row["id"]))
            if row["png"] and still["n"] == 0:
                (self.hist_dir / row["png"]).unlink(missing_ok=True)
            (self.hist_dir / f"{row['id']}.raw").unlink(missing_ok=True)
        self.db.execute("DELETE FROM jobs WHERE created<? AND status NOT IN ('pending','scheduled','held')", (cutoff,))
        self.db.execute("DELETE FROM seen WHERE ts<?", (cutoff,))
        self.db.execute("DELETE FROM sheet_rows WHERE first_seen<? AND status IN ('deleted','ignored','empty')", (cutoff,))
        self.db.execute("DELETE FROM cal_events WHERE first_seen<? AND status!='pending'", (cutoff,))
        aifeedback.prune(self.db, int(self.cfg.data["llm"].get("history_days", 90)))

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        n = self.db.one("SELECT COUNT(*) n FROM jobs WHERE status='pending'")["n"]
        sched = self.db.all("SELECT due FROM jobs WHERE status='scheduled' ORDER BY due")
        return {"queued": n, "held": self.held_count(), "scheduled": len(sched), "next_due": sched[0]["due"] if sched else None, "printer_error": self.printer_error, "last_print": self.last_print,
                "printed_last_hour": self._printed_last_hour()}
