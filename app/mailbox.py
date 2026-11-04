"""IMAP poller: fetch new mail from the printer's own Gmail and queue prints."""
from __future__ import annotations

import hashlib
import imaplib
import logging
import threading
import time
from datetime import datetime

from .config import ConfigStore
from .mailparse import parse
from .pipeline import build_cards, classify_sender, message_card
from .service import Service

log = logging.getLogger("mail")
MAX_BYTES = 30 * 1024 * 1024


class MailPoller(threading.Thread):
    def __init__(self, cfg: ConfigStore, svc: Service):
        super().__init__(name="mail", daemon=True)
        self.cfg, self.svc = cfg, svc
        self.kick = threading.Event()
        self.status = {"state": "idle", "last_check": "", "error": "", "processed": 0}
        self.last_outcome = "empty"
        self._fail_logged = False

    def run(self):
        while not self.svc.stop.is_set():
            e = self.cfg.data["email"]
            wait = max(3, int(e.get("poll_seconds", 5)))
            if e.get("enabled") and e.get("username") and e.get("password"):
                try:
                    self.poll_once(keep=True)                    # one connection, kept open: a check is one quick round trip, not a new login
                    self.status.update(state="ok", error="")
                    self._fail_logged = False
                except Exception as ex:
                    self._drop()
                    msg = f"{type(ex).__name__}: {ex}"
                    self.status.update(state="error", error=msg)
                    if not self._fail_logged:
                        log.error("Mailbox check failed: %s", msg)
                        self._fail_logged = True
                    wait = min(300, wait * 2)
            else:
                self._drop()
                self.status["state"] = "disabled"
            self.kick.wait(wait)
            self.kick.clear()

    def connect(self) -> imaplib.IMAP4:
        e = self.cfg.data["email"]
        imap = imaplib.IMAP4_SSL(e["host"], int(e.get("port", 993)), timeout=30)
        imap.login(e["username"], e["password"])
        return imap

    # A kept connection (used by the running poller): reused for up to KEEP_SECONDS, dropped on any error or when the account settings change.
    KEEP_SECONDS = 15 * 60
    _kept = None
    _kept_key: tuple = ()
    _kept_at = 0.0

    def _drop(self) -> None:
        imap, self._kept = self._kept, None
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass

    def _open(self, keep: bool):
        """(connection, was it just opened?)"""
        e = self.cfg.data["email"]
        key = (e.get("host"), e.get("port"), e.get("username"), e.get("password"), e.get("folder"))
        if keep and self._kept is not None and key == self._kept_key and time.time() - self._kept_at < self.KEEP_SECONDS:
            return self._kept, False
        self._drop()
        imap = self.connect()
        if keep:
            self._kept, self._kept_key, self._kept_at = imap, key, time.time()
        return imap, True

    def _unseen(self, imap, fresh: bool) -> list:
        e = self.cfg.data["email"]
        if fresh:
            typ, _ = imap.select(e.get("folder") or "INBOX")
            if typ != "OK":
                raise RuntimeError(f"Cannot open folder {e.get('folder')!r}")
        else:
            imap.noop()                                          # lets the server show mail that arrived since the last check
        typ, data = imap.uid("SEARCH", None, "UNSEEN")
        return data[0].split() if typ == "OK" and data and data[0] else []

    def poll_once(self, keep: bool = False) -> int:
        imap, fresh = self._open(keep)
        try:
            try:
                uids = self._unseen(imap, fresh)
            except (imaplib.IMAP4.abort, OSError):
                if fresh:
                    raise
                self._drop()                                     # the server closed a kept connection: log in again, once
                imap, fresh = self._open(keep)
                uids = self._unseen(imap, fresh)
            done = 0
            for uid in uids[:25]:
                typ, msgdata = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in msgdata if isinstance(p, tuple)), b"") if typ == "OK" else b""
                outcome = "error"
                if raw and len(raw) <= MAX_BYTES:
                    try:
                        self.handle(raw)
                        outcome = self.last_outcome
                    except Exception:
                        log.exception("Failed to process an email")
                elif raw:
                    log.warning("Ignored an email larger than %d MB", MAX_BYTES // 1048576)
                self._finish(imap, uid, outcome)
                done += 1
        except Exception:
            self._drop()
            raise
        finally:
            if not keep:
                try:
                    imap.logout()
                except Exception:
                    pass
        self.status["last_check"] = datetime.now().strftime("%d %b %H:%M:%S")
        return done

    # What was done with the email.  Only these are safe to delete afterwards: the printout is already
    # saved in the queue/history, so even if the printer is off nothing is lost.
    DELETABLE = {"printed", "held", "duplicate"}

    def _finish(self, imap: imaplib.IMAP4, uid: bytes, outcome: str = "printed") -> None:
        e = self.cfg.data["email"]
        imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")                     # always mark read first
        mode = e.get("after")
        if mode == "delete" and outcome in self.DELETABLE:
            self._delete(imap, uid)
        elif mode == "move" and e.get("move_folder"):
            folder = e["move_folder"]
            imap.create(folder)  # harmless if it already exists
            if imap.uid("COPY", uid, f'"{folder}"')[0] == "OK":
                imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                imap.expunge()

    def _delete(self, imap: imaplib.IMAP4, uid: bytes) -> None:
        """Delete a printed email.  On Gmail it goes to Trash first (kept 30 days, then gone) so a mistake is
        recoverable; with no trash folder set (other providers) it is removed for good."""
        trash = (self.cfg.data["email"].get("trash_folder") or "").strip()
        if trash and imap.uid("COPY", uid, f'"{trash}"')[0] != "OK":
            log.warning("Couldn't copy an email to %r, so it was only marked read (check 'Trash folder' in the Email settings)", trash)
            return
        imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        imap.expunge()

    def handle(self, raw: bytes) -> list[int]:
        """Process one raw email.  Also used by tests and the 'send test' button.

        Sets ``self.last_outcome``: printed | held | duplicate | blocked | spoofed | dropped | empty.
        Only printed / held / duplicate emails may be deleted afterwards."""
        self.last_outcome = "empty"
        t0 = time.perf_counter()
        self.svc.touch()
        c = self.cfg.data
        inc = parse(raw, min_image_px=int(c["email"].get("min_image_px", 150)),
                    max_images=int(c["email"].get("max_images", 3)))
        key = inc.message_id or hashlib.sha1(raw).hexdigest()
        if self.svc.db.one("SELECT 1 FROM seen WHERE key=?", (key,)):
            self.last_outcome = "duplicate"          # handled on an earlier pass (e.g. the delete failed last time)
            return []
        now = datetime.now().isoformat(timespec="seconds")
        role, friend = classify_sender(inc.sender_addr, c)
        if role is None:
            log.warning("Ignored email from %s - not on your admin list or friends list", inc.sender_addr or "unknown sender")
            self.svc.db.execute("INSERT INTO seen VALUES (?,?,?,?)", (key, now, "blocked", inc.sender_addr))
            self.last_outcome = "blocked"
            return []
        if c["email"].get("require_auth_pass", True) and not inc.auth_ok:
            log.warning("Ignored email claiming to be from %s - it failed SPF/DKIM checks", inc.sender_addr)
            self.svc.db.execute("INSERT INTO seen VALUES (?,?,?,?)", (key, now, "spoofed", inc.sender_addr))
            self.last_outcome = "spoofed"
            return []
        ids: list[int] = []
        if role == "friend":
            card = message_card(inc, friend, c)
            hold = bool(c["messages"].get("hold_for_button", True)) and not friend.get("bypass_queue", False)
            dropped = False
            if card is not None and hold and self.svc.held_count() >= int(c["messages"].get("max_held", 50)):
                log.warning("Message from %s dropped - %d messages are already waiting for the button",
                            friend.get("name"), self.svc.held_count())
                card, dropped = None, True
            if card is not None:
                ids = self.svc.submit(card, 1, hold=hold)
            outcome = "held" if (card is not None and hold) else "ok"
            self.svc.db.execute("INSERT INTO seen VALUES (?,?,?,?)", (key, now, outcome, friend.get("name", "")))
            self.status["processed"] += 1
            self.last_outcome = "dropped" if dropped else "empty" if card is None else "held" if hold else "printed"
            return ids
        t1 = time.perf_counter()
        cards, copies = build_cards(inc, c)
        t2 = time.perf_counter()
        for card in cards:
            ids += self.svc.submit(card, copies)
        log.info("Email '%s' -> %d slip(s) queued in %.1fs (reading %.1fs, building %.1fs, drawing %.1fs)", (inc.subject or "")[:30], len(cards), time.perf_counter() - t0,
                 t1 - t0, t2 - t1, time.perf_counter() - t2)
        self.status["last_seconds"] = round(time.perf_counter() - t0, 1)
        self.svc.db.execute("INSERT INTO seen VALUES (?,?,?,?)", (key, now, "ok", f"{len(cards)} cards"))
        self.status["processed"] += 1
        self.last_outcome = "printed" if cards else "empty"
        if not cards:
            log.info("Email from %s produced nothing to print", inc.sender_addr)
        return ids
