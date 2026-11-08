"""Backing up and restoring the settings (SD card insurance): download, restore, copies on the Pi, the emailed copy."""
import email
import json
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import backup as bk  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.service import Service  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

HASH = generate_password_hash("hunter22")

NOW = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)


def make_store(path, **email):
    s = ConfigStore(path)
    d = s.data
    d["printer"]["backend"] = "dummy"
    d["email"].update(username="printer@gmail.com", password="app-pass-123", enabled=True, allowed_senders=["me@icloud.com"], **email)
    d["sheet"]["token"] = "form-token-xyz"
    d["integrations"]["api_token"] = "api-token-abc"
    d["web"]["password_hash"] = HASH
    d["friends"] = [{"name": "Sarah", "email": "sarah@x.com", "bypass_queue": False, "enabled": True}]
    d["birthdays"]["people"] = [{"id": "a1", "name": "Mum", "day": 3, "month": 4, "year": 1960, "note": "", "enabled": True}]
    s.replace(d)
    return s


@pytest.fixture()
def store(tmp_path):
    return make_store(tmp_path / "old" / "c.json")


# ----------------------------------------------------------------------------- the file
def test_a_backup_holds_everything_including_passwords_by_default(store):
    doc = bk.make_backup(store.data, True, NOW)
    assert doc["format"] == bk.FORMAT and doc["version"] == 1 and doc["created"] == "2026-11-05T10:00:00+00:00" and doc["includes_passwords"] is True
    c = doc["config"]
    assert (c["email"]["password"], c["sheet"]["token"], c["integrations"]["api_token"], c["web"]["password_hash"]) == ("app-pass-123", "form-token-xyz", "api-token-abc", HASH)
    assert c["friends"][0]["name"] == "Sarah" and c["birthdays"]["people"][0]["name"] == "Mum" and "reminder" in c["templates"]
    assert json.loads(bk.dumps(doc)) == doc and bk.file_name(NOW) == "jack-in-a-box-backup-2026-11-05.json"


def test_without_passwords_none_are_in_the_file_and_the_live_config_is_untouched(store):
    doc = bk.make_backup(store.data, False, NOW)
    text = bk.dumps(doc).decode()
    for secret in ("app-pass-123", "form-token-xyz", "api-token-abc", HASH, store.data["web"]["secret_key"]):
        assert secret not in text
    assert doc["includes_passwords"] is False and store.data["email"]["password"] == "app-pass-123"


def test_only_real_backups_are_accepted():
    for bad in (None, [], "x", {}, {"format": "other"}, {"format": bk.FORMAT, "version": 1}, {"format": bk.FORMAT, "version": 1, "config": {"junk": 1}},
                {"format": bk.FORMAT, "version": 99, "config": {"email": {}}}):
        with pytest.raises(bk.BackupError):
            bk.validate(bad)
    with pytest.raises(bk.BackupError, match="newer version"):
        bk.validate({"format": bk.FORMAT, "version": 99, "config": {"email": {}}})
    assert bk.validate({"format": bk.FORMAT, "version": 1, "config": {"email": {}}}) == {"email": {}}


def test_the_raw_config_file_rescued_from_a_dead_card_is_accepted(store, tmp_path):
    raw = json.loads((tmp_path / "old" / "c.json").read_text())                                     # what data/config.json looks like
    other = ConfigStore(tmp_path / "rescued" / "c.json")
    res = bk.restore(other, raw, tmp_path / "rescued" / "backups")
    assert other.data["email"]["password"] == "app-pass-123" and other.data["friends"][0]["name"] == "Sarah" and res["login_changed"] is True
    for bad in ({"email": {}}, {"hello": 1}, {"format": "other", "email": {}, "printer": {}, "templates": {}}):
        with pytest.raises(bk.BackupError):
            bk.validate(bad)


# ----------------------------------------------------------------------------- restoring onto a fresh Pi
def test_a_complete_backup_restores_everything_on_a_new_sd_card(store, tmp_path):
    doc = bk.make_backup(store.data, True, NOW)
    fresh = ConfigStore(tmp_path / "new" / "c.json")
    fresh.data["web"]["port"] = 8099                                                                  # this Pi's own port
    d = fresh.data
    d["web"]["password_hash"] = "pbkdf2:new-pi-hash"
    fresh.replace(d)
    res = bk.restore(fresh, doc, tmp_path / "new" / "backups")
    c = fresh.data
    assert (c["email"]["username"], c["email"]["password"], c["sheet"]["token"], c["integrations"]["api_token"]) == ("printer@gmail.com", "app-pass-123", "form-token-xyz", "api-token-abc")
    assert c["friends"][0]["email"] == "sarah@x.com" and c["birthdays"]["people"][0]["name"] == "Mum" and c["email"]["allowed_senders"] == ["me@icloud.com"]
    assert c["web"]["port"] == 8099 and c["web"]["password_hash"] == HASH and res["login_changed"] is True
    assert "1 friend" in res["summary"] and "1 birthday" in res["summary"]
    assert ConfigStore(tmp_path / "new" / "c.json").data["email"]["password"] == "app-pass-123"      # and it was saved to disk


def test_a_backup_made_without_passwords_keeps_the_ones_this_pi_has(store, tmp_path):
    doc = bk.make_backup(store.data, False, NOW)
    other = make_store(tmp_path / "other" / "c.json")
    d = other.data
    d["email"].update(password="this-pis-own-pass", username="old@gmail.com")
    d["friends"] = []
    other.replace(d)
    res = bk.restore(other, doc, tmp_path / "other" / "backups")
    c = other.data
    assert c["email"]["password"] == "this-pis-own-pass" and c["email"]["username"] == "printer@gmail.com"      # the settings come back, the password stays
    assert c["friends"][0]["name"] == "Sarah" and res["login_changed"] is False and "email.password" in res["kept"]
    assert c["web"]["password_hash"] == HASH                                              # the Pi's own login is untouched


def test_the_current_settings_are_saved_before_a_restore_and_old_versions_are_filled_in(store, tmp_path):
    other = make_store(tmp_path / "o" / "c.json")
    old_style = {"format": bk.FORMAT, "version": 1, "config": {"email": {"username": "x@gmail.com", "password": "p"}, "friends": [{"name": "Z", "email": "z@x.com"}]}}   # an old/small config
    bk.restore(other, old_style, tmp_path / "o" / "backups")
    saved = list((tmp_path / "o" / "backups").glob("before-restore-*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["config"]["email"]["password"] == "app-pass-123"        # what it had, passwords included
    assert other.data["email"]["username"] == "x@gmail.com" and "reminder" in other.data["templates"] and other.data["birthdays"]["enabled"] is True   # gaps filled from defaults
    assert (saved[0].stat().st_mode & 0o077) == 0                                                      # readable only by you


def test_a_restored_birthday_list_is_made_safe(store, tmp_path):
    doc = bk.make_backup(store.data, True, NOW)
    doc["config"]["birthdays"]["people"] = [{"name": "Ok", "day": 1, "month": 1}, {"name": "", "day": 1, "month": 1}, {"name": "Bad", "day": 40, "month": 1}, "junk"]
    other = make_store(tmp_path / "o2" / "c.json")
    bk.restore(other, doc, tmp_path / "o2" / "backups")
    assert [p["name"] for p in other.data["birthdays"]["people"]] == ["Ok"]


# ----------------------------------------------------------------------------- copies kept on the Pi
def test_copies_on_the_pi_are_made_only_when_something_changed_and_pruned(store, tmp_path):
    d = tmp_path / "bk"
    d.mkdir()
    for i in range(3):                                                                                # three old copies, each with different settings
        c = json.loads(json.dumps(store.data))
        c["general"]["history_keep_days"] = 10 + i
        (d / f"config-2026010{i}-000000.json").write_bytes(bk.dumps(bk.make_backup(c, True)))
    (d / "before-restore-20260101-000000.json").write_text("{}")
    new = bk.snapshot(store.data, d, keep=2)
    assert new and new.exists() and len(list(d.glob("config-*.json"))) == 2                           # the newest 2 are kept
    assert (d / "before-restore-20260101-000000.json").exists()                                       # those are never pruned
    assert bk.snapshot(store.data, d, keep=2) is None                                                 # unchanged: no new copy
    assert all(f["name"].endswith(".json") for f in bk.list_snapshots(d))


def test_a_saved_copy_can_only_be_read_by_plain_name(store, tmp_path):
    d = tmp_path / "bk"
    p = bk.write_snapshot(store.data, d)
    assert bk.read_snapshot(d, p.name)["config"]["email"]["username"] == "printer@gmail.com"
    for bad in ("../c.json", "..\\x.json", "", ".hidden.json", "nope.json", "config.txt", "/etc/passwd"):
        with pytest.raises(bk.BackupError):
            bk.read_snapshot(d, bad)
    (d / "broken.json").write_text("{not json")
    with pytest.raises(bk.BackupError):
        bk.read_snapshot(d, "broken.json")


# ----------------------------------------------------------------------------- the emailed copy
class FakeSMTP:
    sent = []
    fail = None

    def __init__(self, host, port, timeout=0):
        self.host, self.port = host, port
        FakeSMTP.hosts = getattr(FakeSMTP, "hosts", []) + [(host, port)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        if FakeSMTP.fail == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad")
        if FakeSMTP.fail == "net":
            raise OSError("network is unreachable")
        assert (u, p) == ("printer@gmail.com", "app-pass-123")

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture(autouse=True)
def reset_smtp():
    FakeSMTP.sent, FakeSMTP.fail, FakeSMTP.hosts = [], None, []


def test_the_email_goes_to_you_from_the_printers_mailbox_with_the_file_attached(store):
    to = bk.send_email_backup(store.data, FakeSMTP, NOW)
    assert to == "me@icloud.com" and FakeSMTP.hosts == [("smtp.gmail.com", 465)]
    (msg,) = FakeSMTP.sent
    assert msg["From"] == "printer@gmail.com" and msg["To"] == "me@icloud.com" and "backup" in msg["Subject"].lower()
    parsed = email.message_from_bytes(bytes(msg))
    (att,) = [p for p in parsed.walk() if p.get_filename()]
    assert att.get_filename() == "jack-in-a-box-backup-2026-11-05.json"
    doc = json.loads(att.get_payload(decode=True))
    assert bk.validate(doc)["email"]["password"] == "app-pass-123" and "keep this email private" in parsed.get_payload(0).get_payload()


def test_the_emailed_copy_can_leave_the_passwords_out_and_go_to_another_address(store):
    d = store.data
    d["backup"].update(email_secrets=False, email_to="backup@example.com")
    store.replace(d)
    assert bk.send_email_backup(store.data, FakeSMTP, NOW) == "backup@example.com"
    att = [p for p in email.message_from_bytes(bytes(FakeSMTP.sent[0])).walk() if p.get_filename()][0]
    assert b"app-pass-123" not in att.get_payload(decode=True)


def test_email_problems_are_explained(store):
    assert bk.smtp_host("imap.gmail.com") == "smtp.gmail.com" and bk.smtp_host("imap.mail.me.com") == "smtp.mail.me.com" and bk.smtp_host("mail.example.com") == "smtp.mail.example.com"
    for mode, words in (("auth", "app password"), ("net", "Couldn't send")):
        FakeSMTP.fail = mode
        with pytest.raises(bk.BackupError, match=words):
            bk.send_email_backup(store.data, FakeSMTP, NOW)
    FakeSMTP.fail = None
    d = store.data
    d["email"]["allowed_senders"] = []
    store.replace(d)
    with pytest.raises(bk.BackupError, match="No address"):
        bk.send_email_backup(store.data, FakeSMTP, NOW)
    d["email"]["password"] = ""
    with pytest.raises(bk.BackupError, match="mailbox"):
        bk.send_email_backup(d, FakeSMTP, NOW)


def test_the_service_emails_when_due_and_only_when_something_changed(store, tmp_path):
    svc = Service(store, tmp_path / "svc")
    s = bk.BackupService(store, svc)
    d = store.data
    d["backup"]["email_enabled"] = True
    store.replace(d)
    s.check(NOW, FakeSMTP)
    assert len(FakeSMTP.sent) == 1 and store.data["backup"]["last_email"].startswith("2026-11-05") and s.status["last_email_error"] == ""
    s.check(NOW + timedelta(days=30), FakeSMTP)
    assert len(FakeSMTP.sent) == 1                                                                    # nothing changed: no new email
    d = store.data
    d["general"]["history_keep_days"] = 61
    store.replace(d)
    s.check(NOW + timedelta(days=2), FakeSMTP)
    assert len(FakeSMTP.sent) == 1                                                                    # changed, but not due yet (7 days)
    s.check(NOW + timedelta(days=8), FakeSMTP)
    assert len(FakeSMTP.sent) == 2
    d = store.data
    d["backup"]["email_enabled"] = False
    d["general"]["history_keep_days"] = 62
    store.replace(d)
    s.check(NOW + timedelta(days=30), FakeSMTP)
    assert len(FakeSMTP.sent) == 2


def test_the_service_saves_copies_on_the_pi_and_reports_an_email_failure(store, tmp_path):
    svc = Service(store, tmp_path / "svc2")
    s = bk.BackupService(store, svc)
    s.check(NOW, FakeSMTP)
    assert len(list((svc.data_dir / "backups").glob("config-*.json"))) == 1 and s.status["last_local"]
    d = store.data
    d["backup"].update(email_enabled=True, local=False)
    store.replace(d)
    FakeSMTP.fail = "auth"
    s.check(NOW, FakeSMTP)
    assert "app password" in s.status["last_email_error"] and store.data["backup"]["last_email"] == ""     # will try again


# ----------------------------------------------------------------------------- the web page
@pytest.fixture()
def client(tmp_path):
    from app.backup import BackupService
    from app.birthdays import BirthdayService
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.web import create_app
    cfg = make_store(tmp_path / "c.json")
    svc = Service(cfg, tmp_path)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc), birthdays=BirthdayService(cfg, svc), backup=BackupService(cfg, svc)).test_client()
    return c, cfg, svc, lambda client: client.post("/api/login", json={"password": "hunter22"}, headers={"X-Requested-With": "fetch"})


def test_the_page_downloads_a_backup_with_or_without_passwords(client):
    c, cfg, svc, login = client
    assert c.get("/api/backup").status_code == 401
    login(c)
    r = c.get("/api/backup")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and ".json" in r.headers["Content-Disposition"] and r.mimetype == "application/json"
    assert json.loads(r.data)["config"]["email"]["password"] == "app-pass-123"
    r = c.get("/api/backup?passwords=0")
    assert b"app-pass-123" not in r.data and json.loads(r.data)["includes_passwords"] is False


def test_the_page_restores_a_backup_and_signs_you_out_if_the_login_changed(client):
    from test_web import H
    c, cfg, svc, login = client
    login(c)
    doc = json.loads(c.get("/api/backup").data)
    doc["config"]["friends"] = [{"name": "New", "email": "new@x.com", "bypass_queue": False, "enabled": True}]
    doc["config"]["web"]["password_hash"] = "pbkdf2:another"
    r = c.post("/api/backup-restore", json=doc, headers=H).get_json()
    assert r["ok"] and r["login_changed"] is True and "friend" in r["message"] and cfg.data["friends"][0]["name"] == "New"
    assert c.get("/api/backup").status_code == 401                                                    # signed out: the login is the backup's
    assert list((svc.data_dir / "backups").glob("before-restore-*.json"))


def test_the_page_rejects_bad_files_and_restores_saved_copies(client):
    from test_web import H
    c, cfg, svc, login = client
    login(c)
    assert c.post("/api/backup-restore", json={"hello": 1}, headers=H).status_code == 400
    assert "backup" in c.post("/api/backup-restore", json={"format": "x"}, headers=H).get_json()["error"]
    assert c.post("/api/backup-restore", data=b"not json", content_type="application/json", headers=H).status_code == 400
    snap = c.post("/api/backup-snapshot", headers=H).get_json()
    assert snap["ok"] and snap["files"][0]["name"] == snap["name"]
    d = cfg.data
    d["friends"] = []
    cfg.replace(d)
    r = c.post("/api/backup-restore-local", json={"name": snap["name"]}, headers=H).get_json()
    assert r["ok"] and cfg.data["friends"][0]["name"] == "Sarah"                                      # back to the saved copy
    assert c.post("/api/backup-restore-local", json={"name": "../c.json"}, headers=H).status_code == 400


def test_the_page_emails_a_copy_on_request_and_explains_when_it_cannot(client, monkeypatch):
    from test_web import H
    c, cfg, svc, login = client
    login(c)
    monkeypatch.setattr(bk.smtplib, "SMTP_SSL", FakeSMTP)
    r = c.post("/api/backup-email", headers=H).get_json()
    assert r["ok"] and "me@icloud.com" in r["message"] and len(FakeSMTP.sent) == 1
    d = cfg.data
    d["email"]["password"] = ""
    cfg.replace(d)
    assert "mailbox" in c.post("/api/backup-email", headers=H).get_json()["error"]
    st = c.get("/api/backup-status").get_json()
    assert st["email_to"] == "me@icloud.com" and st["mailbox_ready"] is False and "friend" in st["summary"]
    schema = c.get("/api/schema").get_json()["sections"]["backup"]
    assert {f["path"] for f in schema} >= {"backup.local", "backup.email_enabled", "backup.email_to"}


def test_saving_settings_does_not_forget_when_the_last_copy_was_emailed(client):
    from test_web import H
    c, cfg, svc, login = client
    login(c)
    d = cfg.data
    d["backup"].update(last_email="2026-11-01T10:00:00+00:00", last_email_hash="abc")
    cfg.replace(d)
    conf = c.get("/api/config").get_json()
    conf["backup"].update(last_email="", last_email_hash="")                                          # a stale page
    c.put("/api/config", json=conf, headers=H)
    assert cfg.data["backup"]["last_email"] == "2026-11-01T10:00:00+00:00" and cfg.data["backup"]["last_email_hash"] == "abc"
