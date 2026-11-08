"""Features chosen in the first-run guide: only those get a tab and only those run; Settings can add or remove them later."""
import re
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import birthdays as bd  # noqa: E402
from app import features as ft  # noqa: E402
from app import pinkpanther as pp  # noqa: E402
from app import watchlist as wl  # noqa: E402
from app.config import ConfigStore, normalise  # noqa: E402
from app.mailparse import Incoming  # noqa: E402
from app.pipeline import build_cards, classify_sender  # noqa: E402
from app.service import Service  # noqa: E402

APP_JS = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text()


def done(**chosen):
    """A config whose setup guide is finished with just these features."""
    return normalise({"setup_complete": True, "features": {k: True for k in chosen}})


# ----------------------------------------------------------------------------- the catalogue
def test_every_tab_a_feature_adds_exists_and_every_tab_is_core_or_a_feature():
    tabs = re.findall(r'\["([a-z]+)", "[^"]+"\]', APP_JS[APP_JS.index("const TABS = ["):APP_JS.index("const tabVisible")])
    owned = set(ft.CORE_TABS) | {t for f in ft.FEATURES for t in f["tabs"]}
    assert set(tabs) == owned and len(tabs) == len(set(tabs))
    assert len(ft.FEATURE_IDS) == len(set(ft.FEATURE_IDS)) and all(f["blurb"] and f["title"] for f in ft.FEATURES)
    assert {"dashboard", "settings", "backup", "printer", "account"} <= set(ft.CORE_TABS)


def test_only_email_and_calendar_and_symbols_are_recommended_and_the_rest_are_opt_in():
    assert {f["id"] for f in ft.FEATURES if f["recommended"]} == {"email", "calendar", "symbols"}
    assert {f["id"] for f in ft.FEATURES if f["step"]} == {"email", "calendar"}                    # the guide walks through email and calendar only


# ----------------------------------------------------------------------------- which tabs show
def test_only_the_chosen_features_have_a_tab():
    cfg = done(email=True, calendar=True)
    tabs = ft.visible_tabs(cfg)
    assert {"email", "text", "rules", "calendar"} <= set(tabs) and set(ft.CORE_TABS) <= set(tabs)
    for hidden in ("form", "airprint", "shortcuts", "pinkpanther", "watchlist", "birthdays", "messages", "aireview", "power", "integrations", "symbols"):
        assert hidden not in tabs
    assert "form" in ft.visible_tabs(done(email=True, form=True)) and "text" in ft.visible_tabs(done(form=True))        # shared tabs follow any feature that uses them
    assert "text" not in ft.visible_tabs(done(calendar=True))


def test_until_the_guide_is_finished_everything_counts_as_on():
    cfg = normalise({})
    assert cfg["setup_complete"] is False and all(ft.on(cfg, fid) for fid in ft.FEATURE_IDS)
    assert ft.on(done(email=True), "email") and not ft.on(done(email=True), "form")


def test_an_install_from_before_the_guide_is_moved_to_all_on_so_nothing_disappears():
    old = normalise({"web": {"password_hash": "x"}, "email": {"username": "a@b.c"}})                 # no "features" key, a password: already in use
    assert old["setup_complete"] is True and all(old["features"].values()) and set(old["features"]) == set(ft.FEATURE_IDS)
    rescued = normalise({"email": {}, "printer": {}, "templates": {}, "web": {"password_hash": "h"}})   # a raw config.json from a dead card
    assert rescued["setup_complete"] is True and all(rescued["features"].values())
    fresh = normalise({})
    assert fresh["setup_complete"] is False and not any(fresh["features"].values())
    partial = normalise({"setup_complete": True, "features": {"email": True, "bogus": True}})
    assert partial["features"]["email"] is True and partial["features"]["form"] is False and "bogus" not in partial["features"]


def test_a_config_file_from_an_old_version_loads_as_all_on_and_a_fresh_one_waits_for_the_guide(tmp_path):
    import json
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"web": {"password_hash": "x"}, "friends": []}))
    assert ConfigStore(p).data["setup_complete"] is True
    assert ConfigStore(tmp_path / "new.json").data["setup_complete"] is False


# ----------------------------------------------------------------------------- unchosen features really don't run
def msg(subject="", body="", addr="me@x.com"):
    return Incoming(subject=subject, body=body, sender_addr=addr, sender_name="Jack")


def test_pink_panther_watch_list_and_birthdays_do_nothing_unless_chosen():
    off, on = done(email=True), done(email=True, pinkpanther=True, watchlist=True, birthdays=True)
    for cfg, expect in ((off, "reminder"), (on, None)):
        kinds = {c.kind for c in build_cards(msg("Pink Panther", "The Pink Phink"), dict(cfg, pinkpanther=dict(cfg["pinkpanther"], lookup=False)))[0]}
        assert (kinds == {expect}) if expect else (kinds == {"pinkpanther"})
    assert {c.kind for c in build_cards(msg("Watch list", "Dune (2021)"), off)[0]} == {"reminder"}
    assert {c.kind for c in build_cards(msg("Watch list", "Dune (2021)"), on)[0]} == {"watchlist"}
    from app.models import Card, Section
    card = lambda: Card(kind="reminder", heading="Sarah's birthday", sections=[Section("text", text="x")])
    assert bd.restyle(card(), off).kind == "reminder" and bd.restyle(card(), on).kind == "birthday"


def test_birthdays_are_not_printed_unless_the_feature_is_chosen(tmp_path):
    from datetime import datetime, timezone, timedelta
    store = ConfigStore(tmp_path / "c.json")
    d = store.data
    d["printer"]["backend"] = "dummy"
    d["setup_complete"] = True
    d["birthdays"]["people"] = [bd.clean_person({"name": "Sarah", "day": 5, "month": 11})]
    store.replace(d)
    svc = Service(store, tmp_path)
    now = datetime(2026, 11, 5, 8, 0, tzinfo=timezone(timedelta(hours=11)))
    assert bd.BirthdayService(store, svc).check(now) == 0
    d = store.data
    d["features"]["birthdays"] = True
    store.replace(d)
    assert bd.BirthdayService(store, svc).check(now) == 1


def test_friends_only_count_when_messages_is_chosen():
    friend = {"name": "Sarah", "email": "sarah@x.com", "enabled": True}
    cfg = done(email=True)
    cfg["friends"] = [friend]
    assert classify_sender("sarah@x.com", cfg) == (None, None)
    cfg["features"]["messages"] = True
    assert classify_sender("sarah@x.com", cfg)[0] == "friend"


def test_airprint_is_never_advertised_unless_chosen():
    from app.airprint import Settings
    cfg = done(email=True)
    cfg["airprint"]["enabled"] = True
    assert Settings.from_cfg(cfg).enabled is False
    cfg["features"]["airprint"] = True
    assert Settings.from_cfg(cfg).enabled is True


def test_the_mailbox_is_not_polled_and_the_calendar_not_fetched_unless_chosen(tmp_path, monkeypatch):
    from app import calsync, mailbox
    store = ConfigStore(tmp_path / "c.json")
    d = store.data
    d["printer"]["backend"] = "dummy"
    d["setup_complete"] = True
    d["email"].update(username="p@gmail.com", password="x", enabled=True)
    d["calendars"] = [{"id": "c1", "name": "Mine", "url": "https://example.com/x.ics", "enabled": True, "template": "calendar"}]
    store.replace(d)
    svc = Service(store, tmp_path)
    poller, cal = mailbox.MailPoller(store, svc), calsync.CalendarSync(store, svc)
    polled, fetched = [], []
    monkeypatch.setattr(poller, "poll_once", lambda keep=False: polled.append(1) or 0)
    monkeypatch.setattr(calsync, "fetch_ics", lambda url: fetched.append(url) or b"")
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    time.sleep(0.4)
    svc.stop.set()
    poller.kick.set()
    t.join(2)
    cal.sync()
    assert polled == [] and fetched == [] and poller.status["state"] == "disabled"
    svc.stop.clear()
    d = store.data
    d["features"].update(email=True, calendar=True)
    store.replace(d)
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    time.sleep(0.4)
    svc.stop.set()
    poller.kick.set()
    t.join(2)
    try:
        cal.sync()
    except Exception:
        pass
    assert polled and fetched                                                                    # both ran once chosen


# ----------------------------------------------------------------------------- the pages
@pytest.fixture()
def client(tmp_path):
    from app.backup import BackupService
    from app.birthdays import BirthdayService
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.web import create_app
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc), birthdays=BirthdayService(cfg, svc), backup=BackupService(cfg, svc)).test_client()
    return c, cfg, svc


def test_first_use_walks_through_the_guide_and_the_chosen_features_make_the_menu(client):
    from test_web import H, login
    c, cfg, svc = client
    assert c.get("/api/features").status_code == 401
    login(c)                                                                                     # sets the password: first use
    conf = c.get("/api/config").get_json()
    assert conf["setup_complete"] is False and not any(conf["features"].values())              # the browser will show the guide
    st = c.get("/api/features").get_json()
    assert st["setup_complete"] is False and {f["id"] for f in st["catalog"]} == set(ft.FEATURE_IDS)
    r = c.put("/api/features", json={"features": {"email": True, "calendar": True, "bogus": True}}, headers=H).get_json()
    assert r["features"]["email"] is True and "bogus" not in r["features"] and r["setup_complete"] is False
    r = c.put("/api/features", json={"features": {"email": True, "calendar": True}, "complete": True}, headers=H).get_json()
    assert r["setup_complete"] is True and "email" in r["tabs"] and "calendar" in r["tabs"] and "form" not in r["tabs"] and "settings" in r["tabs"]


def test_settings_can_add_and_remove_features_later_without_losing_their_setup(client):
    from test_web import H, login
    c, cfg, svc = client
    login(c)
    c.put("/api/features", json={"features": {"email": True}, "complete": True}, headers=H)
    c.post("/api/birthdays", json={"text": "Sarah 14 March"}, headers=H)
    r = c.put("/api/features", json={"features": {"email": True, "birthdays": True}}, headers=H).get_json()
    assert "birthdays" in r["tabs"]
    r = c.put("/api/features", json={"features": {"email": True}}, headers=H).get_json()
    assert "birthdays" not in r["tabs"] and len(cfg.data["birthdays"]["people"]) == 1             # hidden, not deleted
    r = c.put("/api/features", json={"features": {"email": True, "birthdays": True}}, headers=H).get_json()
    assert "birthdays" in r["tabs"] and cfg.data["birthdays"]["people"][0]["name"] == "Sarah"      # back as it was


def test_saving_other_settings_never_changes_the_features(client):
    from test_web import H, login
    c, cfg, svc = client
    login(c)
    c.put("/api/features", json={"features": {"email": True}, "complete": True}, headers=H)
    conf = c.get("/api/config").get_json()
    conf["features"] = {fid: True for fid in ft.FEATURE_IDS}
    conf["setup_complete"] = False
    c.put("/api/config", json=conf, headers=H)
    assert cfg.data["features"]["form"] is False and cfg.data["features"]["email"] is True and cfg.data["setup_complete"] is True


def test_the_print_api_is_refused_unless_iphone_shortcuts_was_chosen(client):
    from test_web import H, login
    c, cfg, svc = client
    login(c)
    d = cfg.data
    d["integrations"].update(api_enabled=True, api_token="tok123")
    cfg.replace(d)
    c.put("/api/features", json={"features": {"email": True}, "complete": True}, headers=H)
    r = c.post("/api/print", json={"text": "hello"}, headers={"Authorization": "Bearer tok123"})
    assert r.status_code == 401
    c.put("/api/features", json={"features": {"email": True, "shortcuts": True}}, headers=H)
    r = c.post("/api/print", json={"text": "hello"}, headers={"Authorization": "Bearer tok123"})
    assert r.status_code == 200


def test_restoring_a_backup_from_the_guide_skips_the_rest(client):
    from test_web import H, login
    from app import backup as bk
    c, cfg, svc = client
    login(c)
    other = normalise({"setup_complete": True, "features": {"email": True, "form": True}, "friends": [{"name": "Z", "email": "z@x.com"}]})
    r = c.post("/api/backup-restore", json=bk.make_backup(other, True), headers=H).get_json()
    assert r["ok"] and cfg.data["setup_complete"] is True and cfg.data["features"]["form"] is True
    st = c.get("/api/features").get_json()
    assert "form" in st["tabs"] and "airprint" not in st["tabs"]
