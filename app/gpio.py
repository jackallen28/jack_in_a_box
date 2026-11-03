"""Physical button + LED on the Pi's GPIO header.

* LED flashes while messages are waiting in the queue, off when there are none.
* Pressing the button releases every waiting message to the printer.

Wiring (BCM pin numbers, set on the Messages tab):
  button:  one side to the button pin (default GPIO17), the other side to any GND pin.
           The Pi's internal pull-up is used - no resistor needed.
  LED:     LED pin (default GPIO27) -> resistor -> LED (+) ... LED (-) -> GND.
           For a 3.3 V LED a ~330 ohm resistor is right.  Illuminated arcade buttons are often
           5 V or 12 V: do NOT connect those straight to a GPIO pin - switch them with a
           transistor/MOSFET (see README).
Uses ``gpiozero`` (pre-installed on Raspberry Pi OS).  On anything that isn't a Pi the controller
simply reports that GPIO isn't available.
"""
from __future__ import annotations

import logging
import threading

from .config import ConfigStore
from .service import Service

log = logging.getLogger("button")


class ButtonController:
    def __init__(self, cfg: ConfigStore, svc: Service):
        self.cfg, self.svc = cfg, svc
        self._lock = threading.RLock()
        self._button = None
        self._led = None
        self._blinking = False
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.status = {"state": "disabled", "message": ""}

    # ----------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.reconfigure()
        self._thread = threading.Thread(target=self._loop, name="button-led", daemon=True)
        self._thread.start()

    def _close(self) -> None:
        for dev in (self._button, self._led):
            try:
                if dev is not None:
                    dev.close()
            except Exception:
                pass
        self._button = self._led = None
        self._blinking = False

    def reconfigure(self) -> None:
        """(Re)open the pins from the current config.  Called at start and after every save."""
        with self._lock:
            self._close()
            c = self.cfg.data["button"]
            if not c.get("enabled"):
                self.status = {"state": "disabled", "message": "Button is turned off in settings."}
                return
            try:
                from gpiozero import LED, Button  # type: ignore
                self._button = Button(int(c["button_pin"]), pull_up=True, bounce_time=0.08)
                self._button.when_pressed = self._pressed
                if c.get("led_pin") not in (None, "", 0):
                    self._led = LED(int(c["led_pin"]), active_high=bool(c.get("led_active_high", True)))
                self.status = {"state": "ok", "message": f"Button on GPIO{c['button_pin']}, LED on GPIO{c.get('led_pin')}."}
                log.info(self.status["message"])
            except Exception as e:     # not a Pi, pins busy, bad pin number, no permission ...
                self._close()
                self.status = {"state": "error", "message": f"GPIO not available: {e}"}
                log.warning("GPIO setup failed: %s", e)
            self._wake.set()

    # ----------------------------------------------------------------- behaviour
    def _pressed(self) -> None:
        n = self.svc.release_held()
        log.info("Button pressed - %s", f"printing {n} waiting message(s)" if n else "no messages waiting")
        self._wake.set()

    def press(self) -> int:
        """Same as the physical button (used by the 'Print waiting messages' button on the web page)."""
        n = self.svc.release_held()
        self._wake.set()
        return n

    def _loop(self) -> None:
        while not self.svc.stop.is_set():
            try:
                self._update_led()
            except Exception:
                log.exception("LED update failed")
            self._wake.wait(0.5)
            self._wake.clear()

    def _update_led(self) -> None:
        with self._lock:
            if self._led is None:
                return
            waiting = self.svc.held_count() > 0
            if waiting and not self._blinking:
                t = float(self.cfg.data["button"].get("blink_seconds", 0.5) or 0.5)
                self._led.blink(on_time=t, off_time=t)
                self._blinking = True
            elif not waiting and self._blinking:
                self._led.off()
                self._blinking = False

    def test_led(self, seconds: float = 3.0) -> bool:
        with self._lock:
            if self._led is None:
                return False
            self._led.blink(on_time=0.15, off_time=0.15, n=int(seconds / 0.3))
            self._blinking = False      # let the loop restore the right state afterwards
            return True
