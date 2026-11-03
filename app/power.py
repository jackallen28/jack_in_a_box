"""Fan control and idle power-off.

FAN   A 5 V fan switched by a GPIO pin through a transistor (a GPIO pin can't power a fan itself - see README).
      auto mode: on at ``on_temp`` (default 60 C), off again below ``off_temp`` (50 C) so it doesn't chatter, kept on for
      at least ``min_on_seconds``.  With ``during_ai`` the fan also runs for the whole time the AI is summarising (the
      CPU works flat-out and heats quickly, so cool it before it throttles).

IDLE POWER-OFF (opt-in, off by default)
      A Raspberry Pi 3B+ has no sleep mode and no clock to wake itself, so the only real "deep sleep" is switching off.
      After ``idle_shutdown_hours`` with nothing happening it runs ``systemctl poweroff`` - but never while anything is
      queued, held for the button, an AI summary is running, or a calendar print is due soon.  While it's off nothing
      is printed and email waits safely in Gmail; wake it by shorting GPIO3 to ground (a button on pins 5 and 6) or by
      re-plugging power, and everything catches up.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime, timedelta

from . import llm
from .config import ConfigStore
from .service import Service

log = logging.getLogger("power")
THERMAL = "/sys/class/thermal/thermal_zone0/temp"


def read_cpu_temp(path: str = THERMAL) -> float | None:
    try:
        return int(open(path).read().strip()) / 1000.0
    except Exception:
        return None


def read_throttled() -> dict:
    """Best-effort `vcgencmd get_throttled` (Raspberry Pi only): under-voltage / throttling flags."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        v = int(out.split("=")[1].strip(), 16)
        return {"undervoltage": bool(v & 0x1), "throttled": bool(v & 0x4), "undervoltage_seen": bool(v & 0x10000),
                "throttled_seen": bool(v & 0x40000)}
    except Exception:
        return {}


def read_uptime() -> float:
    try:
        return float(open("/proc/uptime").read().split()[0])
    except Exception:
        return 1e9


# --------------------------------------------------------------------------- fan
class FanController:
    def __init__(self, cfg: ConfigStore, temp_reader=read_cpu_temp, ai_busy=llm.is_busy, clock=time.time):
        self.cfg, self.read_temp, self.ai_busy, self.clock = cfg, temp_reader, ai_busy, clock
        self._lock = threading.RLock()
        self._dev = None
        self.on = False
        self.on_since = 0.0
        self.status = {"state": "disabled", "message": "Fan control is off.", "temp": None, "fan_on": False, "reason": ""}
        self._wake = threading.Event()
        self._stop = threading.Event()

    def start(self) -> None:
        self.reconfigure()
        threading.Thread(target=self._loop, name="fan", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._set(False, force=True)

    def reconfigure(self) -> None:
        with self._lock:
            self._set(False, force=True)
            if self._dev is not None:
                try:
                    self._dev.close()
                except Exception:
                    pass
                self._dev = None
            c = self.cfg.data["fan"]
            if not c.get("enabled"):
                self.status.update(state="disabled", message="Fan control is off.", fan_on=False)
                return
            try:
                from gpiozero import OutputDevice  # type: ignore
                self._dev = OutputDevice(int(c["pin"]), active_high=bool(c.get("active_high", True)), initial_value=False)
                self.status.update(state="ok", message=f"Fan on GPIO{c['pin']} ({c.get('mode', 'auto')}).")
            except Exception as e:
                self._dev = None
                self.status.update(state="error", message=f"GPIO not available: {e}", fan_on=False)
                log.warning("Fan setup failed: %s", e)
            self._wake.set()

    def _set(self, on: bool, force: bool = False) -> None:
        if self._dev is None:
            self.on = False
            return
        if on == self.on and not force:
            return
        try:
            self._dev.on() if on else self._dev.off()
        except Exception as e:
            log.warning("Fan switch failed: %s", e)
        self.on = on
        if on:
            self.on_since = self.clock()

    def decide(self, temp: float | None, c: dict) -> tuple[bool, str]:
        mode = c.get("mode", "auto")
        if mode == "always":
            return True, "always on"
        if mode == "off":
            return False, "fan set to off"
        on_t = float(c.get("on_temp", 60))
        off_t = min(float(c.get("off_temp", 50)), on_t - 2)
        if temp is not None and temp >= on_t:
            return True, f"CPU {temp:.0f}°C is above {on_t:.0f}°C"
        if c.get("during_ai", True) and self.ai_busy():
            return True, "AI summary running"
        if self.on and (temp is None or temp > off_t):
            return True, "cooling down"
        return False, "cool"

    def tick(self) -> None:
        with self._lock:
            c = self.cfg.data["fan"]
            temp = self.read_temp()
            want, reason = self.decide(temp, c) if self._dev is not None else (False, "")
            if not want and self.on and self.clock() - self.on_since < float(c.get("min_on_seconds", 60)):
                want, reason = True, "minimum run time"
            self._set(want)
            self.status.update(temp=temp, fan_on=self.on, reason=reason)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Fan loop error")
            self._wake.wait(2)
            self._wake.clear()


# --------------------------------------------------------------------------- idle power-off
def default_poweroff() -> None:
    subprocess.run(["sudo", "-n", "systemctl", "poweroff"], timeout=30)


class PowerManager(threading.Thread):
    def __init__(self, cfg: ConfigStore, svc: Service, poweroff=default_poweroff, uptime=read_uptime,
                 ai_busy=llm.is_busy, now=None):
        super().__init__(name="power", daemon=True)
        self.cfg, self.svc, self.poweroff, self.uptime, self.ai_busy = cfg, svc, poweroff, uptime, ai_busy
        self.now = now or (lambda: datetime.now().astimezone())
        self.status = {"enabled": False, "message": "Idle power-off is off (the Pi stays on).", "idle_minutes": 0}

    def _calendar_due_within(self, hours: float) -> bool:
        now = self.now()
        horizon = now + timedelta(hours=hours)
        for r in self.svc.db.all("SELECT due, end FROM cal_events WHERE status='pending' AND due IS NOT NULL"):
            try:
                due, end = datetime.fromisoformat(r["due"]), datetime.fromisoformat(r["end"])
            except ValueError:
                continue
            if end > now and due <= horizon:
                return True
        return False

    def evaluate(self) -> tuple[bool, str]:
        """(power off now?, plain-English status)."""
        p = self.cfg.data["power"]
        hours = float(p.get("idle_shutdown_hours", 0) or 0)
        idle_min = (time.time() - self.svc.last_activity) / 60
        self.status.update(enabled=hours > 0, idle_minutes=int(idle_min))
        if hours <= 0:
            return False, "Idle power-off is off (the Pi stays on)."
        if self.uptime() < float(p.get("min_uptime_minutes", 30)) * 60:
            return False, "Just started - staying on for a while."
        if self.ai_busy():
            return False, "An AI summary is running."
        if self.svc.held_count() or self.svc.status()["queued"]:
            return False, "Messages or printouts are waiting."
        keep = float(p.get("keep_awake_before_calendar_hours", 12))
        if self._calendar_due_within(keep):
            return False, f"A calendar reminder prints within {keep:g} hours."
        left = hours * 60 - idle_min
        if left > 0:
            return False, f"Will power off in about {int(left)} minutes if nothing happens."
        return True, f"Idle for {idle_min / 60:.1f} hours."

    def run(self) -> None:
        while not self.svc.stop.is_set():
            try:
                go, why = self.evaluate()
                self.status["message"] = why
                if go:
                    log.warning("Powering off: %s Wake it with the GPIO3 button or by re-plugging power.", why)
                    self.status["message"] = "Powering off..."
                    self.poweroff()
                    return
            except Exception:
                log.exception("Power manager error")
            self.svc.stop.wait(60)
