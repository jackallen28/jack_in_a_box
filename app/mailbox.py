"""IMAP poller: fetch new mail from the printer's own Gmail and queue prints."""
from __future__ import annotations

import hashlib
import imaplib
import logging
import threading
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
        self._fail_logged = False

    def run(self):
        while not self.svc.stop.is_set():
            e = self.cfg.data["email"]
            wait = max(10, int(e.get("poll_seconds", 30)))
            if e.get("enabled") and e.get("username") and e.get("password"):
                try:
                    self.poll_once()
                    self.status.update(state="ok", error="")
                    self._fail_logged = False
                except Exception as ex:
                    msg = f"{type(ex).__name__}: {ex}"
                    self.status.update(state="error", error=msg)
                    if not self._fail_logged:
                        log.error("Mailbox check failed: %s", msg)
                        self._fail_logged = True
                    wait = min(300, wait * 2)
            else:
                self.status["state"] = "disabled"
            self.kick.wait(wait)
            self.kick.clear()

    def connect(self) -> imaplib.IMAP4:
        e = self.cfg.data["email"]
        imap = imaplib.IMAP4_SSL(e["host"], int(e.get("port", 993)), timeout=30)
        imap.login(e["username"], e["password"])
        return imap

    def poll_once(self) -> int:
        e = self.cfg.data["email"]
        imap = self.connect()
        done = 0
        try:
            typ, _ = imap.select(e.get("folder") or "INBOX")
            if typ != "OK":
                raise RuntimeError(f"Cannot open folder {e.get('folder')!r}")
            typ, data = imap.uid("SEARCH", None, "UNSEEN")
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            for uid in uids[:25]:
                typ, msgdata = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in msgdata if isinstance(p, tuple)), b"") if typ == "OK" else b""
                if raw and len(raw) <= MAX_BYTES:
                    try:
                        self.handle(raw)
                    except Exception:
                        log.exception("Failed to process an email")
                elif raw:
                    log.warning("Ignored an email larger than %d MB", MAX_BYTES // 1048576)
                self._finish(imap, uid)
                done += 1
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        self.status["last_check"] = datetime.now().strftime("%d %b %H:%M:%S")
        return done

    def _finish(self, imap: imaplib.IMAP4, uid: bytes) -> None:
        e = self.cfg.data["email"]
        imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        if e.get("after") == "move" and e.get("move_folder"):
            folder = e["move_folder"]
            imap.create(folder)  # harmless if it already exists
            if imap.uid("COPY", uid, f'"{folder}"')[0] == "OK":
                imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                imap.expunge()

    def handle(self, raw: bytes) -> list[int]:
        """Process one raw email.  Also used by tests and the 'send test' button."""
        c = self.cfg.data
        inc = parse(raw, min_image_px=int(c["email"].get("min_image_px", 150)),
                    max_images=int(c["email"].get("max_images", 3)))
        key = inc.message_id or hashlib.sha1(raw).hexdigest()
        if self.svc.db.one("SELECT 1 FROM seen WHERE key=?", (key,)):
            return []
        now = datetime.now().isoformat(timespec="seconds")
        role, friend = classify_sender(inc.sender_addr, c)
        if role is None:
            log.warning("Ignored email from %s - not on your admin list or friends list", inc.sender_addr or "unknown sender")
            self.svc.db.execute("INSERT INTO seen VALUES (?,?,?,?)", (key, now, "blocked", inc.sender_addr))
            return []
        if c["email"].get("require_auth_pass", True) and not inc.auth_ok:
            log.warning("Ignored email claiming to be from %s - it failed SPF/DKIM checks", inc.sender_addr)
            self.svc.db.execute("INSERT INTO seen VALUES (?,?,?,?)", (key, now, "spoofed", inc.sender_addr))
            return []
        ids: list[int] = []
        if role == "friend":
            card = message_card(inc, friend, c)
            hold = bool(c["messages"].get("hold_for_button", True)) and not friend.get("bypass_queue", False)
            if card is not None and hold and self.svc.held_count() >= int(c["messages"].get("max_held", 50)):
                log.warning("Message from %s dropped - %d messages are already waiting for the button",
                            friend.get("name"), self.svc.held_count())
                card = None
            if card is not None:
                ids = self.svc.submit(card, 1, hold=hold)
            outcome = "held" if (card is not None and hold) else "ok"
            self.svc.db.execute("INSERT INTO seen VALUES (?,?,?,?)", (key, now, outcome, friend.get("name", "")))
            self.status["processed"] += 1
            return ids
        cards, copies = build_cards(inc, c)
        for card in cards:
            ids += self.svc.submit(card, copies)
        self.svc.db.execute("INSERT INTO seen VALUES (?,?,?,?)", (key, now, "ok", f"{len(cards)} cards"))
        self.status["processed"] += 1
        if not cards:
            log.info("Email from %s produced nothing to print", inc.sender_addr)
        return ids
