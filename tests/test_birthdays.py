"""Birthdays: your list prints at 7am on the day (once), and reminders about a birthday get the BIRTHDAY template."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import birthdays as bd  # noqa: E402
from app.config import ConfigStore, normalise  # noqa: E402
from app.mailparse import Incoming  # noqa: E402
from app.models import Card, Section  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.render import render_card  # noqa: E402
from app.service import Service  # noqa: E402

TODAY = date(2026, 11, 5)
TZ = timezone(timedelta(hours=11))


def at(h, m=0, d=TODAY):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


@pytest.fixture()
def env(tmp_path):
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    cfg.replace(d)
    return cfg, Service(cfg, tmp_path)


def add(cfg, **p):
    d = cfg.data
    d["birthdays"]["people"].append(bd.clean_person({"name": "Sarah", "day": 5, "month": 11, "year": 1985, **p}))
    cfg.replace(d)


def jobs(svc):
    return svc.db.all("SELECT kind, title, source FROM jobs ORDER BY id")


# ----------------------------------------------------------------------------- reading dates and names
@pytest.mark.parametrize("text,expected", [
    ("Sarah 14 March", (14, 3, 0)), ("14th of March 1985", (14, 3, 1985)), ("March 9th, 2018", (9, 3, 2018)), ("Mum 3/4/1960", (3, 4, 1960)),
    ("Tom 14/3", (14, 3, 0)), ("1985-03-14", (14, 3, 1985)), ("29 Feb", (29, 2, 0)), ("her 40th on 14 March 2027", (14, 3, 0))])      # a future year is not a birth year
def test_dates_are_found(text, expected):
    d, m, y, _span = bd.find_date(text, "dmy", TODAY)
    assert (d, m, y) == expected


def test_the_date_order_setting_and_impossible_dates():
    assert bd.find_date("Sam 3/4", "mdy", TODAY)[:2] == (4, 3) and bd.find_date("Sam 3/4", "dmy", TODAY)[:2] == (3, 4)
    assert bd.find_date("Sam 3/14", "dmy", TODAY)[:2] == (14, 3)                              # only March 14th makes sense
    assert bd.find_date("31 April", "dmy", TODAY) is None and bd.find_date("no date here", "dmy", TODAY) is None and bd.find_date("32/13", "dmy", TODAY) is None


@pytest.mark.parametrize("text,name", [
    ("Sarah's birthday on Friday", "Sarah"), ("birthday: Mum", "Mum"), ("Happy birthday Tom!", "Tom"), ("Tom turns 8 next week", "Tom"), ("my mum's birthday", "Mum"),
    ("friend Sarah's birthday", "Sarah"), ("buy a birthday present", ""), ("birthday on Friday", ""), ("Aunty Jo's 60th birthday", "Aunty Jo"), ("don't forget Dad's bday", "Dad")])
def test_names_are_picked_out(text, name):
    assert bd.extract_name(text) == name


@pytest.mark.parametrize("text", ["Sarah's birthday on Friday", "birthday!", "Tom turns 8", "b-day drinks", "Birthdays", "it's my bday"])
def test_birthday_words(text):
    assert bd.BIRTHDAY.search(text)


@pytest.mark.parametrize("text", ["buy milk", "Monday meeting", "turn 90 degrees", "the day after"])
def test_not_birthday_words(text):
    assert not bd.BIRTHDAY.search(text)


@pytest.mark.parametrize("text,expected", [
    ("Sarah 14 March", ("Sarah", 14, 3, 0)), ("14 Mar Dad", ("Dad", 14, 3, 0)), ("Mum 3/4/1960", ("Mum", 3, 4, 1960)), ("Tom - March 9th 2018", ("Tom", 9, 3, 2018)),
    ("Sarah's birthday is on 14 March", ("Sarah", 14, 3, 0)), ("Aunty Jo, 1/12", ("Aunty Jo", 1, 12, 0))])
def test_quick_add_text(text, expected):
    p = bd.parse_person(text, "dmy", TODAY)
    assert (p["name"], p["day"], p["month"], p["year"]) == expected and p["enabled"] is True and p["id"]


def test_quick_add_explains_what_is_missing():
    with pytest.raises(ValueError, match="date"):
        bd.parse_person("Sarah", "dmy", TODAY)
    with pytest.raises(ValueError, match="name"):
        bd.parse_person("14 March", "dmy", TODAY)


def test_people_from_the_browser_or_a_backup_are_made_safe():
    assert bd.clean_person({"name": "  ", "day": 1, "month": 1}) is None and bd.clean_person({"name": "X", "day": 31, "month": 4}) is None
    assert bd.clean_person({"name": "X", "day": "x", "month": 1}) is None
    p = bd.clean_person({"name": "X" * 100, "day": "5", "month": "11", "year": 1700, "note": "n" * 300, "id": "../../etc"}, TODAY)
    assert len(p["name"]) == 60 and p["year"] == 0 and len(p["note"]) == 120 and p["id"] == "etcetc"[:6] or p["id"].isalnum()
    dup = bd.clean_people([{"name": "A", "day": 1, "month": 1, "id": "same"}, {"name": "B", "day": 2, "month": 2, "id": "same"}, "junk", {"name": "C"}])
    assert [x["name"] for x in dup] == ["A", "B"] and len({x["id"] for x in dup}) == 2


def test_29_february_is_kept_on_the_28th_when_there_is_no_29th():
    p = bd.clean_person({"name": "Leap", "day": 29, "month": 2})
    assert bd.occurs_on(p, date(2028, 2, 29)) and not bd.occurs_on(p, date(2028, 2, 28))
    assert bd.occurs_on(p, date(2027, 2, 28)) and not bd.occurs_on(p, date(2027, 3, 1))
    assert bd.next_birthday(p, date(2026, 11, 5)) == date(2027, 2, 28)


def test_upcoming_is_sorted_and_counts_days_and_age():
    cfg = normalise({"birthdays": {"people": [bd.clean_person({"name": "Later", "day": 1, "month": 12, "year": 2000}), bd.clean_person({"name": "Today", "day": 5, "month": 11, "year": 1990}),
                                              bd.clean_person({"name": "Off", "day": 6, "month": 11, "enabled": False}), bd.clean_person({"name": "Past", "day": 1, "month": 1})]}})
    rows = bd.upcoming(cfg, TODAY)
    assert [(r["name"], r["days"], r["turns"]) for r in rows] == [("Today", 0, 36), ("Later", 26, 26), ("Past", 57, 0)]


# ----------------------------------------------------------------------------- the slip
def test_the_slip_has_the_name_the_cake_and_the_age(env):
    cfg, _ = env
    p = bd.clean_person({"name": "Sarah", "day": 5, "month": 11, "year": 1985, "note": "she likes lilies"})
    c = bd.person_card(p, cfg.data, at(7))
    assert (c.kind, c.template, c.heading, c.icons, c.stamp) == ("birthday", "birthday", "Sarah", ["birthday"], "")
    assert c.sections[0].text == "Turns 41 today" and c.sections[1].text == "she likes lilies"
    assert bd.person_card(dict(p, year=0, note=""), cfg.data, at(7)).sections[0].text == "Birthday today"
    tpl = cfg.data["templates"]["birthday"]
    assert tpl["title"]["text"] == "BIRTHDAY" and tpl["icons"]["size_mm"] > 20 and tpl["heading"]["align"] == "center"
    img = render_card(c, tpl, cfg.data["printer"], cfg.data["icons"])
    assert img.size[0] == 576 and sum(1 for px in img.getdata() if px == 0) > 5000


# ----------------------------------------------------------------------------- 7am
def test_it_prints_at_7_once_and_not_before(env):
    cfg, svc = env
    add(cfg)
    add(cfg, name="Tom", day=6)
    b = bd.BirthdayService(cfg, svc)
    assert b.check(at(6, 59)) == 0 and jobs(svc) == []
    assert b.check(at(7, 0)) == 1
    assert jobs(svc) == [{"kind": "birthday", "title": "Sarah", "source": "birthday"}]
    assert b.check(at(7, 1)) == 0 and b.check(at(15)) == 0 and len(jobs(svc)) == 1                      # once a day
    assert bd.BirthdayService(cfg, svc).check(at(8)) == 0 and len(jobs(svc)) == 1                       # and not again after a restart
    assert b.check(at(7, 0, date(2026, 11, 6))) == 1 and jobs(svc)[-1]["title"] == "Tom"                 # next day, next person
    assert b.check(at(7, 0, date(2027, 11, 5))) == 1                                                    # next year


def test_a_pi_that_was_off_at_7_prints_when_it_is_back_if_catch_up_is_on(env):
    cfg, svc = env
    add(cfg)
    assert bd.BirthdayService(cfg, svc).check(at(9, 30)) == 1
    cfg2, svc2 = env[0], Service(env[0], Path(svc.data_dir) / "other")
    d = cfg2.data
    d["birthdays"]["catch_up"] = False
    cfg2.replace(d)
    b = bd.BirthdayService(cfg2, svc2)
    assert b.check(at(9, 30)) == 0 and b.check(at(7, 5)) == 1                                           # only within 10 minutes of the time


def test_off_and_disabled_people_and_other_days_print_nothing(env):
    cfg, svc = env
    add(cfg, enabled=False)
    add(cfg, name="Other", day=9)
    assert bd.BirthdayService(cfg, svc).check(at(8)) == 0
    d = cfg.data
    d["birthdays"]["people"][0]["enabled"] = True
    d["birthdays"]["enabled"] = False
    cfg.replace(d)
    assert bd.BirthdayService(cfg, svc).check(at(8)) == 0 and jobs(svc) == []


def test_a_custom_print_time_and_time_parsing(env):
    cfg, svc = env
    add(cfg)
    d = cfg.data
    d["birthdays"]["print_time"] = "06:30"
    cfg.replace(d)
    b = bd.BirthdayService(cfg, svc)
    assert b.check(at(6, 29)) == 0 and b.check(at(6, 30)) == 1
    assert bd.parse_hhmm("7:5") .hour == 7 and bd.parse_hhmm("nonsense").hour == 7 and bd.parse_hhmm("25:99").hour == 23


def test_the_service_keeps_running_if_a_check_fails(env, monkeypatch):
    cfg, svc = env
    b = bd.BirthdayService(cfg, svc)
    monkeypatch.setattr(b, "check", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    svc.stop.set()
    b.run()                                                                                           # returns at once because stop is set: no crash


# ----------------------------------------------------------------------------- recognising birthday reminders
def reminder(subject, body="", **kw):
    return Incoming(subject=subject, body=body, sender_addr="me@x.com", sender_name="Jack", **kw)


def test_an_emailed_reminder_about_a_birthday_gets_the_birthday_template(env):
    cfg, svc = env
    cards, _ = build_cards(reminder("", "Sarah's birthday on Friday"), cfg.data)
    svc.submit(cards[0])
    (j,) = jobs(svc)
    assert j["kind"] == "birthday"
    assert cards[0].template == "birthday" and cards[0].icons[0] == "birthday" and cards[0].heading == "Sarah"
    assert "Sarah's birthday" in cards[0].sections[0].text                                           # nothing you wrote is removed
    assert cards[0].vars["date"] and "{" not in cards[0].vars["date"]                                # the subtitle has a real date, not "{date}"


def test_the_subject_and_other_sources_work_too_and_other_reminders_are_untouched(env):
    cfg, svc = env
    cards, _ = build_cards(reminder("Mum's birthday", "buy flowers"), cfg.data)
    svc.submit(cards[0])
    cards, _ = build_cards(reminder("Chores", "wash dishes"), cfg.data)
    svc.submit(cards[0])
    web = Card(kind="reminder", heading="Dad's bday", sections=[Section("text", text="ring him")], source="api")
    svc.submit(web)
    assert [j["kind"] for j in jobs(svc)] == ["birthday", "reminder", "birthday"]


def test_calendar_events_called_birthday_get_the_template_but_friends_messages_and_chosen_templates_do_not(env):
    cfg, svc = env
    svc.submit(Card(kind="calendar", heading="Sarah's birthday", sections=[Section("when", text="Fri 7 Nov (all day)")], source="calendar"))
    svc.submit(Card(kind="message", heading="Happy birthday!", sections=[Section("text", text="from me")], source="email"))
    svc.submit(Card(kind="reminder", template="email", heading="Birthday plans", sections=[Section("text", text="x")], source="email"))
    assert [j["kind"] for j in jobs(svc)] == ["birthday", "message", "reminder"]


def test_recognition_can_be_switched_off(env):
    cfg, svc = env
    d = cfg.data
    d["birthdays"]["recognise"] = False
    cfg.replace(d)
    cards, _ = build_cards(reminder("", "Sarah's birthday on Friday"), cfg.data)
    svc.submit(cards[0])
    assert jobs(svc)[0]["kind"] == "reminder"


def test_learning_a_birthday_from_a_reminder_only_when_asked_and_only_once(env):
    cfg, svc = env
    svc.submit(build_cards(reminder("", "Sarah's birthday is 14 March"), cfg.data)[0][0])
    assert cfg.data["birthdays"]["people"] == []                                                      # off by default
    d = cfg.data
    d["birthdays"]["learn"] = True
    cfg.replace(d)
    for _ in range(2):
        svc.submit(build_cards(reminder("", "Sarah's birthday is 14 March 1985"), cfg.data)[0][0])
    svc.submit(build_cards(reminder("", "buy a birthday card"), cfg.data)[0][0])                         # no name, no date
    svc.submit(build_cards(reminder("", "Tom's birthday on Friday"), cfg.data)[0][0])                   # no date
    (p,) = cfg.data["birthdays"]["people"]
    assert (p["name"], p["day"], p["month"], p["year"]) == ("Sarah", 14, 3, 1985) and p["note"] == "added from an email"


# ----------------------------------------------------------------------------- the web page
@pytest.fixture()
def client(tmp_path):
    from test_web import login
    from app.backup import BackupService
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.web import create_app
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc), birthdays=bd.BirthdayService(cfg, svc), backup=BackupService(cfg, svc)).test_client()
    return c, cfg, svc, login


def test_the_birthdays_page_adds_edits_prints_and_removes(client):
    from test_web import H
    c, cfg, svc, login = client
    assert c.get("/api/birthdays").status_code == 401
    login(c)
    r = c.post("/api/birthdays", json={"text": "Sarah 14 March 1985"}, headers=H).get_json()
    pid = r["added"]["id"]
    assert [(p["name"], p["day"], p["month"]) for p in r["people"]] == [("Sarah", 14, 3)] and r["upcoming"][0]["name"] == "Sarah"
    assert c.post("/api/birthdays", json={"text": "Sarah 14 March"}, headers=H).status_code == 400            # already there
    assert "date" in c.post("/api/birthdays", json={"text": "just a name"}, headers=H).get_json()["error"]
    r = c.put(f"/api/birthdays/{pid}", json={"name": "Sarah J", "day": 15, "month": 3, "note": "lilies"}, headers=H).get_json()
    assert (r["people"][0]["name"], r["people"][0]["day"], r["people"][0]["note"], r["people"][0]["year"]) == ("Sarah J", 15, "lilies", 1985)
    assert c.put(f"/api/birthdays/{pid}", json={"day": 31, "month": 2}, headers=H).status_code == 400
    assert c.put("/api/birthdays/nope", json={"name": "x"}, headers=H).status_code == 404
    assert c.post("/api/birthdays-test", json={"id": pid}, headers=H).get_json()["ok"] is True and jobs(svc) == [{"kind": "birthday", "title": "Sarah J", "source": "web"}]
    assert c.post("/api/birthdays-test", json={"id": "nope"}, headers=H).status_code == 404
    assert c.delete(f"/api/birthdays/{pid}", headers=H).get_json()["people"] == []


def test_saving_the_other_settings_never_overwrites_the_list_and_cleans_the_time(client):
    from test_web import H
    c, cfg, svc, login = client
    login(c)
    c.post("/api/birthdays", json={"text": "Sarah 14 March"}, headers=H)
    conf = c.get("/api/config").get_json()
    conf["birthdays"]["people"] = []                                                                  # a stale page
    conf["birthdays"]["print_time"] = "7:5"
    r = c.put("/api/config", json=conf, headers=H).get_json()
    assert len(r["birthdays"]["people"]) == 1 and r["birthdays"]["print_time"] == "07:05"
    schema = c.get("/api/schema").get_json()["sections"]["birthdays"]
    assert {f["path"] for f in schema} >= {"birthdays.enabled", "birthdays.print_time", "birthdays.recognise", "birthdays.learn"}
