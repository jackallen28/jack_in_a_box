import io
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_core import mk  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.gpio import ButtonController  # noqa: E402
from app.mailbox import MailPoller  # noqa: E402
from app.mailparse import parse  # noqa: E402
from app.pipeline import classify_sender, message_card  # noqa: E402
from app.service import Service  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["email"]["allowed_senders"] = ["me@example.com"]
    d["general"]["timezone"] = "Australia/Sydney"
    d["friends"] = [
        {"name": "Sarah", "email": "sarah@x.com", "bypass_queue": False, "enabled": True},
        {"name": "Mum", "email": "mum@x.com", "bypass_queue": True, "enabled": True},
        {"name": "Ex", "email": "ex@x.com", "bypass_queue": False, "enabled": False},
    ]
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    return cfg, svc, MailPoller(cfg, svc)


def status(svc):
    return [(r["title"], r["status"]) for r in svc.db.all("SELECT title, status FROM jobs ORDER BY id")]


def test_sender_roles(env):
    cfg, *_ = env
    assert classify_sender("me@example.com", cfg.data)[0] == "admin"
    role, f = classify_sender("SARAH@x.com", cfg.data)
    assert role == "friend" and f["name"] == "Sarah"
    assert classify_sender("ex@x.com", cfg.data) == (None, None)        # disabled friend
    assert classify_sender("stranger@x.com", cfg.data) == (None, None)
    d = cfg.data
    d["friends"].append({"name": "Me too", "email": "me@example.com", "enabled": True})
    cfg.replace(d)
    assert classify_sender("me@example.com", cfg.data)[0] == "admin"    # admin wins over friend


def test_friend_message_is_held_and_not_altered(env):
    cfg, svc, poller = env
    ids = poller.handle(mk("Dinner??", "dont forget teh wine, tomorow at 7pm", sender="Sarah <sarah@x.com>", mid="<f1@x>"))
    assert len(ids) == 1 and status(svc) == [("Dinner??", "held")]
    svc.process_pending()
    assert status(svc) == [("Dinner??", "held")]                          # held jobs never print by themselves
    card = message_card(parse(mk("Dinner??", "dont forget teh wine, tomorow at 7pm", sender="Sarah <sarah@x.com>")), cfg.data["friends"][0], cfg.data)
    assert card.kind == "message" and card.vars["name"] == "Sarah" and card.heading == "Dinner??"
    assert card.sections[0].text == "dont forget teh wine, tomorow at 7pm"   # exactly as written, not split/corrected
    assert not any(s.key == "deadline" for s in card.sections)


def test_message_title_and_name_are_on_the_printout(env):
    from app.render import render_card
    cfg, *_ = env
    card = message_card(parse(mk("Hi", "yo", sender="Sarah <sarah@x.com>")), cfg.data["friends"][0], cfg.data)
    tpl = cfg.data["templates"]["message"]
    assert tpl["title"]["text"] == "MESSAGE" and "{name}" in tpl["title"]["subtitle"]
    img = render_card(card, tpl, cfg.data["printer"])
    assert img.size[0] == 576 and img.size[1] >= 240      # at least 30 mm (240 dots)
    # the name really is drawn: same card with a different name must produce a different bitmap
    card2 = message_card(parse(mk("Hi", "yo", sender="Sarah <sarah@x.com>")), {**cfg.data["friends"][0], "name": "Bob"}, cfg.data)
    assert render_card(card2, tpl, cfg.data["printer"]).tobytes() != img.tobytes()


def test_tidy_option(env):
    cfg, *_ = env
    d = cfg.data
    d["messages"]["tidy_text"] = True
    cfg.replace(d)
    card = message_card(parse(mk("Hi", "dont forget teh wine", sender="sarah@x.com")), cfg.data["friends"][0], cfg.data)
    assert card.sections[0].text == "Don't forget the wine."


def test_bypass_admin_and_hold_off(env):
    cfg, svc, poller = env
    poller.handle(mk("Hello", "hi there", sender="mum@x.com", mid="<m@x>"))                       # bypass friend
    poller.handle(mk("Chores", "wash dishes", sender="me@example.com", mid="<a@x>"))             # admin
    poller.handle(mk("Psst", "hi", sender="sarah@x.com", mid="<s@x>"))                          # normal friend
    assert status(svc) == [("Hello", "pending"), ("Chores", "pending"), ("Psst", "held")]
    d = cfg.data
    d["messages"]["hold_for_button"] = False
    cfg.replace(d)
    poller.handle(mk("Psst2", "hi", sender="sarah@x.com", mid="<s2@x>"))
    assert status(svc)[-1] == ("Psst2", "pending")


def test_friend_images_print_with_the_message(env):
    cfg, svc, poller = env
    pic = Image.new("RGB", (400, 300), (10, 120, 200))
    poller.handle(mk("Look!", "my new bike", sender="sarah@x.com", images=[pic], mid="<i@x>"))
    inc = parse(mk("Look!", "my new bike", sender="sarah@x.com", images=[pic]))
    assert len(inc.images) == 1
    card = message_card(inc, cfg.data["friends"][0], cfg.data)
    assert len(card.images) == 1 and len(svc.db.all("SELECT 1 FROM jobs")) == 1


def test_release_prints_oldest_first_and_max_held(env):
    cfg, svc, poller = env
    d = cfg.data
    d["messages"]["max_held"] = 2
    cfg.replace(d)
    for i in range(3):
        poller.handle(mk(f"m{i}", "x", sender="sarah@x.com", mid=f"<q{i}@x>"))
    assert status(svc) == [("m0", "held"), ("m1", "held")]                # third dropped, not queued forever
    assert svc.release_held() == 2
    svc.process_pending()
    assert status(svc) == [("m0", "printed"), ("m1", "printed")]
    assert svc.release_held() == 0


# ------------------------------------------------------------------ physical button + LED (gpiozero mock pins)
def test_button_and_led(env):
    gz = pytest.importorskip("gpiozero")
    from gpiozero.pins.mock import MockFactory
    cfg, svc, poller = env
    old = gz.Device.pin_factory
    gz.Device.pin_factory = MockFactory()
    try:
        d = cfg.data
        d["button"].update(enabled=True, button_pin=17, led_pin=27, blink_seconds=0.1)
        cfg.replace(d)
        ctl = ButtonController(cfg, svc)
        ctl.start()
        assert ctl.status["state"] == "ok", ctl.status
        led = gz.Device.pin_factory.pin(27)
        time.sleep(0.8)
        assert led.state == 0                                              # nothing waiting -> LED off
        poller.handle(mk("Hi", "hello", sender="sarah@x.com", mid="<b1@x>"))
        poller.handle(mk("Yo", "hello", sender="sarah@x.com", mid="<b2@x>"))
        ctl._wake.set()
        seen = set()
        end = time.time() + 2.5
        while time.time() < end and len(seen) < 2:
            seen.add(led.state)
            time.sleep(0.02)
        assert seen == {0, 1}                                              # LED is flashing
        assert [s for _, s in status(svc)] == ["held", "held"]
        btn = gz.Device.pin_factory.pin(17)
        btn.drive_low()                                                    # press
        time.sleep(0.4)
        btn.drive_high()
        assert [s for _, s in status(svc)] == ["pending", "pending"]       # released to the printer
        time.sleep(1.2)
        assert led.state == 0                                              # LED stops
        svc.process_pending()
        assert [s for _, s in status(svc)] == ["printed", "printed"]
        ctl._close()
    finally:
        gz.Device.pin_factory = old


def test_button_disabled_and_bad_gpio_do_not_crash(env):
    cfg, svc, _ = env
    ctl = ButtonController(cfg, svc)
    ctl.reconfigure()
    assert ctl.status["state"] == "disabled"
    d = cfg.data
    d["button"].update(enabled=True, button_pin=999)
    cfg.replace(d)
    ctl.reconfigure()
    assert ctl.status["state"] == "error" and ctl.test_led() is False


# ------------------------------------------------------------------ web: print custom message
def test_print_now_endpoint(tmp_path, monkeypatch):
    from app.calsync import CalendarSync
    from app.web import create_app
    H = {"X-Requested-With": "fetch"}
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc)).test_client()
    assert c.post("/api/print-now", data={"text": "x"}, headers=H).status_code == 401
    c.post("/api/setup", json={"password": "hunter22"}, headers=H)

    r = c.post("/api/print-now", data={"heading": "Chores", "text": "wash dishes and walk the dog", "split": "1"}, headers=H)
    assert r.json == {"queued": 2, "cards": 2}
    r = c.post("/api/print-now", data={"heading": "Poem", "text": "roses are red, violets r blue", "exact": "1", "urgent": "1", "copies": "2"}, headers=H)
    assert r.json["queued"] == 2 and r.json["cards"] == 1
    rows = svc.db.all("SELECT title, status FROM jobs ORDER BY id")
    assert [x["status"] for x in rows] == ["pending"] * 4                # admin prints are never held

    buf = io.BytesIO()
    Image.new("RGB", (500, 400), "red").save(buf, "PNG")
    r = c.post("/api/print-now", data={"text": "see photo", "images": (io.BytesIO(buf.getvalue()), "p.png")},
               headers=H, content_type="multipart/form-data")
    assert r.json["cards"] == 1
    assert c.post("/api/print-now", data={}, headers=H).status_code == 400
    bad = c.post("/api/print-now", data={"text": "x", "images": (io.BytesIO(b"nope"), "p.png")}, headers=H, content_type="multipart/form-data")
    assert bad.status_code == 400

    svc.db.execute("UPDATE jobs SET status='held' WHERE id=1")
    assert c.get("/api/status").json["printer"]["held"] == 1
    assert c.post("/api/release", headers=H).json == {"released": 1}


def test_saving_friends_cleans_the_list(tmp_path):
    from app.calsync import CalendarSync
    from app.web import create_app
    H = {"X-Requested-With": "fetch"}
    cfg = ConfigStore(tmp_path / "c.json")
    svc = Service(cfg, tmp_path)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc)).test_client()
    c.post("/api/setup", json={"password": "hunter22"}, headers=H)
    conf = c.get("/api/config").json
    conf["friends"] = [{"name": " Sarah ", "email": " SARAH@X.com "}, {"name": "dup", "email": "sarah@x.com"},
                       {"name": "", "email": "joe@x.com", "bypass_queue": True}, {"name": "bad", "email": "nope"}]
    out = c.put("/api/config", json=conf, headers=H).json["friends"]
    assert [(f["name"], f["email"], f["bypass_queue"]) for f in out] == [("Sarah", "sarah@x.com", False), ("joe", "joe@x.com", True)]
