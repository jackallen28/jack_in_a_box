"""The direct-USB printer path, driven through a stand-in for pyusb that behaves like the real library."""
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import printer  # noqa: E402
from app.printer import PrinterError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class USBError(IOError):
    def __init__(self, msg="", errno=None):
        super().__init__(msg)
        self.errno = errno


class NoBackendError(ValueError):
    pass


class EP:
    bEndpointAddress = 0x01                       # OUT endpoint (direction bit clear)

    def __init__(self, fail=None):
        self.writes, self.fail = [], fail

    def write(self, data, timeout=None):
        if self.fail:
            raise self.fail
        self.writes.append(bytes(data))


class Dev:
    def __init__(self, ep, driver_active=True):
        self.ep, self.driver_active, self.detached, self.configured = ep, driver_active, False, False

    def is_kernel_driver_active(self, i):
        return self.driver_active

    def detach_kernel_driver(self, i):
        self.detached = True

    def set_configuration(self):
        self.configured = True

    def get_active_configuration(self):
        return {(0, 0): "interface0"}


def install_fake_usb(monkeypatch, dev=None, find_error=None):
    usb, core, util = types.ModuleType("usb"), types.ModuleType("usb.core"), types.ModuleType("usb.util")
    seen = {"find_kwargs": None, "disposed": 0}

    def find(**kw):
        seen["find_kwargs"] = kw
        if find_error:
            raise find_error
        return dev

    core.find, core.USBError, core.NoBackendError = find, USBError, NoBackendError
    util.ENDPOINT_OUT = 0x00
    util.endpoint_direction = lambda addr: addr & 0x80
    util.find_descriptor = lambda intf, custom_match: dev.ep if custom_match(dev.ep) else None
    util.dispose_resources = lambda d: seen.__setitem__("disposed", seen["disposed"] + 1)
    usb.core, usb.util = core, util
    for name, mod in (("usb", usb), ("usb.core", core), ("usb.util", util)):
        monkeypatch.setitem(sys.modules, name, mod)
    return seen


CFG = {"backend": "usb", "usb_vendor": "0x04b8", "usb_product": ""}


def test_prints_in_chunks_after_taking_the_printer_from_the_kernel(monkeypatch):
    ep = EP()
    dev = Dev(ep)
    seen = install_fake_usb(monkeypatch, dev)
    printer._usb_write(0x04B8, None, b"x" * 40000)
    assert [len(w) for w in ep.writes] == [16384, 16384, 7232]
    assert dev.detached and dev.configured and seen["disposed"] == 1
    assert seen["find_kwargs"] == {"idVendor": 0x04B8}                      # any Epson, no product id needed


def test_product_id_is_used_when_given(monkeypatch):
    seen = install_fake_usb(monkeypatch, Dev(EP()))
    printer._usb_write(0x04B8, 0x0E15, b"abc")
    assert seen["find_kwargs"] == {"idVendor": 0x04B8, "idProduct": 0x0E15}


@pytest.mark.parametrize("make,expect", [
    (lambda m: install_fake_usb(m, None), "not found on USB"),
    (lambda m: install_fake_usb(m, Dev(EP()), find_error=NoBackendError("no backend")), "libusb"),
    (lambda m: install_fake_usb(m, Dev(EP(fail=USBError("Access denied (insufficient permissions)", 13)))), "permission"),
    (lambda m: install_fake_usb(m, Dev(EP(fail=USBError("Resource busy", 16)))), "busy"),
    (lambda m: install_fake_usb(m, Dev(EP(fail=USBError("Operation timed out", 110)))), "USB printing failed"),
])
def test_every_usb_failure_becomes_a_clear_printer_error(monkeypatch, make, expect):
    make(monkeypatch)
    with pytest.raises(PrinterError) as e:
        printer._usb_write(0x04B8, None, b"data")
    assert expect in str(e.value)


def test_missing_pyusb_says_how_to_fix_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "usb", None)                          # makes `import usb.core` fail
    with pytest.raises(PrinterError) as e:
        printer._usb_write(0x04B8, None, b"data")
    assert "pip install pyusb" in str(e.value)
    ok, msg = printer.check(CFG)
    assert not ok and "pyusb" in msg and "Check & update now" in msg


def test_check_reports_each_situation(monkeypatch):
    install_fake_usb(monkeypatch, None)
    ok, msg = printer.check(CFG)
    assert not ok and "No Epson printer found" in msg and "lsusb" in msg
    install_fake_usb(monkeypatch, Dev(EP()), find_error=NoBackendError("x"))
    assert "libusb-1.0-0" in printer.check(CFG)[1]
    install_fake_usb(monkeypatch, Dev(EP()))
    assert printer.check(CFG) == (True, "USB printer found (direct USB).")


def test_auto_mode_falls_back_to_direct_usb_when_there_is_no_lp_device(monkeypatch):
    """The exact situation on newer Pi OS: no /dev/usb/lp0, so 'automatic' must use pyusb and actually print."""
    monkeypatch.setattr(printer.glob, "glob", lambda pat: [])
    ep = EP()
    install_fake_usb(monkeypatch, Dev(ep))
    assert printer.resolve_backend({"backend": "auto"})[0] == "usb"
    printer.print_image(Image.new("1", (576, 200), 1), {"backend": "auto", "cut": "partial", "feed_lines": 3})
    data = b"".join(ep.writes)
    assert data.startswith(b"\x1b@\x1d\x76\x30") and data.endswith(b"\x1d\x56\x42\x18")


def test_pyusb_is_a_real_requirement_not_an_optional_extra():
    lines = [l.split("#")[0].strip() for l in (ROOT / "requirements.txt").read_text().splitlines()]
    assert any(l.lower().startswith("pyusb") for l in lines)


def test_lean_mode_leaves_existing_compressed_swap_alone(tmp_path):
    boot = tmp_path / "config.txt"
    boot.write_text("gpu_mem=128\n")
    base = {**os.environ, "JIAB_BOOT_CONFIG": str(boot), "JIAB_STATE_DIR": str(tmp_path / "st"), "JIAB_DRY_RUN": "1"}
    r = subprocess.run(["bash", str(ROOT / "deploy/lean-mode.sh")], env={**base, "JIAB_ZRAM_ACTIVE": "1"}, capture_output=True, text=True)
    assert r.returncode == 0 and "already active" in r.stdout and "would install zram-tools" not in r.stdout
    r = subprocess.run(["bash", str(ROOT / "deploy/lean-mode.sh")], env={**base, "JIAB_ZRAM_ACTIVE": "no"}, capture_output=True, text=True)
    assert r.returncode == 0 and "would install zram-tools" in r.stdout
