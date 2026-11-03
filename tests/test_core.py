import io
import json
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import calsync, printer  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.dates import extract_deadline, fmt_when  # noqa: E402
from app.mailbox import MailPoller  # noqa: E402
from app.pipeline import build_cards, sender_allowed  # noqa: E402
from app.mailparse import parse  # noqa: E402
from app.service import Service  # noqa: E402
from app.textproc import build_items, sort_items  # noqa: E402

TZ = timezone(timedelta(hours=11))
REF = datetime(2026, 11, 3, 10, 0, tzinfo=TZ)  # a Tuesday


@pytest.fixture()
def env(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["email"]["allowed_senders"] = ["me@example.com", "@work.com"]
    d["general"]["timezone"] = "Australia/Sydney"
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    return cfg, svc, MailPoller(cfg, svc)


def mk(subject="", body="", sender="Me <me@example.com>", images=(), mid="<1@x>"):
    m = EmailMessage()
    m["From"], m["To"], m["Message-ID"] = sender, "printer@gmail.com", mid
    m["Date"] = "Tue, 3 Nov 2026 10:00:00 +1100"
    if subject:
        m["Subject"] = subject
    m.set_content(body)
    for im in images:
        buf = io.BytesIO()
        im.save(buf, "PNG")
        m.add_attachment(buf.getvalue(), maintype="image", subtype="png", filename="p.png")
    return m.as_bytes()


# ---------------------------------------------------------------- dates
@pytest.mark.parametrize("text,clean,shown", [
    ("walk the dog at 5pm tomorrow", "walk the dog", "Tomorrow, 5:00 PM"),
    ("submit report by Friday", "submit report", "Friday 6 Nov"),
    ("pay rent on 14/11", "pay rent", "Sat 14 Nov"),
    ("send invoice tomorrow morning", "send invoice", "Tomorrow, 9:00 AM"),
    ("call bob at 5", "call bob", "Today, 5:00 PM"),
    ("due next Friday: file taxes", "file taxes", "Fri 13 Nov"),
    ("finish slides by EOD", "finish slides", "Today, 5:00 PM"),
])
def test_deadlines(text, clean, shown):
    c, d = extract_deadline(text, REF)
    assert c == clean
    assert fmt_when(d.when, d.has_time, REF) == shown


@pytest.mark.parametrize("text", ["buy 3-5 apples", "do the morning routine", "lunch at 5 Smith Street",
                                  "sat on the couch", "I may 5 times"])
def test_no_false_deadlines(text):
    c, d = extract_deadline(text, REF)
    assert d is None and c == text


def test_us_date_order():
    _, d = extract_deadline("pay rent on 11/14", REF, "mdy")
    assert d.when.month == 11 and d.when.day == 14


# ---------------------------------------------------------------- text
def items(text, env_cfg):
    return sort_items(build_items(text, REF, env_cfg.data), env_cfg.data["text"]["sort"])


def test_splits_three_actions(env):
    out = items("wash dishes, walk the dog and reply to work email", env[0])
    assert [i.text for i in out] == ["Wash dishes", "Walk the dog", "Reply to work email"]


def test_does_not_split_shopping_list(env):
    assert [i.text for i in items("buy milk, eggs and bread", env[0])] == ["Buy milk, eggs and bread"]
    assert len(items("clean and tidy the house", env[0])) == 1


def test_typos_then_split_and_sort(env):
    out = items("wahs the dishes tomorow, walk teh dog at 5pm and relpy to work emial by friday", env[0])
    assert [i.text for i in out] == ["Walk the dog", "Wash the dishes", "Reply to work email"]  # soonest first


def test_blank_lines_separate_and_multiline_stays_together(env):
    out = items("Dentist friday 3pm\nbring insurance card\n\nCall mum tonight", env[0])
    assert len(out) == 2
    assert any("\n" in i.text for i in out)


def test_checklist_is_one_reminder(env):
    out = items("Shopping:\n- milk\n- eggs", env[0])
    assert len(out) == 1 and out[0].checklist == ["Milk", "Eggs"]


def test_urgent_sorts_first(env):
    out = items("water plants tomorrow, call the bank urgent", env[0])
    assert out[0].priority and "bank" in out[0].text.lower()


def test_grammar(env):
    assert items("pick up a apple from the the shop", env[0])[0].text == "Pick up an apple from the shop"
    assert "I" in items("i need to email Sam about the thing. i think its late", env[0])[0].text


# ---------------------------------------------------------------- email -> cards
def test_subject_is_heading_and_each_task_gets_one(env):
    cfg, svc, poller = env
    ids = poller.handle(mk("Home stuff", "wash dishes, walk the dog and reply to work email"))
    assert len(ids) == 3
    rows = svc.db.all("SELECT title FROM jobs")
    assert all(r["title"] == "Home stuff" for r in rows)


def test_no_subject_omits_heading(env):
    cfg, _, _ = env
    inc = parse(mk("", "walk the dog"))
    cards, _ = build_cards(inc, cfg.data)
    assert cards[0].heading == ""
    inc = parse(mk("(no subject)", "walk the dog"))
    assert build_cards(inc, cfg.data)[0][0].heading == ""


def test_subject_only_becomes_the_task(env):
    cfg, _, _ = env
    cards, _ = build_cards(parse(mk("wash dishes and walk the dog", "")), cfg.data)
    assert len(cards) == 2 and all(c.heading == "" for c in cards)


def test_images_make_a_single_reminder(env):
    cfg, svc, poller = env
    pic = Image.new("RGB", (400, 300), (120, 60, 200))
    ids = poller.handle(mk("Photo", "wash dishes, walk the dog and call mum", images=[pic]))
    assert len(ids) == 1
    cards, _ = build_cards(parse(mk("Photo", "wash dishes, walk the dog", images=[pic], mid="<2@x>")), cfg.data)
    assert len(cards) == 1 and len(cards[0].images) == 1
    assert "Wash dishes" in cards[0].sections[0].text and "walk the dog" in cards[0].sections[0].text.lower()


def test_tiny_images_ignored(env):
    cfg, _, _ = env
    logo = Image.new("RGB", (60, 40), "red")
    assert parse(mk("x", "hi there", images=[logo])).images == []


FORWARD = """reply to Dave by friday
check invoice 4471

---------- Forwarded message ---------
From: Dave Smith <dave@supplier.com>
Date: Mon, 3 Nov 2026 at 08:12
Subject: Q4 supplier invoice dispute
To: Jack <jack@example.com>

Hi Jack,

I am writing about invoice 4471 which we sent on 14 October for $12,480. Our records show the payment has not been received. Could you please confirm whether it was processed? We also noticed invoice 4402 was paid twice, so there is a credit of $3,200 on your account. Please let me know by Friday so we can close the quarter. Our bank details change from 1 December.

Kind regards,
Dave
"""


def test_forward_summary_and_actions(env):
    cfg, _, _ = env
    inc = parse(mk("Fwd: Q4 supplier invoice dispute", FORWARD))
    assert inc.forwarded is not None and inc.subject == "Q4 supplier invoice dispute"
    (card,), _ = build_cards(inc, cfg.data)
    assert card.kind == "email" and card.heading == "Q4 supplier invoice dispute"
    summary = next(s for s in card.sections if s.key == "summary").text
    assert 3 <= summary.count(".") + summary.count("?") <= 5 and "4471" in summary
    actions = next(s for s in card.sections if s.key == "actions").items
    assert actions == ["Reply to Dave by Friday", "Check invoice 4471"]
    assert card.subline == "From Dave Smith"


def test_sender_allowlist_and_dedupe(env):
    cfg, svc, poller = env
    assert sender_allowed("boss@work.com", cfg.data) and not sender_allowed("evil@spam.com", cfg.data)
    assert poller.handle(mk("x", "walk dog", sender="evil@spam.com", mid="<s@x>")) == []
    assert poller.handle(mk("x", "walk dog", mid="<d@x>"))
    assert poller.handle(mk("x", "walk dog", mid="<d@x>")) == []   # same message twice


def test_failed_spf_is_rejected(env):
    cfg, svc, poller = env
    raw = mk("x", "walk dog", mid="<f@x>").replace(b"Subject:", b"Authentication-Results: mx.google.com; spf=fail; dkim=fail\r\nSubject:", 1)
    assert poller.handle(raw) == []


def test_rules(env):
    cfg, svc, poller = env
    d = cfg.data
    d["rules"] = [{"name": "boss", "match": "all", "conditions": [{"field": "sender", "op": "contains", "value": "work.com"}],
                   "actions": {"heading_prefix": "[WORK]", "copies": 2, "urgent": True}}]
    cfg.replace(d)
    cards, copies = build_cards(parse(mk("Report", "send report", sender="b@work.com")), cfg.data)
    assert copies == 2 and cards[0].heading == "[WORK] Report" and cards[0].priority


# ---------------------------------------------------------------- printing
def test_escpos_bytes():
    img = Image.new("1", (576, 300), 1)
    data = printer.encode(img, "partial", 3)
    assert data.startswith(b"\x1b@\x1d\x76\x30\x00\x48\x00\x00\x01")      # 72 bytes wide, 256 rows
    assert data.count(b"\x1d\x76\x30\x00") == 2                         # 300 rows = 256 + 44
    assert data.endswith(b"\x1d\x56\x42\x18")


def test_job_prints_to_dummy_and_history(env, tmp_path, monkeypatch):
    monkeypatch.setattr(printer, "DATA_DIR", tmp_path)
    cfg, svc, poller = env
    poller.handle(mk("Hi", "walk the dog"))
    svc.process_pending()
    assert svc.db.one("SELECT status FROM jobs")["status"] == "printed"
    assert len(list((tmp_path / "dummy").glob("*.png"))) == 1
    assert (tmp_path / "history" / "1.png").exists()


def test_printer_offline_retries_in_order(env):
    cfg, svc, poller = env
    d = cfg.data
    d["printer"].update(backend="file", device="/nonexistent/lp0")
    cfg.replace(d)
    poller.handle(mk("Hi", "walk the dog and call mum"))
    svc.process_pending()
    rows = svc.db.all("SELECT status, attempts FROM jobs ORDER BY id")
    assert [r["status"] for r in rows] == ["pending", "pending"] and rows[0]["attempts"] == 1 and rows[1]["attempts"] == 0
    assert "not found" in svc.printer_error


# ---------------------------------------------------------------- calendar timing
def cfg_cal(**kw):
    return {"calendar": {"print_time": "07:00", "late_policy": "print_now", "also_day_of": False, **kw}}


SYD = None


def test_calendar_due_times():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Australia/Sydney")
    start = datetime(2026, 11, 10, 15, 0, tzinfo=tz)       # event Tue 10 Nov 3pm
    t = lambda *a: datetime(*a, tzinfo=tz)
    assert calsync.compute_due(t(2026, 11, 1, 12), start, cfg_cal()) == [t(2026, 11, 9, 7, 0)]   # known early: day before
    assert calsync.compute_due(t(2026, 11, 9, 6, 59), start, cfg_cal()) == [t(2026, 11, 9, 7, 0)]
    assert calsync.compute_due(t(2026, 11, 9, 7, 1), start, cfg_cal()) == [t(2026, 11, 10, 7, 0)]   # added after cutoff: morning of
    late = t(2026, 11, 10, 9, 0)
    assert calsync.compute_due(late, start, cfg_cal()) == [late]                                  # added at 9am: print now
    assert calsync.compute_due(late, start, cfg_cal(late_policy="skip")) == [None]
    assert calsync.compute_due(t(2026, 11, 10, 16, 0), start, cfg_cal()) == [None]                # already started
    assert calsync.compute_due(t(2026, 11, 1), start, cfg_cal(also_day_of=True)) == [t(2026, 11, 9, 7), t(2026, 11, 10, 7)]
    assert calsync.compute_due(t(2026, 11, 1), start, cfg_cal(print_time="06:30"))[0] == t(2026, 11, 9, 6, 30)


ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:evt1@test
DTSTART;TZID=Australia/Sydney:20261110T150000
DTEND;TZID=Australia/Sydney:20261110T160000
SUMMARY:Planning meeting
LOCATION:Level 4\\, 120 Collins St
DESCRIPTION:Bring the budget
END:VEVENT
BEGIN:VEVENT
UID:weekly@test
DTSTART:20261104T230000Z
DTEND:20261105T000000Z
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Weekly standup
END:VEVENT
BEGIN:VEVENT
UID:allday@test
DTSTART;VALUE=DATE:20261111
DTEND;VALUE=DATE:20261112
SUMMARY:Birthday
END:VEVENT
END:VCALENDAR
"""


def test_ics_parse_recurrence_and_cards(env):
    from zoneinfo import ZoneInfo
    cfg, svc, _ = env
    tz = ZoneInfo("Australia/Sydney")
    now = datetime(2026, 11, 3, 9, 0, tzinfo=tz)
    evs = calsync.parse_events(ICS, now, now + timedelta(days=30), tz)
    names = sorted(e["summary"] for e in evs)
    assert names == ["Birthday", "Planning meeting", "Weekly standup", "Weekly standup", "Weekly standup"]
    plan = next(e for e in evs if e["summary"] == "Planning meeting")
    card = calsync.card_for_event(plan, cfg.data, now)
    assert card.vars["day"] == "TUESDAY" and card.sections[0].text == "Tue 10 Nov, 3:00 PM - 4:00 PM"
    assert card.sections[1].text == "Level 4, 120 Collins St"
    bday = next(e for e in evs if e["summary"] == "Birthday")
    assert calsync.card_for_event(bday, cfg.data, now).sections[0].text == "Wed 11 Nov (all day)"


def test_calendar_end_to_end(env, monkeypatch):
    from zoneinfo import ZoneInfo
    cfg, svc, _ = env
    d = cfg.data
    d["calendars"] = [{"id": "c1", "name": "Work", "url": "https://example.com/x.ics", "enabled": True, "template": "calendar"}]
    cfg.replace(d)
    monkeypatch.setattr(calsync, "fetch_ics", lambda url: ICS)
    sync = calsync.CalendarSync(cfg, svc)
    fake_now = [datetime(2026, 11, 3, 9, 0, tzinfo=ZoneInfo("Australia/Sydney"))]
    monkeypatch.setattr(calsync, "local_now", lambda c: fake_now[0])
    sync.sync()
    sync.tick()
    assert svc.db.one("SELECT COUNT(*) n FROM jobs")["n"] == 0                  # nothing due yet (Nov 3 9am)
    fake_now[0] = datetime(2026, 11, 9, 7, 5, tzinfo=ZoneInfo("Australia/Sydney"))
    sync.tick()
    titles = [r["title"] for r in svc.db.all("SELECT title FROM jobs")]
    assert "Planning meeting" in titles                                          # printed at 7am the day before
    sync.tick()
    assert [r["title"] for r in svc.db.all("SELECT title FROM jobs")].count("Planning meeting") == 1   # only once


# ---------------------------------------------------------------- IMAP (fake server, real imaplib response shapes)
class FakeIMAP:
    """Mimics the parts of imaplib.IMAP4_SSL the poller uses."""
    instances = []

    def __init__(self, messages):
        self.messages = dict(messages)           # uid(bytes) -> raw bytes
        self.flags = {}
        self.logged_out = False
        self.created = []
        FakeIMAP.instances.append(self)

    def login(self, u, p):
        assert (u, p) == ("printer@gmail.com", "app-pass")
        return "OK", [b"ok"]

    def select(self, folder, readonly=False):
        return "OK", [b"3"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b" ".join(self.messages)]
        if cmd == "FETCH":
            uid = args[0]
            return "OK", [(b"1 (UID %s BODY[] {%d}" % (uid, len(self.messages[uid])), self.messages[uid]), b")"]
        if cmd == "STORE":
            self.flags.setdefault(args[0], []).append(args[2])
            return "OK", [b""]
        raise AssertionError(cmd)

    def create(self, f):
        self.created.append(f)

    def logout(self):
        self.logged_out = True


def test_poll_once_end_to_end(env, monkeypatch):
    import app.mailbox as mailbox
    cfg, svc, poller = env
    d = cfg.data
    d["email"].update(username="printer@gmail.com", password="app-pass", enabled=True)
    cfg.replace(d)
    msgs = {b"11": mk("Chores", "wash dishes and walk the dog", mid="<m1@x>"),
            b"12": mk("Spam", "buy pills", sender="evil@spam.com", mid="<m2@x>")}
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", lambda *a, **k: FakeIMAP(msgs))
    assert poller.poll_once() == 2
    fake = FakeIMAP.instances[-1]
    assert fake.logged_out and fake.flags[b"11"] == ["(\\Seen)"] and fake.flags[b"12"] == ["(\\Seen)"]
    jobs = svc.db.all("SELECT title FROM jobs")
    assert len(jobs) == 2 and all(j["title"] == "Chores" for j in jobs)     # spam sender ignored, 2 tasks printed
    assert svc.db.one("SELECT status FROM seen WHERE info='evil@spam.com'")["status"] == "blocked"
    assert poller.poll_once() == 2 and svc.db.one("SELECT COUNT(*) n FROM jobs")["n"] == 2   # re-seen mail not reprinted


def test_empty_email_prints_nothing_but_image_only_does(env):
    cfg, svc, poller = env
    assert build_cards(parse(mk("", "")), cfg.data)[0] == []
    assert poller.handle(mk("", "", mid="<e@x>")) == []
    pic = Image.new("RGB", (400, 300), (90, 90, 200))
    cards, _ = build_cards(parse(mk("", "", images=[pic], mid="<i@x>")), cfg.data)
    assert len(cards) == 1 and len(cards[0].images) == 1 and cards[0].heading == ""


# ---------------------------------------------------------------- printer connection choice
def test_auto_backend_prefers_usb_then_network_then_pyusb(monkeypatch):
    monkeypatch.setattr(printer.glob, "glob", lambda pat: ["/dev/usb/lp1"])
    assert printer.resolve_backend({"backend": "auto", "host": "1.2.3.4"}) == ("file", "/dev/usb/lp1")
    monkeypatch.setattr(printer.glob, "glob", lambda pat: [])
    assert printer.resolve_backend({"backend": "auto", "host": "192.168.192.168"})[0] == "network"
    assert printer.resolve_backend({"backend": "auto", "host": ""})[0] == "usb"
    assert printer.resolve_backend({"backend": "network"})[0] == "network"     # explicit choice wins


def test_network_printer_receives_the_bytes_and_check_reports_reachability():
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = []

    def serve():                              # a printer accepts many connections
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                while chunk := conn.recv(65536):
                    got.append(chunk)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    cfg = {"backend": "network", "host": "127.0.0.1", "port": srv.getsockname()[1], "cut": "partial", "feed_lines": 3}
    assert printer.check(cfg)[0] is True
    printer.print_image(Image.new("1", (576, 100), 1), cfg)
    import time
    for _ in range(40):
        if b"".join(got).endswith(b"\x18"):
            break
        time.sleep(0.05)
    data = b"".join(got)
    assert data.startswith(b"\x1b@\x1d\x76\x30") and data.endswith(b"\x1d\x56\x42\x18")
    srv.close()
    ok, msg = printer.check({"backend": "network", "host": "127.0.0.1", "port": 1})
    assert not ok and "Can't reach" in msg


def test_check_reports_missing_usb_device():
    ok, msg = printer.check({"backend": "file", "device": "/dev/usb/nope"})
    assert not ok and "not found" in msg


def test_british_and_australian_spellings_are_left_alone_but_real_typos_still_fixed(env):
    cfg, *_ = env
    text = "pump the tyres, organise the centre, pick a favourite colour, post the cheque, ring mum this arvo"
    out = items(text, cfg)
    joined = " ".join(i.text for i in out).lower()
    for ok in ("tyres", "organise", "centre", "favourite", "colour", "cheque", "mum", "arvo"):
        assert ok in joined, (ok, joined)
    assert items("wahs the dishs and relpy to emial", cfg)[0].text.lower().startswith("wash the dishes")
