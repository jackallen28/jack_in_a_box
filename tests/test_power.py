import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import llm  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.power import FanController, PowerManager, read_cpu_temp  # noqa: E402
from app.service import Service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
gz = pytest.importorskip("gpiozero")


@pytest.fixture()
def rig(tmp_path):
    from gpiozero.pins.mock import MockFactory
    old = gz.Device.pin_factory
    gz.Device.pin_factory = MockFactory()
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["fan"].update(enabled=True, pin=22, mode="auto", on_temp=60, off_temp=50, during_ai=True, min_on_seconds=60)
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    yield cfg, svc, gz.Device.pin_factory
    gz.Device.pin_factory = old


class Env:
    """Fake thermometer, AI flag and clock for the fan."""
    def __init__(self):
        self.temp, self.ai, self.t = 40.0, False, 1000.0


def make_fan(cfg):
    env = Env()
    fan = FanController(cfg, temp_reader=lambda: env.temp, ai_busy=lambda: env.ai, clock=lambda: env.t)
    fan.reconfigure()
    return fan, env


def state(pins):
    return pins.pin(22).state


# ----------------------------------------------------------------------------- fan
def test_fan_comes_on_when_hot_and_has_hysteresis_and_minimum_run_time(rig):
    cfg, svc, pins = rig
    fan, env = make_fan(cfg)
    assert fan.status["state"] == "ok"
    fan.tick()
    assert state(pins) == 0                                   # 40C: off
    env.temp = 61
    fan.tick()
    assert state(pins) == 1 and "above 60" in fan.status["reason"]
    env.temp, env.t = 55, env.t + 120                         # cooler than on-temp but above off-temp: keep running
    fan.tick()
    assert state(pins) == 1
    env.temp, env.t = 49, env.t + 1                           # cool, but let's check the minimum run time first
    fan.on_since = env.t - 10
    fan.tick()
    assert state(pins) == 1 and fan.status["reason"] == "minimum run time"
    env.t += 120
    fan.tick()
    assert state(pins) == 0


def test_fan_runs_for_the_whole_ai_summary_even_when_cool(rig):
    cfg, svc, pins = rig
    fan, env = make_fan(cfg)
    env.ai = True
    fan.tick()
    assert state(pins) == 1 and fan.status["reason"] == "AI summary running"
    env.t += 500
    fan.tick()
    assert state(pins) == 1                                   # still summarising
    env.ai = False
    fan.tick()
    assert state(pins) == 0                                   # cool and done (min run time long past)
    d = cfg.data
    d["fan"]["during_ai"] = False
    cfg.replace(d)
    env.ai = True
    fan.tick()
    assert state(pins) == 0                                   # AI boost switched off in settings


def test_fan_always_off_and_disabled_and_bad_pin(rig):
    cfg, svc, pins = rig
    fan, env = make_fan(cfg)
    d = cfg.data
    d["fan"]["mode"] = "always"
    cfg.replace(d)
    fan.tick()
    assert state(pins) == 1
    d["fan"]["mode"] = "off"
    d["fan"]["min_on_seconds"] = 0
    cfg.replace(d)
    env.temp = 90
    fan.tick()
    assert state(pins) == 0                                   # "off" means off, even when hot
    d["fan"]["enabled"] = False
    cfg.replace(d)
    fan.reconfigure()
    assert fan.status["state"] == "disabled"
    d["fan"].update(enabled=True, pin=999)
    cfg.replace(d)
    fan.reconfigure()
    assert fan.status["state"] == "error" and "GPIO" in fan.status["message"]
    fan.tick()                                                # must not crash with no device


def test_active_low_driver_is_inverted(rig):
    cfg, svc, pins = rig
    d = cfg.data
    d["fan"]["active_high"] = False
    cfg.replace(d)
    fan, env = make_fan(cfg)
    fan.tick()
    assert state(pins) == 1                                   # idle = pin HIGH for an active-low board
    env.temp = 70
    fan.tick()
    assert state(pins) == 0


def test_unreadable_temperature_does_not_crash_or_start_the_fan(rig):
    cfg, svc, pins = rig
    fan, env = make_fan(cfg)
    env.temp = None
    fan.tick()
    assert state(pins) == 0 and fan.status["temp"] is None
    assert read_cpu_temp("/nonexistent/path") is None


def test_ai_busy_flag_is_set_while_the_model_runs_and_cleared_after(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow_post(cfg, path, payload, timeout):
        started.set()
        release.wait(5)
        return {"message": {"content": "Please reply by Friday about the overdue invoice."}}

    monkeypatch.setattr(llm, "_post", slow_post)
    assert not llm.is_busy()
    out = {}
    t = threading.Thread(target=lambda: out.update(r=llm.run(["Please reply by Friday about the overdue invoice."], 3, {})))
    t.start()
    assert started.wait(2) and llm.is_busy()                  # the fan would be on right now
    release.set()
    t.join(5)
    assert not llm.is_busy()
    monkeypatch.setattr(llm, "_post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    llm.run(["Please reply by Friday."], 3, {})
    assert not llm.is_busy()                                  # cleared even when the request fails


# ----------------------------------------------------------------------------- idle power-off
def make_pm(cfg, svc, uptime=10_000, ai=False):
    called = []
    pm = PowerManager(cfg, svc, poweroff=lambda: called.append(1), uptime=lambda: uptime, ai_busy=lambda: ai)
    return pm, called


def set_power(cfg, **kw):
    d = cfg.data
    d["power"].update(kw)
    cfg.replace(d)


def test_off_by_default(rig):
    cfg, svc, _ = rig
    svc.last_activity = time.time() - 10 * 3600
    pm, _ = make_pm(cfg, svc)
    assert pm.evaluate()[0] is False and "off" in pm.evaluate()[1]


def test_powers_off_only_when_idle_long_enough_and_nothing_is_waiting(rig):
    cfg, svc, _ = rig
    set_power(cfg, idle_shutdown_hours=2)
    pm, _ = make_pm(cfg, svc)
    svc.last_activity = time.time() - 30 * 60
    go, why = pm.evaluate()
    assert not go and ("about 90 minutes" in why or "about 89 minutes" in why)
    svc.last_activity = time.time() - 3 * 3600
    assert pm.evaluate()[0] is True
    # ...but not just after boot
    pm2, _ = make_pm(cfg, svc, uptime=5 * 60)
    assert not pm2.evaluate()[0]
    # ...or while the AI is working
    pm3, _ = make_pm(cfg, svc, ai=True)
    assert not pm3.evaluate()[0] and "AI" in pm3.evaluate()[1]


def test_never_powers_off_with_queued_or_held_work(rig):
    cfg, svc, _ = rig
    set_power(cfg, idle_shutdown_hours=1)
    svc.last_activity = time.time() - 5 * 3600
    pm, _ = make_pm(cfg, svc)
    svc.db.execute("INSERT INTO jobs(created, kind, source, title, status) VALUES ('2026-01-01T00:00:00','reminder','t','x','held')")
    go, why = pm.evaluate()
    assert not go and "waiting" in why
    svc.db.execute("UPDATE jobs SET status='pending'")
    assert not pm.evaluate()[0]
    svc.db.execute("UPDATE jobs SET status='printed'")
    assert pm.evaluate()[0] is True


def test_stays_awake_for_an_upcoming_calendar_print(rig):
    cfg, svc, _ = rig
    set_power(cfg, idle_shutdown_hours=1, keep_awake_before_calendar_hours=12)
    svc.last_activity = time.time() - 5 * 3600
    pm, _ = make_pm(cfg, svc)
    now = datetime.now().astimezone()

    def add(key, due, end):
        svc.db.execute("INSERT INTO cal_events(key, cal_id, start, end, first_seen, due, status, data) VALUES (?,?,?,?,?,?, 'pending', '{}')",
                       (key, "c", end.isoformat(), end.isoformat(), now.isoformat(), due.isoformat()))

    add("far", now + timedelta(hours=30), now + timedelta(hours=40))
    assert pm.evaluate()[0] is True                                   # 30 h away: fine to power off
    add("soon", now + timedelta(hours=6), now + timedelta(hours=9))
    go, why = pm.evaluate()
    assert not go and "calendar" in why                               # 6 h away: stay on
    svc.db.execute("DELETE FROM cal_events WHERE key='soon'")
    add("finished", now - timedelta(hours=2), now - timedelta(hours=1))
    assert pm.evaluate()[0] is True                                   # an event that's already over doesn't count


def test_activity_resets_the_idle_clock(rig, tmp_path):
    cfg, svc, _ = rig
    svc.last_activity = time.time() - 5 * 3600
    from app.mailparse import Incoming  # noqa: F401
    from app.models import Card, Section
    svc.submit(Card(kind="reminder", heading="x", sections=[Section("text", text="y")]))
    assert time.time() - svc.last_activity < 5
    svc.last_activity = time.time() - 5 * 3600
    svc.release_held()
    assert time.time() - svc.last_activity < 5


def test_the_loop_actually_calls_poweroff_once(rig):
    cfg, svc, _ = rig
    set_power(cfg, idle_shutdown_hours=1)
    svc.last_activity = time.time() - 5 * 3600
    pm, called = make_pm(cfg, svc)
    pm.start()
    pm.join(5)
    assert called == [1] and not pm.is_alive()
    svc.stop.set()


def test_web_page_activity_counts_but_background_polling_does_not(rig, tmp_path):
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.web import create_app
    cfg, svc, _ = rig
    fan, _ = make_fan(cfg)
    pm, _ = make_pm(cfg, svc)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc), fan, pm).test_client()
    H = {"X-Requested-With": "fetch"}
    c.post("/api/setup", json={"password": "hunter22"}, headers=H)
    svc.last_activity = time.time() - 5 * 3600
    c.get("/api/status")
    c.get("/api/history")                                              # the dashboard polls these every 5 s
    assert time.time() - svc.last_activity > 4 * 3600
    c.post("/api/test-print", json={"template": "reminder"}, headers=H)
    assert time.time() - svc.last_activity < 5
    r = c.get("/api/power-status").json
    assert {"cpu_temp", "fan", "power", "idle_minutes", "throttle"} <= set(r)
    assert c.get("/api/status").json["fan"]["state"] == "ok"
    conf = c.get("/api/config").json
    conf["fan"]["enabled"] = False
    c.put("/api/config", json=conf, headers=H)
    assert fan.status["state"] == "disabled"                           # saving settings re-applies the fan config


# ----------------------------------------------------------------------------- eco-mode script
def test_eco_mode_is_idempotent_and_undo_restores_the_file_exactly(tmp_path):
    cfgf = tmp_path / "config.txt"
    original = "# keep me\ndtparam=audio=on\ngpu_mem=16\ndtoverlay=disable-bt\nhdmi_force_hotplug=1\n"
    cfgf.write_text(original)
    env = {**os.environ, "JIAB_BOOT_CONFIG": str(cfgf), "JIAB_DRY_RUN": "1", "JIAB_UNIT_FILE": str(tmp_path / "eco.service")}
    for _ in range(3):
        r = subprocess.run(["bash", str(ROOT / "deploy/eco-mode.sh")], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    text = cfgf.read_text()
    assert text.count("BEGIN jack-in-a-box eco") == 1 and text.count("act_led_trigger=none") == 1
    assert text.count("dtoverlay=disable-bt") == 1                         # didn't duplicate the one that was already there
    assert "[Service]" in (tmp_path / "eco.service").read_text()
    r = subprocess.run(["bash", str(ROOT / "deploy/eco-mode.sh"), "--undo"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and cfgf.read_text() == original and not (tmp_path / "eco.service").exists()
