"""Backing up (and restoring) your settings, in case the Pi's SD card dies.

WHAT IS IN A BACKUP
  One JSON file holding your whole configuration: the email connection, friends, birthdays, templates, symbols, calendars, the AI and
  printer settings ...  Not the print history or the saved pictures (those are only the past).  By default it includes your passwords
  (the printer mailbox's app password, the Google Form token, the web page login, the print API token) so a restore brings everything
  back with nothing to retype.  Untick "include passwords" and those are left out; on restore the Pi then keeps the passwords it
  already has.  Treat a backup with passwords like a password: keep it somewhere private.

WHERE COPIES GO
  * Download: any time, from the Backup tab.
  * On the Pi: the last few copies in data/backups/ (a bad edit can be undone; a dead SD card cannot).
  * By email (optional): a copy emailed to you from the printer's own mailbox when something has changed.

RESTORING
  Upload the file on the Backup tab of a fresh install.  (The raw data/config.json copied off a dead SD card works too.)  The Pi first saves what it had (data/backups/before-restore-*.json), then
  applies the backup.  Its web port is never changed by a restore (so you can't lock yourself out).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import smtplib
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("backup")

FORMAT = "jack-in-a-box-backup"
VERSION = 1
MAX_BYTES = 5_000_000
SECRETS = (("email", "password"), ("sheet", "token"), ("integrations", "api_token"), ("integrations", "webhook_url"))
WEB_SECRETS = ("password_hash", "secret_key")


class BackupError(ValueError):
    """The file isn't a usable backup; the message is for the person."""


def _get(d: dict, path: tuple):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _set(d: dict, path: tuple, value) -> None:
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def make_backup(config: dict, include_secrets: bool = True, now: datetime | None = None) -> dict:
    """The backup document for ``config`` (the Pi's whole configuration)."""
    cfg = copy.deepcopy(config)
    if not include_secrets:
        for path in SECRETS:
            if _get(cfg, path):
                _set(cfg, path, "")
        for k in WEB_SECRETS:
            cfg.get("web", {}).pop(k, None)
    return {"format": FORMAT, "version": VERSION, "created": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "includes_passwords": bool(include_secrets), "config": cfg}


def dumps(doc: dict) -> bytes:
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")


def file_name(now: datetime | None = None) -> str:
    return f"jack-in-a-box-backup-{(now or datetime.now()):%Y-%m-%d}.json"


def config_hash(config: dict) -> str:
    """Changes when a setting changes (the throw-away bits - when we last emailed, a generated secret key - don't count)."""
    c = copy.deepcopy(config)
    c.pop("backup", None)
    c.get("web", {}).pop("secret_key", None)
    return hashlib.sha256(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def validate(payload) -> dict:
    """The ``config`` inside an uploaded backup, or BackupError."""
    if isinstance(payload, dict) and "format" not in payload and sum(isinstance(payload.get(k), dict) for k in ("email", "printer", "templates", "general", "web", "calendar")) >= 3:
        return payload                                                    # the raw data/config.json rescued from an old SD card
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise BackupError("That isn't a Jack in a Box backup file (it should be the .json file from the Backup tab).")
    if int(payload.get("version", 0) or 0) > VERSION:
        raise BackupError("That backup was made by a newer version of Jack in a Box. Update this Pi first (Account tab), then restore it.")
    cfg = payload.get("config")
    if not isinstance(cfg, dict) or not any(isinstance(cfg.get(k), dict) for k in ("email", "printer", "templates", "general")):
        raise BackupError("That backup file has no settings in it.")
    return cfg


def summary(cfg: dict) -> str:
    bits = []
    for label, n in (("friend", len(cfg.get("friends", []) or [])), ("birthday", len((cfg.get("birthdays") or {}).get("people", []) or [])),
                     ("calendar", len(cfg.get("calendars", []) or [])), ("template", len(cfg.get("templates", {}) or {}))):
        if n:
            bits.append(f"{n} {label}{'s' if n != 1 else ''}")
    return ", ".join(bits)


def restore(store, payload, backup_dir: Path | None = None) -> dict:
    """Apply an uploaded backup to ``store`` (a ConfigStore).  Returns {"summary", "login_changed", "kept"}.  The current settings are saved first."""
    new = copy.deepcopy(validate(payload))
    old = store.data
    if backup_dir is not None:
        write_snapshot(old, backup_dir, "before-restore")
    kept = []
    for path in SECRETS:                                                  # a backup made without passwords keeps the ones this Pi has
        if not _get(new, path) and _get(old, path):
            _set(new, path, _get(old, path))
            kept.append(".".join(path))
    web_old = old.get("web", {})
    web_new = new.get("web") if isinstance(new.get("web"), dict) else {}
    login_changed = bool(web_new.get("password_hash")) and web_new.get("password_hash") != web_old.get("password_hash")
    merged_web = dict(web_old)
    if web_new.get("password_hash"):
        merged_web["password_hash"] = web_new["password_hash"]
        if web_new.get("secret_key"):
            merged_web["secret_key"] = web_new["secret_key"]
    new["web"] = merged_web                                                # the port (and a login the backup doesn't carry) stay as this Pi has them
    new["setup_complete"] = bool(old.get("setup_complete") or new.get("setup_complete") or merged_web.get("password_hash"))
    if isinstance(new.get("birthdays"), dict):
        from .birthdays import clean_people
        new["birthdays"]["people"] = clean_people(new["birthdays"].get("people"))
    store.replace(new)
    return {"summary": summary(store.data), "login_changed": login_changed, "kept": kept}


# ---------------------------------------------------------------------------- copies kept on the Pi
def list_snapshots(backup_dir: Path) -> list[dict]:
    out = []
    for f in sorted(Path(backup_dir).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        out.append({"name": f.name, "size": f.stat().st_size, "time": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d %b %Y %H:%M")})
    return out


def write_snapshot(config: dict, backup_dir: Path, prefix: str = "config") -> Path:
    d = Path(backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_bytes(dumps(make_backup(config, True)))
    try:
        path.chmod(0o600)                                                  # it holds passwords
    except OSError:
        pass
    return path


def snapshot(config: dict, backup_dir: Path, keep: int = 7) -> Path | None:
    """Save a copy unless the newest one is already the same settings; keep only the newest ``keep`` (before-restore copies are never pruned)."""
    d = Path(backup_dir)
    h = config_hash(config)
    for f in sorted(d.glob("config-*.json"), reverse=True)[:1]:
        try:
            if config_hash(json.loads(f.read_text("utf-8"))["config"]) == h:
                return None
        except (OSError, ValueError, KeyError):
            pass
    path = write_snapshot(config, d, "config")
    for old in sorted(d.glob("config-*.json"), reverse=True)[max(1, int(keep)):]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def read_snapshot(backup_dir: Path, name: str) -> dict:
    """A saved copy by file name (only plain names inside the folder)."""
    d = Path(backup_dir)
    if not name or "/" in name or "\\" in name or not name.endswith(".json") or name.startswith("."):
        raise BackupError("No such saved copy.")
    f = d / name
    if not f.is_file():
        raise BackupError("No such saved copy.")
    try:
        return json.loads(f.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise BackupError("That saved copy can't be read.") from e


# ---------------------------------------------------------------------------- the emailed copy
def smtp_host(imap_host: str) -> str:
    h = (imap_host or "imap.gmail.com").strip().lower()
    return "smtp" + h[4:] if h.startswith("imap") else "smtp." + h


def email_to(config: dict) -> str:
    b = config.get("backup", {})
    to = (b.get("email_to") or "").strip()
    if not to:
        senders = [str(x).strip() for x in config.get("email", {}).get("allowed_senders", []) if "@" in str(x)]
        to = senders[0] if senders else ""
    return to


def send_email_backup(config: dict, smtp_factory=None, now: datetime | None = None) -> str:
    """Email the settings to yourself from the printer's mailbox.  Returns the address; raises BackupError with something readable."""
    e = config.get("email", {})
    to = email_to(config)
    if not (e.get("username") and e.get("password")):
        raise BackupError("The printer's mailbox isn't set up yet (Email tab), so there is nothing to send from.")
    if "@" not in to:
        raise BackupError("No address to send the backup to: add yours under “Your addresses (admin)” on the Email tab, or type one in the Backup tab.")
    doc = make_backup(config, bool(config.get("backup", {}).get("email_secrets", True)), now)
    msg = EmailMessage()
    msg["From"], msg["To"] = e["username"], to
    msg["Subject"] = f"Jack in a Box settings backup {(now or datetime.now()):%d %b %Y}"
    msg.set_content("The attached file is a backup of the printer's settings.\n\n"
                    + ("It includes your passwords: keep this email private.\n\n" if doc["includes_passwords"] else "It does not include passwords.\n\n")
                    + "To restore: open the printer's web page, go to the Backup tab and choose this file.\n")
    msg.add_attachment(dumps(doc), maintype="application", subtype="json", filename=file_name(now))
    try:
        with (smtp_factory or smtplib.SMTP_SSL)(smtp_host(e.get("host", "")), 465, timeout=30) as s:
            s.login(e["username"], e["password"])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as ex:
        raise BackupError("The mailbox refused the login for sending (check the app password on the Email tab).") from ex
    except (OSError, smtplib.SMTPException) as ex:
        raise BackupError(f"Couldn't send the email ({type(ex).__name__}: {ex}).") from ex
    return to


class BackupService(threading.Thread):
    """Keeps the on-Pi copies up to date and sends the emailed copy when it is due."""

    def __init__(self, cfg, svc):
        super().__init__(name="backup", daemon=True)
        self.cfg, self.svc = cfg, svc
        self.kick = threading.Event()
        self.status = {"error": "", "last_local": "", "last_email_error": ""}

    @property
    def dir(self) -> Path:
        return self.svc.data_dir / "backups"

    def run(self):
        while not self.svc.stop.is_set():
            try:
                self.check()
            except Exception:
                log.exception("Backup check failed")
            self.kick.wait(300)
            self.kick.clear()

    def check(self, now: datetime | None = None, smtp_factory=None) -> None:
        c = self.cfg.data
        b = c["backup"]
        now = now or datetime.now(timezone.utc)
        if b.get("local", True):
            try:
                p = snapshot(c, self.dir, int(b.get("keep", 7)))
                if p:
                    self.status["last_local"] = datetime.now().strftime("%d %b %H:%M")
                self.status["error"] = ""
            except OSError as e:
                self.status["error"] = f"Couldn't save a copy on the Pi: {e}"
        if b.get("email_enabled"):
            h = config_hash(c)
            due = True
            if b.get("last_email"):
                try:
                    due = (now - datetime.fromisoformat(b["last_email"])).total_seconds() >= max(1, int(b.get("email_every_days", 7))) * 86400
                except ValueError:
                    due = True
            if (due and h != b.get("last_email_hash")) or (not b.get("last_email") and h != b.get("last_email_hash")):
                try:
                    to = send_email_backup(c, smtp_factory, now)
                    d = self.cfg.data
                    d["backup"]["last_email"], d["backup"]["last_email_hash"] = now.isoformat(timespec="seconds"), h
                    self.cfg.replace(d)
                    self.status["last_email_error"] = ""
                    log.info("Emailed a settings backup to %s", to)
                except BackupError as e:
                    self.status["last_email_error"] = str(e)
                    log.warning("Settings backup email failed: %s", e)
