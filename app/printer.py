"""ESC/POS output for the Epson TM-T82II (and most other Epson-compatible printers).

The card is already a finished 1-bit bitmap, so the only commands needed are
init, raster image (GS v 0), feed and cut.  Transport options:

  file     /dev/usb/lp0 - the Linux ``usblp`` driver; simplest for USB, no extras
  usb      talk to the USB device directly with pyusb (needs ``pip install pyusb``)
  network  raw TCP port 9100 (TM-T82II models with the Ethernet board)
  dummy    write PNGs to data/dummy/ instead of printing (for testing)
  auto     USB (/dev/usb/lp*) if present, else the network IP if one is set, else pyusb
"""
from __future__ import annotations

import glob
import os
import socket
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from .config import DATA_DIR

ESC_INIT = b"\x1b@"
BAND_ROWS = 256


class PrinterError(Exception):
    pass


def encode(img: Image.Image, cut: str = "partial", feed_lines: int = 3) -> bytes:
    """1-bit image -> ESC/POS bytes."""
    img = img.convert("L")
    if cut == "line":  # no cutter: add a dashed tear-off line under the card
        torn = Image.new("L", (img.width, img.height + 40), 255)
        torn.paste(img, (0, 0))
        for x in range(0, img.width, 16):
            torn.paste(0, (x, img.height + 28, x + 8, img.height + 31))
        img = torn
    if img.width % 8:
        padded = Image.new("L", (img.width + 8 - img.width % 8, img.height), 255)
        padded.paste(img, (0, 0))
        img = padded
    inv = ImageOps.invert(img).convert("1")        # ESC/POS: 1 = black dot
    wb = img.width // 8
    out = bytearray(ESC_INIT)
    for y in range(0, img.height, BAND_ROWS):
        band = inv.crop((0, y, img.width, min(img.height, y + BAND_ROWS)))
        h = band.height
        out += b"\x1d\x76\x30\x00" + bytes([wb & 255, wb >> 8, h & 255, h >> 8]) + band.tobytes()
    if cut in ("none", "line"):
        out += b"\n" * max(1, feed_lines)
    else:
        mode = b"\x41" if cut == "full" else b"\x42"   # GS V 65/66 n: feed then cut
        out += b"\x1d\x56" + mode + bytes([max(0, min(255, feed_lines * 8))])
    return bytes(out)


def _usb_write(vendor: int, product: int | None, data: bytes) -> None:
    try:
        import usb.core  # type: ignore
        import usb.util  # type: ignore
    except ImportError as e:
        raise PrinterError("pyusb is not installed (pip install pyusb), or use backend 'file'") from e
    kw = {"idVendor": vendor}
    if product:
        kw["idProduct"] = product
    dev = usb.core.find(**kw)
    if dev is None:
        raise PrinterError("Printer not found on USB - is it powered on and plugged in?")
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
    if ep is None:
        raise PrinterError("No OUT endpoint on the printer")
    for i in range(0, len(data), 16384):
        ep.write(data[i:i + 16384], timeout=10000)
    usb.util.dispose_resources(dev)


def _int(v, default=None):
    try:
        return int(str(v), 0)
    except (TypeError, ValueError):
        return default


def resolve_backend(cfg: dict) -> tuple[str, str]:
    """Pick the connection. "auto" prefers a USB printer, then the network IP, then raw USB."""
    backend = cfg.get("backend", "auto")
    device = cfg.get("device") or "/dev/usb/lp0"
    if backend == "auto":
        lp = sorted(glob.glob("/dev/usb/lp*"))
        if lp:
            return "file", lp[0]
        if cfg.get("host"):
            return "network", device
        return "usb", device
    return backend, device


def check(cfg: dict) -> tuple[bool, str]:
    """Is the printer reachable?  (Does not print.)"""
    backend, device = resolve_backend(cfg)
    if backend == "dummy":
        return True, "Test mode: nothing is sent to a printer."
    if backend == "file":
        if not Path(device).exists():
            return False, f"{device} not found. Is the printer on and plugged into the Pi's USB port? (`ls /dev/usb`)"
        if not os.access(device, os.W_OK):
            return False, f"{device} exists but this user can't write to it. Add the user to the 'lp' group and reboot."
        return True, f"USB printer found at {device}."
    if backend == "network":
        host, port = cfg.get("host", ""), int(cfg.get("port", 9100) or 9100)
        if not host:
            return False, "No printer IP address set (and no USB printer found)."
        try:
            socket.create_connection((host, port), timeout=3).close()
        except OSError as e:
            return False, f"Can't reach {host}:{port} ({e}). Check the cable, the printer's IP, and the Pi's Ethernet setup."
        return True, f"Network printer answered at {host}:{port}."
    if backend == "usb":
        try:
            import usb.core  # type: ignore
        except ImportError:
            return False, "pyusb isn't installed and no /dev/usb/lp0 exists. Use the 'USB device file' option or pip install pyusb."
        dev = usb.core.find(idVendor=_int(cfg.get("usb_vendor"), 0x04B8))
        return (True, "USB printer found.") if dev else (False, "No Epson USB printer found.")
    return False, f"Unknown backend {backend!r}"


def send(data: bytes, img: Image.Image, cfg: dict) -> None:
    backend, device = resolve_backend(cfg)
    if backend == "dummy":
        d = DATA_DIR / "dummy"
        d.mkdir(parents=True, exist_ok=True)
        img.save(d / f"{datetime.now():%Y%m%d-%H%M%S-%f}.png")
        return
    try:
        if backend == "file":
            dev = device
            if not Path(dev).exists():
                raise PrinterError(f"{dev} not found - is the printer on and plugged in? "
                                   f"(check `ls /dev/usb`)")
            with open(dev, "wb", buffering=0) as f:
                for i in range(0, len(data), 4096):
                    f.write(data[i:i + 4096])
                    time.sleep(0.002)
        elif backend == "network":
            if not cfg.get("host"):
                raise PrinterError("No printer IP address set")
            with socket.create_connection((cfg["host"], int(cfg.get("port", 9100))), timeout=8) as s:
                s.sendall(data)
        elif backend == "usb":
            _usb_write(_int(cfg.get("usb_vendor"), 0x04B8), _int(cfg.get("usb_product")), data)
        else:
            raise PrinterError(f"Unknown printer backend {backend!r}")
    except PrinterError:
        raise
    except PermissionError as e:
        raise PrinterError("Permission denied talking to the printer - add the user to the 'lp' group "
                           "(see README) and reboot") from e
    except OSError as e:
        raise PrinterError(f"Could not reach the printer: {e}") from e


def print_image(img: Image.Image, cfg: dict) -> None:
    send(encode(img, cfg.get("cut", "partial"), int(cfg.get("feed_lines", 3))), img, cfg)
