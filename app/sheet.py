"""Google Form -> Google Sheet -> printouts.

You make a Google Form (heading, body, symbols, an image upload ...).  Google puts every submission in a
spreadsheet.  A small script pasted into that spreadsheet (app/apps_script.js) lets this app, using only a web
address and a secret code, list the rows, fetch uploaded images and delete rows.  This module:

  * checks the sheet every minute and prints each NEW row (once - rows are remembered)
  * deletes a row from the sheet only after its printout has actually PRINTED (a row that is waiting for the
    button, or for a printer that is switched off, stays in the sheet until it prints)
  * never holds a Google login on the Pi: no passwords, no keys - just the URL and the code
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from . import icons as icon_lib
from .config import ConfigStore
from .mailparse import Incoming, _load_image
from .pipeline import build_cards, classify_sender, message_card
from .service import Service
from .textproc import _dedupe

log = logging.getLogger("sheet")
FILE_ID = re.compile(r"(?:[?&]id=|/d/)([A-Za-z0-9_-]{10,})")


class SheetError(Exception):
    pass


def valid_url(url: str) -> bool:
    """Only Google Apps Script web apps: this app never fetches arbitrary addresses on your behalf."""
    u = urllib.parse.urlparse(url or "")
    return (u.scheme == "https" and u.hostname == "script.google.com"
            and re.fullmatch(r"/(?:a/macros/[^/]+/|macros/)s/[A-Za-z0-9_-]+/exec", u.path) is not None)


def call(cfg: dict, action: str, timeout: float = 40, **params) -> dict:
    sc = cfg["sheet"]
    if not valid_url(sc.get("url", "")):
        raise SheetError("The web app URL should look like https://script.google.com/macros/s/.../exec")
    if not sc.get("token"):
        raise SheetError("No secret code yet - generate the script on the Google Form page first")
    q = urllib.parse.urlencode({"action": action, "token": sc["token"], **params})
    req = urllib.request.Request(f"{sc['url']}?{q}", headers={"User-Agent": "jack-in-a-box/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(30_000_000)
    except urllib.error.HTTPError as e:
        raise SheetError(f"Google answered {e.code}. Check the deployment: 'Execute as: Me', 'Who has access: Anyone'.") from e
    except (urllib.error.URLError, OSError) as e:
        raise SheetError(f"Couldn't reach Google ({getattr(e, 'reason', e)})") from e
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SheetError("Google returned a web page instead of data. Re-deploy the script as 'Anyone' and use the newest /exec URL.") from e
    if not data.get("ok"):
        raise SheetError(data.get("error") or "the script reported an error")
    return data


# --------------------------------------------------------------------------- understanding the columns
def detect_columns(headers: list[str]) -> dict:
    """Work out which column is which from the question titles you gave the form."""
    cols: dict = {"type": None, "heading": None, "body": None, "image": None, "email": None, "symbols": []}
    used: set = set()
    norm = {h: re.sub(r"[^a-z0-9]+", " ", h.lower()).strip() for h in headers}

    def take(role, test, many=False):
        for h in headers:
            if h in used or not norm[h]:
                continue
            if test(norm[h]):
                used.add(h)
                if many:
                    cols[role].append(h)
                else:
                    cols[role] = h
                    return

    take("email", lambda n: "email" in n)
    take("symbols", lambda n: re.search(r"\b(symbol|icon)\b", n) is not None, many=True)
    take("image", lambda n: re.search(r"image|photo|picture|upload|\bfile\b", n) is not None)
    take("heading", lambda n: re.search(r"heading|title|subject", n) is not None)
    take("type", lambda n: re.search(r"reminder", n) is not None or n in ("type", "kind"))
    take("body", lambda n: re.search(r"body|text|note|detail|message", n) is not None)
    if cols["body"] is None:                                  # fall back: first unused, non-timestamp column
        for h in headers[1:]:
            if h not in used and norm[h]:
                cols["body"] = h
                used.add(h)
                break
    return cols


# --------------------------------------------------------------------------- the poller
class SheetPoller(threading.Thread):
    def __init__(self, cfg: ConfigStore, svc: Service):
        super().__init__(name="sheet", daemon=True)
        self.cfg, self.svc = cfg, svc
        self.kick = threading.Event()
        self.status = {"state": "disabled", "last_check": "", "error": "", "processed": 0, "waiting": 0}
        self._last_poll = 0.0
        self._err_logged = False

    # ------------------------------------------------------------- loop
    def run(self) -> None:
        while not self.svc.stop.is_set():
            sc = self.cfg.data["sheet"]
            if sc.get("enabled") and sc.get("url") and sc.get("token"):
                try:
                    if self.kick.is_set() or time.time() - self._last_poll >= max(15, int(sc.get("poll_seconds", 60))):
                        self._last_poll = time.time()
                        self.poll_once()
                    else:
                        self.reap()
                    self.status.update(state="ok", error="")
                    self._err_logged = False
                except SheetError as e:
                    self.status.update(state="error", error=str(e))
                    if not self._err_logged:
                        log.error("Google Sheet: %s", e)
                        self._err_logged = True
                except Exception:
                    log.exception("Google Sheet loop error")
            else:
                self.status["state"] = "disabled"
            self.kick.wait(10)
            self.kick.clear()

    # ------------------------------------------------------------- one pass
    def poll_once(self) -> int:
        c = self.cfg.data
        data = call(c, "rows")
        cols = detect_columns(data.get("headers", []))
        done = 0
        waiting = 0
        for row in data.get("rows", []):
            rid = row["id"]
            if self.svc.db.one("SELECT 1 FROM sheet_rows WHERE id=?", (rid,)):
                waiting += 1
                continue
            try:
                self.process_row(rid, row.get("values", {}), cols)
                done += 1
            except SheetError:
                raise
            except Exception as e:                           # a bad row must not block the rows behind it
                log.exception("Couldn't print a sheet row")
                self._record(rid, "error", [], f"{type(e).__name__}: {e}")
        self.status.update(waiting=waiting + done, last_check=datetime.now().strftime("%d %b %H:%M:%S"))
        self.reap()
        return done

    def _record(self, rid: str, status: str, jobs: list[int], note: str = "") -> None:
        self.svc.db.execute("INSERT OR REPLACE INTO sheet_rows(id, first_seen, status, jobs, note) VALUES (?,?,?,?,?)",
                            (rid, datetime.now().isoformat(timespec="seconds"), status, json.dumps(jobs), note))

    def process_row(self, rid: str, values: dict, cols: dict) -> None:
        c = self.cfg.data
        g = lambda role: (values.get(cols[role]) or "").strip() if cols.get(role) else ""
        self.svc.touch()
        notes: list[str] = []
        email = g("email").lower()
        kind = "message" if re.search("message", g("type"), re.I) else "reminder"

        # --- who sent it?  (a verified Google address is trustworthy; a typed one is not, so the form must verify it)
        role, friend = classify_sender(email, c) if email else (None, None)
        policy = c["sheet"].get("unknown_policy", "hold")
        hold = False
        if role == "friend":
            kind = "message"
            hold = bool(c["messages"].get("hold_for_button", True)) and not friend.get("bypass_queue", False)
        elif role is None:
            if policy == "ignore":
                self._record(rid, "ignored", [], f"not on your lists ({email or 'no email'})")
                return
            hold = policy == "hold"
        name = (friend or {}).get("name") or (email.split("@")[0] if email else "Form")

        # --- symbols (columns "Symbol 1/2/3")
        sym_ids: list[str] = []
        for h in cols.get("symbols", []):
            val = re.sub(r"^\[|\]$", "", (values.get(h) or "").strip()).strip()
            if val:
                _, ids = icon_lib.extract_tags(f"[{val}]", c)
                if ids:
                    sym_ids += ids
                else:
                    notes.append(f"unknown symbol '{val}'")

        # --- picture(s) uploaded to the form (they live in your Google Drive)
        images = []
        file_ids = _dedupe(FILE_ID.findall(g("image")))[: int(c["email"].get("max_images", 3))]
        for fid in file_ids:
            try:
                f = call(c, "file", id=fid)
                im = _load_image(base64.b64decode(f["data"]), 0)
                if im is None:
                    raise SheetError("not a picture")
                images.append(im)
            except SheetError as e:
                notes.append(f"image skipped ({e})")
            except Exception as e:
                notes.append(f"image skipped ({type(e).__name__})")

        inc = Incoming(subject=g("heading"), body=g("body"), sender_addr=email or "form", sender_name=name, images=images)
        if kind == "message":
            card = message_card(inc, friend or {"name": name, "email": email}, c)
            cards, copies = ([card] if card else []), 1
        else:
            cards, copies = build_cards(inc, c)
        job_ids: list[int] = []
        for card in cards:
            card.source = "form"
            card.icons = _dedupe(sym_ids + card.icons)
            card.notes = [*card.notes, *notes]
            job_ids += self.svc.submit(card, copies, hold=hold)
        self._record(rid, "queued" if job_ids else "empty", job_ids, "; ".join(notes))
        log.info("Form row -> %d printout(s)%s%s", len(job_ids), " (held for the button)" if hold and job_ids else "",
                 f" [{'; '.join(notes)}]" if notes else "")

    # ------------------------------------------------------------- tidy up the sheet
    def reap(self) -> int:
        """Delete from the sheet every row whose printout(s) have printed (or that had nothing to print)."""
        rows = self.svc.db.all("SELECT id, status, jobs FROM sheet_rows WHERE status IN ('queued','empty')")
        ready: list[str] = []
        for r in rows:
            if r["status"] == "empty":
                ready.append(r["id"])
                continue
            ids = json.loads(r["jobs"] or "[]")
            if not ids:
                continue
            jobs = self.svc.db.all(f"SELECT status, error FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids)
            if any(j["status"] in ("pending", "held") for j in jobs):
                continue                                              # still waiting (printer off / button not pressed)
            bad = [j for j in jobs if j["status"] == "failed" and (j["error"] or "") not in ("Cancelled", "Discarded")]
            if bad:
                self.svc.db.execute("UPDATE sheet_rows SET status='error', note=? WHERE id=?",
                                    (f"printing failed: {bad[0]['error']}", r["id"]))
                continue                                              # keep the row in the sheet so you can see it
            ready.append(r["id"])                                     # printed, or you cancelled/discarded it
        removed = 0
        for i in range(0, len(ready), 25):
            batch = ready[i:i + 25]
            call(self.cfg.data, "delete", ids=",".join(batch))
            for rid in batch:
                self.svc.db.execute("UPDATE sheet_rows SET status='deleted' WHERE id=?", (rid,))
            removed += len(batch)
        if removed:
            log.info("Removed %d printed row(s) from the Google Sheet", removed)
        self.status["processed"] += removed
        return removed
