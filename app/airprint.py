"""Share the receipt printer on your network as an AirPrint / IPP Everywhere printer.

iPhones, iPads, Macs, Windows 10/11 and Android find it by itself ("Jack in a Box" in the print dialog) and print to it like any
other printer.  There is no CUPS and no driver: this app speaks IPP itself and announces the printer over Bonjour (mDNS).  Whatever
the device sends - PDF, JPEG/PNG, Apple URF or PWG raster, plain text - is turned into pages 72 mm wide and put on the SAME print
queue as everything else, so it shares the printer properly with emails and the Google Form.

  * the page is trimmed of blank space (top and bottom, and equal margins left and right) and scaled to the paper width
  * text and line art are thresholded, photos are dithered (automatic; or force one in the settings)
  * each page is one printout; the history shows them with a "airprint" source and you can Cancel / reprint as usual
  * only devices on your own network (private addresses) can print, unless you allow otherwise; page, size and queue limits apply
"""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import logging
import re
import socket
import socketserver
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps

from . import features, ipp
from .config import ConfigStore
from .rasterfmt import RasterError, read_pwg, read_urf
from .render import _dither, mm, pt, text_block
from .service import Service

log = logging.getLogger("airprint")

IPP_PATHS = ("/", "/ipp", "/ipp/print", "/ipp/print/")
RAW_FORMAT = "application/vnd.cups-raw"        # what CUPS (Mac/Linux) calls "printer data from the driver, send it untouched"
RAW_IDLE_SECONDS = 4                           # a raw (port 9100) job is finished when the sender goes quiet this long (or closes)
FALLBACK_PORT = 6310
PRINTER_MODEL = "Epson TM-T82II receipt printer"
MEDIA_A6, MEDIA_4X6 = "iso_a6_105x148mm", "om_small-photo_100x150mm"
STATE_PENDING, STATE_HELD, STATE_PROCESSING, STATE_CANCELED, STATE_ABORTED, STATE_COMPLETED = 3, 4, 5, 7, 8, 9


# =========================================================================== settings
@dataclass
class Settings:
    enabled: bool = False
    name: str = "Jack in a Box"
    port: int = 631
    allow_any_network: bool = False
    hold_for_button: bool = False
    max_pages: int = 10
    max_copies: int = 5
    max_job_mb: int = 30
    max_queued: int = 20
    autocrop: str = "margins"          # none | vertical | margins
    dither: str = "auto"               # auto | text | photo
    max_height_mm: int = 700
    raw_enabled: bool = False          # let another computer use the printer's OWN driver (Epson's): its ESC/POS data is passed through untouched
    raw_port: int = 9100               # ...also accepted on this raw TCP port (like a network receipt printer)
    width_mm: float = 72
    width_dots: int = 576              # the printer's printable width (from the Printer settings)

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Settings":
        a = cfg.get("airprint", {})
        s = cls()
        for f in s.__dataclass_fields__:
            if f in a:
                try:
                    setattr(s, f, bool(a[f]) if isinstance(getattr(s, f), bool) else type(getattr(s, f))(a[f]))
                except (TypeError, ValueError):
                    pass                                         # a bad value in the settings file: keep the default
        s.port = max(0, min(65535, s.port))
        s.raw_port = max(0, min(65535, s.raw_port))
        s.autocrop = s.autocrop if s.autocrop in ("none", "vertical", "margins") else "margins"
        s.dither = s.dither if s.dither in ("auto", "text", "photo") else "auto"
        s.max_pages, s.max_copies = max(1, s.max_pages), max(1, s.max_copies)
        s.name = re.sub(r"\s+", " ", s.name).strip()[:60] or "Jack in a Box"
        s.enabled = s.enabled and features.on(cfg, "airprint")        # not chosen in the setup guide: never advertised
        s.width_mm = float(cfg.get("templates", {}).get("reminder", {}).get("width_mm", 72))
        s.width_dots = int(cfg.get("printer", {}).get("width_dots", 576))
        return s


def pdf_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
        return True
    except Exception:
        return False


def zeroconf_available() -> bool:
    try:
        import zeroconf  # noqa: F401
        return True
    except Exception:
        return False


def supported_formats(raw: bool = False) -> list[str]:
    return (["application/pdf"] if pdf_available() else []) + ["image/jpeg", "image/png", "image/urf", "image/pwg-raster", "text/plain",
                                                                "application/octet-stream"] + ([RAW_FORMAT] if raw else [])


def looks_like_escpos(data: bytes) -> bool:
    """Printer data starts with an ESC / GS / DLE command ("ESC @" = initialise) - that is what Epson's driver sends."""
    return data.lstrip(b"\x00\r\n")[:1] in (b"\x1b", b"\x1d", b"\x10")


def is_raw(data: bytes, declared: str) -> bool:
    """Is this printer data from the printer's own driver rather than a document for us to draw?  Only when you've allowed it."""
    if declared == RAW_FORMAT:
        return True
    if declared != "application/octet-stream":
        return False
    return looks_like_escpos(data) or sniff(data, declared) == "application/octet-stream"       # unknown binary: the driver's data


def is_allowed(ip: str, st: Settings) -> bool:
    """Only machines on your own network (private / link-local / loopback addresses) unless you allow any."""
    if st.allow_any_network:
        return True
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        if getattr(a, "ipv4_mapped", None):
            a = a.ipv4_mapped
        return a.is_private or a.is_loopback or a.is_link_local
    except ValueError:
        return False


# =========================================================================== documents -> pages
class DocError(Exception):
    def __init__(self, message: str, status: int = ipp.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def sniff(data: bytes, declared: str) -> str:
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:8] == b"UNIRAST\x00":
        return "image/urf"
    if data[:4] in (b"RaS2", b"RaS3", b"RaSt"):
        return "image/pwg-raster"
    if data[:3] == b"GIF" or data[:2] == b"BM" or data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/png"                                   # Pillow reads these too
    if declared == "text/plain" or (declared == "application/octet-stream" and _looks_like_text(data)):
        return "text/plain"
    return declared


def _looks_like_text(data: bytes) -> bool:
    head = data[:2000]
    return bool(head) and b"\x00" not in head and sum(c < 9 or 13 < c < 32 for c in head) < len(head) * 0.02


def _pdf_pages(data: bytes, max_pages: int) -> list[Image.Image]:
    if not pdf_available():
        raise DocError("PDF printing needs the 'pypdfium2' package (pip install pypdfium2)", ipp.FORMAT_NOT_SUPPORTED)
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as e:
        raise DocError(f"unreadable PDF ({type(e).__name__})") from e
    out = []
    try:
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            w, h = page.get_size()
            scale = max(0.5, min(4.0, 1152 / max(w, 1)))                  # about 1152 px wide, then scaled down: crisp text
            while w * scale * h * scale > 40_000_000 and scale > 0.3:
                scale *= 0.7
            out.append(page.render(scale=scale).to_pil().convert("RGB"))
            page.close()
    except DocError:
        raise
    except Exception as e:
        raise DocError(f"couldn't draw the PDF ({type(e).__name__})") from e
    finally:
        pdf.close()
    return out


def load_pages(data: bytes, declared: str, st: Settings) -> list[Image.Image]:
    """The document the device sent -> a list of page images (RGB or L)."""
    kind = sniff(data, declared)
    try:
        if kind == "application/pdf":
            return _pdf_pages(data, st.max_pages)
        if kind == "image/urf":
            return read_urf(data, st.max_pages)
        if kind == "image/pwg-raster":
            return read_pwg(data, st.max_pages)
        if kind == "text/plain":
            body = data[:200_000].decode("utf-8", "replace").replace("\t", "    ")
            return [text_block(body or " ", "courier-prime", pt(10), st.width_dots - 8)]
        if kind in ("image/jpeg", "image/png", "image/gif", "image/bmp", "image/tiff"):
            im = Image.open(io.BytesIO(data))
            im.load()
            return [ImageOps.exif_transpose(im)]
    except RasterError as e:
        raise DocError(f"bad raster data: {e}") from e
    except DocError:
        raise
    except Exception as e:
        raise DocError(f"couldn't read the {kind} document ({type(e).__name__})") from e
    raise DocError(f"unsupported document type {declared}", ipp.FORMAT_NOT_SUPPORTED)


def ticket(im: Image.Image, st: Settings) -> Image.Image | None:
    """One page -> a 1-bit image as wide as the paper (None for a blank page)."""
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, "white")
        bg.alpha_composite(im)
        im = bg
    g = im.convert("L")
    box = ImageOps.invert(g).point(lambda p: 255 if p > 12 else 0).getbbox()       # anything darker than ~95% white
    if box is None:
        return None
    x0, y0, x1, y1 = box
    pad = max(6, g.width // 60)
    top, bottom, left, right = 0, g.height, 0, g.width
    if st.autocrop in ("vertical", "margins"):
        top, bottom = max(0, y0 - pad), min(g.height, y1 + pad)
    if st.autocrop == "margins":
        m = max(0, min(x0, g.width - x1) - pad)                                    # equal margins, so layout stays put
        left, right = m, g.width - m
    g = g.crop((left, top, right, bottom))
    target = st.width_dots
    scale = target / g.width
    cap = mm(st.max_height_mm)
    if g.height * scale > cap:
        g = g.crop((0, 0, g.width, max(1, int(cap / scale))))
    g = g.resize((target, max(1, round(g.height * scale))), Image.LANCZOS)
    mode = st.dither
    if mode == "auto":
        hist = g.histogram()
        extreme = (sum(hist[:61]) + sum(hist[200:])) / max(1, g.width * g.height)
        mode = "text" if extreme > 0.93 else "photo"
    out = _dither(g, "threshold" if mode == "text" else "floyd", 1.0 if mode == "text" else 1.1)
    return out.convert("1")


# =========================================================================== IPP printer description
def _media_col(w: int, h: int) -> list:
    return ipp.coll(media_bottom_margin=ipp.integer(0), media_left_margin=ipp.integer(0), media_right_margin=ipp.integer(0),
                    media_top_margin=ipp.integer(0), media_size=ipp.coll(x_dimension=ipp.integer(w), y_dimension=ipp.integer(h)),
                    media_source=ipp.kw("main"), media_type=ipp.kw("stationery"))


def printer_attributes(st: Settings, uri: str, info: dict) -> list[tuple[str, str, list]]:
    """(group, name, values) for Get-Printer-Attributes; group = 'desc' (printer-description) or 'tmpl' (job-template)."""
    formats = supported_formats(st.raw_enabled)
    sizes = ipp.merge(ipp.coll(x_dimension=ipp.integer(10000), y_dimension=ipp.integer(15000)),
                      ipp.coll(x_dimension=ipp.integer(10500), y_dimension=ipp.integer(14800)),
                      ipp.coll(x_dimension=ipp.integer(int(st.width_mm * 100)), y_dimension=ipp.rng(3000, 200000)))
    database = ipp.merge(_media_col(10000, 15000), _media_col(10500, 14800),
                         ipp.coll(media_bottom_margin=ipp.integer(0), media_left_margin=ipp.integer(0), media_right_margin=ipp.integer(0),
                                  media_top_margin=ipp.integer(0),
                                  media_size=ipp.coll(x_dimension=ipp.integer(int(st.width_mm * 100)), y_dimension=ipp.rng(3000, 200000)),
                                  media_source=ipp.kw("main"), media_type=ipp.kw("stationery")))
    accepting = info["queued"] < st.max_queued
    now = datetime.now(timezone.utc)
    A = []
    d = lambda n, v: A.append(("desc", n, v))
    t = lambda n, v: A.append(("tmpl", n, v))
    d("charset-configured", ipp.charset("utf-8"))
    d("charset-supported", ipp.charset("utf-8"))
    d("natural-language-configured", ipp.lang("en"))
    d("generated-natural-language-supported", ipp.lang("en"))
    d("ipp-versions-supported", ipp.kw("1.1", "2.0"))
    d("ipp-features-supported", ipp.kw("ipp-everywhere", "airprint-1.4"))
    d("operations-supported", ipp.enum(ipp.PRINT_JOB, ipp.VALIDATE_JOB, ipp.CREATE_JOB, ipp.SEND_DOCUMENT, ipp.CANCEL_JOB,
                                       ipp.GET_JOB_ATTRIBUTES, ipp.GET_JOBS, ipp.GET_PRINTER_ATTRIBUTES))
    d("printer-uri-supported", ipp.uri(uri))
    d("uri-security-supported", ipp.kw("none"))
    d("uri-authentication-supported", ipp.kw("none"))
    d("printer-name", ipp.name(st.name))
    d("printer-info", ipp.text(st.name))
    d("printer-location", ipp.text(""))
    d("printer-make-and-model", ipp.text(PRINTER_MODEL))
    d("printer-device-id", ipp.text("MFG:EPSON;CMD:ESCPOS;MDL:TM-T82II;CLS:PRINTER;"))
    d("printer-uuid", ipp.uri(f"urn:uuid:{info['uuid']}"))
    d("printer-more-info", ipp.uri(info["admin_url"]))
    d("printer-state", ipp.enum(3))
    d("printer-state-reasons", ipp.kw("none"))
    d("printer-is-accepting-jobs", ipp.boolean(accepting))
    d("queued-job-count", ipp.integer(info["queued"]))
    d("printer-up-time", ipp.integer(int(time.time() - info["started"]) + 1))
    d("printer-current-time", ipp.when(now))
    d("pdl-override-supported", ipp.kw("attempted"))
    d("compression-supported", ipp.kw("none"))
    d("document-format-supported", ipp.mime(*formats))
    d("document-format-default", ipp.mime("application/octet-stream"))
    d("document-format-preferred", ipp.mime("application/pdf" if "application/pdf" in formats else "image/jpeg"))
    d("multiple-document-jobs-supported", ipp.boolean(False))
    d("multiple-operation-time-out", ipp.integer(60))
    d("color-supported", ipp.boolean(False))
    d("job-creation-attributes-supported", ipp.kw("copies", "document-format", "job-name", "media", "media-col", "orientation-requested",
                                                  "print-color-mode", "print-quality", "printer-resolution", "sides"))
    d("job-ids-supported", ipp.boolean(True))
    d("job-k-octets-supported", ipp.rng(0, st.max_job_mb * 1024))
    d("job-priority-supported", ipp.integer(1))
    d("job-hold-until-supported", ipp.kw("no-hold"))
    d("job-sheets-supported", ipp.kw("none"))
    d("which-jobs-supported", ipp.kw("completed", "not-completed"))
    d("media-col-database", database)
    d("media-col-supported", ipp.kw("media-bottom-margin", "media-left-margin", "media-right-margin", "media-size", "media-source",
                                    "media-top-margin", "media-type"))
    d("printer-resolution-supported", ipp.resolution(300, 300))
    d("print-quality-supported", ipp.enum(4))
    d("urf-supported", ipp.kw("CP1", "W8", "RS300", "V1.4"))
    d("pwg-raster-document-resolution-supported", ipp.resolution(300, 300))
    d("pwg-raster-document-sheet-back", ipp.kw("normal"))
    d("pwg-raster-document-type-supported", ipp.kw("sgray_8", "srgb_8", "black_1"))
    d("printer-kind", ipp.kw("document", "receipt"))
    t("copies-supported", ipp.rng(1, st.max_copies))
    t("copies-default", ipp.integer(1))
    t("sides-supported", ipp.kw("one-sided"))
    t("sides-default", ipp.kw("one-sided"))
    t("orientation-requested-supported", ipp.enum(3, 4, 5, 6))
    t("orientation-requested-default", ipp.enum(3))
    t("print-quality-default", ipp.enum(4))
    t("print-color-mode-supported", ipp.kw("monochrome"))
    t("print-color-mode-default", ipp.kw("monochrome"))
    t("printer-resolution-default", ipp.resolution(300, 300))
    t("media-supported", ipp.kw(MEDIA_4X6, MEDIA_A6, f"custom_min_{int(st.width_mm)}x30mm", f"custom_max_{int(st.width_mm)}x2000mm"))
    t("media-default", ipp.kw(MEDIA_4X6))
    t("media-ready", ipp.kw(MEDIA_4X6))
    t("media-col-default", _media_col(10000, 15000))
    t("media-col-ready", _media_col(10000, 15000))
    t("media-size-supported", sizes)
    t("media-bottom-margin-supported", ipp.integer(0))
    t("media-left-margin-supported", ipp.integer(0))
    t("media-right-margin-supported", ipp.integer(0))
    t("media-top-margin-supported", ipp.integer(0))
    t("media-source-supported", ipp.kw("main"))
    t("media-type-supported", ipp.kw("stationery"))
    t("finishings-supported", ipp.enum(3))
    t("finishings-default", ipp.enum(3))
    t("number-up-supported", ipp.integer(1))
    t("number-up-default", ipp.integer(1))
    t("page-ranges-supported", ipp.boolean(False))
    t("job-priority-default", ipp.integer(1))
    t("output-bin-supported", ipp.kw("face-up"))
    t("output-bin-default", ipp.kw("face-up"))
    return A


def txt_record(st: Settings, info: dict) -> dict[str, str]:
    """The Bonjour TXT record AirPrint clients read before they ever talk to the printer."""
    return {
        "txtvers": "1", "qtotal": "1", "rp": "ipp/print", "ty": f"{st.name}", "product": "(TM-T82II)", "note": "Receipt printer",
        "adminurl": info["admin_url"], "priority": "0", "usb_MFG": "EPSON", "usb_MDL": "TM-T82II",
        "pdl": ",".join(supported_formats(st.raw_enabled)), "URF": "CP1,W8,RS300,V1.4", "Color": "F", "Duplex": "F", "Copies": "T", "Collate": "F",
        "Scan": "F", "Fax": "F", "Binary": "T", "Transparent": "T", "TBCP": "F", "Staple": "F", "Punch": "F", "Sort": "F",
        "kind": "document", "PaperMax": "<legal-A4", "UUID": info["uuid"], "air": "none", "printer-state": "3", "printer-type": "0x809046",
    }


def lan_addresses(exclude_net: str = "") -> list[str]:
    """This machine's usable IPv4 addresses (not loopback / link-local; not the direct cable to the printer)."""
    out: list[str] = []
    try:
        import ifaddr
        for ad in ifaddr.get_adapters():
            for ip in ad.ips:
                if ip.is_IPv4:
                    a = ipaddress.ip_address(ip.ip)
                    if a.is_loopback or a.is_link_local or (exclude_net and a in ipaddress.ip_network(exclude_net, strict=False)):
                        continue
                    out.append(str(a))
    except Exception:
        pass
    if not out:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                out.append(s.getsockname()[0])
        except OSError:
            pass
    return out


# =========================================================================== Bonjour
def build_services(st: Settings, port: int, addresses: list[str], info: dict):
    """The Bonjour records: the printer (registered with the 'universal' AirPrint subtype, which the library also announces
    as a plain _ipp._tcp service, so every kind of client finds it) and the legacy _printer placeholder AirPrint expects."""
    from zeroconf import ServiceInfo
    props = {k.encode(): v.encode() for k, v in txt_record(st, info).items()}
    addrs = [socket.inet_aton(a) for a in addresses]
    server = re.sub(r"[^a-z0-9-]+", "-", st.name.lower()).strip("-") or "jack-in-a-box"
    server = f"{server}-airprint.local."              # not the Pi's own hostname: avahi already owns that one
    inst = f"{st.name}._ipp._tcp.local."
    return [ServiceInfo("_universal._sub._ipp._tcp.local.", inst, port=port, properties=props, server=server, addresses=addrs),
            ServiceInfo("_printer._tcp.local.", f"{st.name}._printer._tcp.local.", port=0, properties=props, server=server, addresses=addrs)]


class Bonjour:
    def __init__(self):
        self.zc = None
        self.infos: list = []
        self.addresses: list[str] = []

    def start(self, st: Settings, port: int, addresses: list[str], info: dict) -> None:
        from zeroconf import IPVersion, Zeroconf
        self.stop()
        self.zc = Zeroconf(ip_version=IPVersion.V4Only)
        self.infos = build_services(st, port, addresses, info)
        self.addresses = list(addresses)
        for i in self.infos:
            self.zc.register_service(i, allow_name_change=True)

    def stop(self) -> None:
        zc, self.zc = self.zc, None
        if zc is not None:
            try:
                for i in self.infos:
                    zc.unregister_service(i)
                zc.close()
            except Exception:
                log.debug("Bonjour shutdown", exc_info=True)
        self.infos = []


# =========================================================================== the print queue side
@dataclass
class IppJob:
    id: int
    name: str
    user: str
    created: float
    ids: list[int] = field(default_factory=list)      # our own job ids (the print queue)
    pages: int = 0
    copies: int = 1
    awaiting_document: bool = False                   # Create-Job done, Send-Document not yet
    canceled: bool = False
    k_octets: int = 0
    error: str = ""


class AirPrintService(threading.Thread):
    def __init__(self, cfg: ConfigStore, svc: Service):
        super().__init__(name="airprint", daemon=True)
        self.cfg, self.svc = cfg, svc
        self.status: dict = {"state": "disabled", "message": "AirPrint is off.", "port": None, "url": "", "name": "", "advertising": False,
                             "jobs": 0, "last_job": "", "address": "", "warnings": []}
        self._httpd: ThreadingHTTPServer | None = None
        self._rawd: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None
        self._bonjour = Bonjour()
        self._sig: tuple | None = None
        self._addr_check = 0.0
        self._retry_at = 0.0
        self.started = time.time()
        self._jobs: dict[int, IppJob] = {}
        self._next_job = 1
        self._lock = threading.Lock()
        self.uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{socket.gethostname()}.jack-in-a-box.airprint"))
        self.wake = threading.Event()

    # ------------------------------------------------------------------ lifecycle
    def settings(self) -> Settings:
        return Settings.from_cfg(self.cfg.data)

    def run(self) -> None:
        while not self.svc.stop.is_set():
            try:
                self.reconcile()
            except Exception:
                log.exception("AirPrint manager error")
            self.wake.wait(5)
            self.wake.clear()
        self.shutdown()

    def reconcile(self) -> None:
        st = self.settings()
        sig = (st.port, st.name, st.raw_enabled, st.raw_port)
        if st.enabled and (sig != self._sig or (self._httpd is None and time.time() >= self._retry_at)):
            self.shutdown()
            self.start_server(st)
        elif not st.enabled and (self._httpd is not None or self._sig is not None):
            self.shutdown()
            self._sig = None
            self.status.update(state="disabled", message="AirPrint is off.", advertising=False, url="", port=None)
        elif st.enabled and self._httpd is not None and time.time() - self._addr_check > 60:
            self.refresh_addresses(st)                       # Wi-Fi/Ethernet changed? announce the new address

    def start_server(self, st: Settings) -> None:
        warnings: list[str] = []
        sig_now = (st.port, st.name, st.raw_enabled, st.raw_port)
        handler = self._handler_class()
        httpd = None
        ports = [st.port] + ([FALLBACK_PORT] if st.port == 631 else [])
        err = ""
        for p in ports:
            try:
                httpd = ThreadingHTTPServer(("0.0.0.0", p), handler)
                httpd.daemon_threads = True
                if p != st.port:
                    warnings.append(f"Port {st.port} wasn't available, so port {p} is used instead (clients find it automatically).")
                break
            except OSError as e:
                err = f"{e.strerror or e}"
        if httpd is None:
            self.status.update(state="error", message=f"Couldn't listen on port {st.port}: {err}", advertising=False)
            self._sig = sig_now
            self._retry_at = time.time() + 60                # try again in a minute (or at once if you change the settings)
            return
        self._httpd = httpd
        self._sig = sig_now
        self._thread = threading.Thread(target=httpd.serve_forever, name="airprint-http", daemon=True)
        self._thread.start()
        port = httpd.server_address[1]
        self.status.update(state="ok", port=port, name=st.name, warnings=warnings, raw_port=None)
        if st.raw_enabled:
            self.start_raw(st, warnings)
        self.refresh_addresses(st)
        log.info("AirPrint: '%s' listening on port %d", st.name, port)

    def refresh_addresses(self, st: Settings) -> None:
        self._addr_check = time.time()
        port = self._httpd.server_address[1] if self._httpd else st.port
        printer_net = ""
        pc = self.cfg.data.get("printer", {})
        if pc.get("backend") == "network" and pc.get("host"):
            printer_net = f"{pc['host']}/24"
        addrs = lan_addresses(printer_net)
        host = addrs[0] if addrs else "127.0.0.1"
        self.status.update(address=host, url=f"ipp://{host}:{port}/ipp/print")
        warnings = [w for w in self.status.get("warnings", []) if not w.startswith("Bonjour")]
        if not zeroconf_available():
            warnings.append("Bonjour announcements need the 'zeroconf' package (pip install zeroconf), so devices won't find the printer by themselves. "
                            "You can still add it by address.")
            self.status.update(advertising=False, warnings=warnings, message=f"Listening on {self.status['url']} (not announced).")
            return
        if self._bonjour.zc is not None and sorted(self._bonjour.addresses) == sorted(addrs):
            return
        try:
            self._bonjour.start(st, port, addrs, self.info())
            self.status.update(advertising=True, warnings=warnings,
                               message=f"Shared as “{st.name}” on your network ({self.status['url']}).")
        except Exception as e:
            log.warning("Bonjour announcement failed: %s", e)
            warnings.append(f"Bonjour announcement failed ({type(e).__name__}: {e}). You can still add the printer by address.")
            self.status.update(advertising=False, warnings=warnings, message=f"Listening on {self.status['url']} (not announced).")

    def start_raw(self, st: Settings, warnings: list[str]) -> None:
        """The raw TCP port (9100): drivers that print to "a network receipt printer" send their data here."""
        service = self

        class RawHandler(socketserver.BaseRequestHandler):
            def handle(self):
                ip = self.client_address[0]
                cur = service.settings()
                if not is_allowed(ip, cur):
                    log.warning("AirPrint: refused raw printer data from %s (not on your network)", ip)
                    return
                data, limit = bytearray(), cur.max_job_mb * 1024 * 1024
                self.request.settimeout(30)
                try:
                    while True:
                        chunk = self.request.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                        if len(data) > limit:
                            log.warning("AirPrint: raw job from %s is too big, ignored", ip)
                            return
                        self.request.settimeout(RAW_IDLE_SECONDS)             # quiet for a few seconds = the job is complete
                except (socket.timeout, OSError):
                    pass
                if data and service.queued_count() < cur.max_queued:
                    service.svc.submit_raw(bytes(data), f"Print job from {ip}", source="driver", hold=cur.hold_for_button)
                    service.status.update(jobs=service.status["jobs"] + 1, last_job=f"printer driver data ({len(data) // 1024 + 1} KB) from {ip}")
                    log.info("AirPrint: raw printer data (%d bytes) from %s", len(data), ip)

        class RawServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        try:
            self._rawd = RawServer(("0.0.0.0", st.raw_port), RawHandler)
        except OSError as e:
            warnings.append(f"Couldn't open the raw printer port {st.raw_port}: {e.strerror or e}. The driver route through the print dialog (IPP) still works.")
            self._rawd = None
            return
        threading.Thread(target=self._rawd.serve_forever, name="airprint-raw", daemon=True).start()
        self.status["raw_port"] = self._rawd.server_address[1]
        log.info("AirPrint: raw printer port %d open", self.status["raw_port"])

    def shutdown(self) -> None:
        raw, self._rawd = self._rawd, None
        if raw is not None:
            raw.shutdown()
            raw.server_close()
        self._bonjour.stop()
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            log.info("AirPrint stopped")

    # ------------------------------------------------------------------ the HTTP/IPP server
    def info(self) -> dict:
        return {"uuid": self.uuid, "started": self.started, "queued": self.queued_count(), "admin_url": self.admin_url()}

    def admin_url(self) -> str:
        return f"http://{self.status.get('address') or '127.0.0.1'}:{self.cfg.data['web'].get('port', 8080)}/"

    def queued_count(self) -> int:
        return self.svc.db.one("SELECT COUNT(*) n FROM jobs WHERE source='airprint' AND status IN ('pending','held')")["n"]

    def _handler_class(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "JackInABox-IPP/1.0"

            def log_message(self, fmt, *args):
                log.debug("%s %s", self.client_address[0], fmt % args)

            def _send(self, code: int, body: bytes, ctype: str, close: bool = False) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                if close:
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _body(self, limit: int) -> bytes | None:
                if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                    data = bytearray()
                    while True:
                        line = self.rfile.readline(100).strip()
                        try:
                            size = int(line.split(b";")[0] or b"0", 16)
                        except ValueError:
                            return None
                        if size == 0:
                            while self.rfile.readline(200).strip():          # trailers
                                pass
                            return bytes(data)
                        if len(data) + size > limit:
                            return None
                        chunk = self.rfile.read(size)
                        if len(chunk) != size:
                            return None
                        data += chunk
                        self.rfile.readline(4)                              # the CRLF after each chunk
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return None
                if n < 0 or n > limit:
                    return None
                data = self.rfile.read(n)
                return data if len(data) == n else None

            def do_GET(self):
                st = service.settings()
                if not is_allowed(self.client_address[0], st):
                    return self._send(403, b"Forbidden", "text/plain", True)
                if self.path.split("?")[0] in IPP_PATHS:
                    page = (f"<html><head><title>{st.name}</title></head><body><h1>{st.name}</h1><p>This is an AirPrint / IPP printer "
                            f"(Epson receipt printer). Add it by choosing this address in your print dialog: "
                            f"<code>ipp://{self.headers.get('Host', '')}/ipp/print</code></p></body></html>")
                    return self._send(200, page.encode(), "text/html; charset=utf-8")
                self._send(404, b"Not found", "text/plain")

            def do_POST(self):
                st = service.settings()
                if not is_allowed(self.client_address[0], st):
                    log.warning("AirPrint: refused a print request from %s (not on your network)", self.client_address[0])
                    return self._send(403, b"Forbidden", "text/plain", True)
                if self.path.split("?")[0] not in IPP_PATHS:
                    return self._send(404, b"Not found", "text/plain", True)
                if "application/ipp" not in self.headers.get("Content-Type", ""):
                    return self._send(415, b"Expected application/ipp", "text/plain", True)
                body = self._body((st.max_job_mb + 1) * 1024 * 1024)
                if body is None:
                    return self._send(413, b"Request too large or malformed", "text/plain", True)
                try:
                    out = service.handle(body, self.client_address[0], self.headers.get("Host") or "")
                except Exception:
                    log.exception("AirPrint request failed")
                    out = ipp.build_response(ipp.INTERNAL_ERROR, 0, [(ipp.G_OPERATION, service._op_attrs("internal error"))])
                self._send(200, out, "application/ipp")

        return Handler

    # ------------------------------------------------------------------ IPP operations
    @staticmethod
    def _op_attrs(message: str = "") -> list:
        a = [("attributes-charset", ipp.charset("utf-8")), ("attributes-natural-language", ipp.lang("en"))]
        if message:
            a.append(("status-message", ipp.text(message)))
        return a

    def _error(self, status: int, rid: int, message: str, version=(2, 0)) -> bytes:
        return ipp.build_response(status, rid, [(ipp.G_OPERATION, self._op_attrs(message))], version)

    def handle(self, body: bytes, client: str, host: str = "") -> bytes:
        st = self.settings()
        try:
            req = ipp.parse_request(body)
        except ipp.IPPError as e:
            return self._error(ipp.BAD_REQUEST, 0, f"malformed request: {e}")
        version = (2, 0) if req.version[0] >= 2 else (1, 1)
        if req.version[0] > 2:
            return self._error(ipp.VERSION_NOT_SUPPORTED, req.request_id, "unsupported IPP version")
        self.svc.touch()
        port = self._httpd.server_address[1] if self._httpd else st.port
        netloc = host or f"{self.status.get('address') or '127.0.0.1'}:{port}"
        uri = f"ipp://{netloc}/ipp/print"
        op = req.operation
        if op == ipp.GET_PRINTER_ATTRIBUTES:
            return self._get_printer(req, st, uri, version)
        if op in (ipp.PRINT_JOB, ipp.VALIDATE_JOB, ipp.CREATE_JOB, ipp.SEND_DOCUMENT):
            return self._submit(req, st, uri, version, client)
        if op == ipp.GET_JOBS:
            return self._get_jobs(req, uri, version)
        if op in (ipp.GET_JOB_ATTRIBUTES, ipp.CANCEL_JOB):
            return self._job_op(req, uri, version)
        return self._error(ipp.OPERATION_NOT_SUPPORTED, req.request_id, "operation not supported", version)

    # -- printer
    def _get_printer(self, req: ipp.Request, st: Settings, uri: str, version) -> bytes:
        wanted = set(req.get_all(ipp.G_OPERATION, "requested-attributes"))
        attrs = []
        for group, name, values in printer_attributes(st, uri, self.info()):
            if not wanted or "all" in wanted or name in wanted or (group == "desc" and "printer-description" in wanted) \
                    or (group == "tmpl" and "job-template" in wanted):
                attrs.append((name, values))
        return ipp.build_response(ipp.OK, req.request_id, [(ipp.G_OPERATION, self._op_attrs()), (ipp.G_PRINTER, attrs)], version)

    # -- jobs
    def job_state(self, job: IppJob) -> tuple[int, str]:
        if job.canceled:
            return STATE_CANCELED, "job-canceled-by-user"
        if job.awaiting_document:
            return STATE_HELD, "job-incoming"
        if job.error:
            return STATE_ABORTED, "document-format-error"
        if not job.ids:
            return STATE_COMPLETED, "job-completed-successfully"                  # a blank document: nothing to print
        rows = self.svc.db.all(f"SELECT status, error FROM jobs WHERE id IN ({','.join('?' * len(job.ids))})", job.ids)
        statuses = [r["status"] for r in rows]
        if any(s == "pending" for s in statuses):
            return (STATE_PROCESSING if "printed" in statuses else STATE_PENDING), "job-printing" if "printed" in statuses else "none"
        if any(s == "held" for s in statuses):
            return STATE_HELD, "job-hold-until-specified"
        if any(r["status"] == "failed" and r["error"] in ("Cancelled", "Discarded") for r in rows):
            return STATE_CANCELED, "job-canceled-by-user"
        if any(s == "failed" for s in statuses):
            return STATE_ABORTED, "job-aborted-by-system"
        return STATE_COMPLETED, "job-completed-successfully"

    def job_attrs(self, job: IppJob, uri: str, wanted: set | None = None) -> list:
        state, reason = self.job_state(job)
        printed = 0
        if job.ids:
            printed = self.svc.db.one(f"SELECT COUNT(*) n FROM jobs WHERE status='printed' AND id IN ({','.join('?' * len(job.ids))})", job.ids)["n"]
        up = lambda ts: int(ts - self.started) + 1
        everything = [
            ("job-id", ipp.integer(job.id)), ("job-uri", ipp.uri(f"{uri}/{job.id}")), ("job-printer-uri", ipp.uri(uri)),
            ("job-state", ipp.enum(state)), ("job-state-reasons", ipp.kw(reason)), ("job-name", ipp.name(job.name)),
            ("job-originating-user-name", ipp.name(job.user)), ("time-at-creation", ipp.integer(up(job.created))),
            ("time-at-processing", ipp.integer(up(job.created))), ("time-at-completed", ipp.integer(up(job.created) if state >= 7 else 0)),
            ("job-impressions-completed", ipp.integer(printed)), ("job-media-sheets-completed", ipp.integer(printed)),
            ("job-k-octets", ipp.integer(job.k_octets)), ("number-of-documents", ipp.integer(1)),
        ]
        return [(n, v) for n, v in everything if not wanted or "all" in wanted or n in wanted or "job-description" in wanted]

    def _find_job(self, req: ipp.Request) -> IppJob | None:
        jid = req.get(ipp.G_OPERATION, "job-id")
        if jid is None:
            m = re.search(r"/(\d+)$", str(req.get(ipp.G_OPERATION, "job-uri", "")))
            jid = int(m.group(1)) if m else None
        return self._jobs.get(jid) if jid is not None else None

    def _new_job(self, req: ipp.Request, awaiting: bool) -> IppJob:
        with self._lock:
            jid, self._next_job = self._next_job, self._next_job + 1
            job = IppJob(jid, str(req.get(ipp.G_OPERATION, "job-name", "") or "document")[:100],
                         str(req.get(ipp.G_OPERATION, "requesting-user-name", "") or "someone")[:60], time.time(),
                         awaiting_document=awaiting)
            self._jobs[jid] = job
            for old in sorted(self._jobs)[:-50]:                                  # remember the last 50
                del self._jobs[old]
        return job

    def _submit(self, req: ipp.Request, st: Settings, uri: str, version, client: str) -> bytes:
        op, rid = req.operation, req.request_id
        formats = supported_formats(st.raw_enabled)
        declared = str(req.get(ipp.G_OPERATION, "document-format", "application/octet-stream")).lower()
        if declared not in formats:
            return self._error(ipp.FORMAT_NOT_SUPPORTED, rid, f"{declared} is not supported", version)
        if req.get(ipp.G_OPERATION, "compression", "none") not in ("none", None):
            return self._error(ipp.ATTRIBUTES_OR_VALUES, rid, "compression is not supported", version)
        if op == ipp.VALIDATE_JOB:
            return ipp.build_response(ipp.OK, rid, [(ipp.G_OPERATION, self._op_attrs())], version)
        if self.queued_count() >= st.max_queued:
            return self._error(ipp.BUSY, rid, "the printer queue is full", version)
        if op == ipp.CREATE_JOB:
            job = self._new_job(req, awaiting=True)
            return ipp.build_response(ipp.OK, rid, [(ipp.G_OPERATION, self._op_attrs()), (ipp.G_JOB, self.job_attrs(job, uri))], version)
        if op == ipp.SEND_DOCUMENT:
            job = self._find_job(req)
            if job is None or not job.awaiting_document:
                return self._error(ipp.NOT_FOUND, rid, "no such job waiting for a document", version)
            job.awaiting_document = False
        else:
            job = self._new_job(req, awaiting=False)
        data = req.document
        if not data:
            job.error = "no document"
            return self._error(ipp.BAD_REQUEST, rid, "no document data", version)
        job.k_octets = max(1, len(data) // 1024)
        if st.raw_enabled and is_raw(data, declared):               # the printer's own driver (Epson's) made this: pass it through untouched
            job.pages, job.copies = 1, 1
            job.ids.append(self.svc.submit_raw(data, f"{job.name}", source="driver", hold=st.hold_for_button))
            self.status.update(jobs=self.status["jobs"] + 1, last_job=f"{job.name} (printer driver data, {job.k_octets} KB) from {client}")
            log.info("AirPrint: raw printer data '%s' (%d bytes) from %s", job.name, len(data), client)
            return ipp.build_response(ipp.OK, rid, [(ipp.G_OPERATION, self._op_attrs()), (ipp.G_JOB, self.job_attrs(job, uri))], version)
        job.copies = max(1, min(st.max_copies, int(req.get(ipp.G_JOB, "copies", 1) or 1)))
        try:
            pages = load_pages(data, declared, st)
            tickets = [t for t in (ticket(p, st) for p in pages) if t is not None]
        except DocError as e:
            job.error = str(e)
            log.warning("AirPrint: couldn't use a document from %s: %s", client, e)
            return self._error(e.status, rid, str(e), version)
        job.pages = len(tickets)
        title = f"AirPrint: {job.name}"
        for copy in range(job.copies):
            for i, img in enumerate(tickets, 1):
                label = title + (f" ({i}/{len(tickets)})" if len(tickets) > 1 else "") + (f" copy {copy + 1}" if job.copies > 1 else "")
                job.ids.append(self.svc.submit_image(img, label, kind="airprint", source="airprint", hold=st.hold_for_button))
        self.status.update(jobs=self.status["jobs"] + 1, last_job=f"{job.name} ({job.pages} page{'s' if job.pages != 1 else ''}) from {client}")
        log.info("AirPrint: '%s' from %s -> %d page(s)%s", job.name, client, job.pages, " (held for the button)" if st.hold_for_button else "")
        return ipp.build_response(ipp.OK, rid, [(ipp.G_OPERATION, self._op_attrs()), (ipp.G_JOB, self.job_attrs(job, uri))], version)

    def _get_jobs(self, req: ipp.Request, uri: str, version) -> bytes:
        which = req.get(ipp.G_OPERATION, "which-jobs", "not-completed")
        limit = int(req.get(ipp.G_OPERATION, "limit", 50) or 50)
        wanted = set(req.get_all(ipp.G_OPERATION, "requested-attributes")) or {"job-id", "job-uri", "job-state", "job-state-reasons", "job-name"}
        groups = [(ipp.G_OPERATION, self._op_attrs())]
        for jid in sorted(self._jobs, reverse=True):
            job = self._jobs[jid]
            done = self.job_state(job)[0] >= STATE_CANCELED
            if (which == "completed") != done:
                continue
            groups.append((ipp.G_JOB, self.job_attrs(job, uri, wanted)))
            if len(groups) > limit:
                break
        return ipp.build_response(ipp.OK, req.request_id, groups, version)

    def _job_op(self, req: ipp.Request, uri: str, version) -> bytes:
        job = self._find_job(req)
        if job is None:
            return self._error(ipp.NOT_FOUND, req.request_id, "no such job", version)
        if req.operation == ipp.CANCEL_JOB:
            if self.job_state(job)[0] >= STATE_CANCELED:
                return self._error(ipp.NOT_POSSIBLE, req.request_id, "the job has already finished", version)
            if job.ids:
                self.svc.db.execute(f"UPDATE jobs SET status='failed', error='Cancelled' WHERE status IN ('pending','held') "
                                    f"AND id IN ({','.join('?' * len(job.ids))})", job.ids)
            job.canceled = True
            return ipp.build_response(ipp.OK, req.request_id, [(ipp.G_OPERATION, self._op_attrs())], version)
        wanted = set(req.get_all(ipp.G_OPERATION, "requested-attributes"))
        return ipp.build_response(ipp.OK, req.request_id, [(ipp.G_OPERATION, self._op_attrs()), (ipp.G_JOB, self.job_attrs(job, uri, wanted))], version)

    # ------------------------------------------------------------------ self-test (for the web page)
    def self_test(self) -> dict:
        """Ask our own IPP port for the printer's attributes, the way a phone would."""
        if self._httpd is None:
            return {"ok": False, "message": "AirPrint isn't running. Switch it on and save first."}
        port = self._httpd.server_address[1]
        body = ipp.build_response(ipp.GET_PRINTER_ATTRIBUTES, 1, [(ipp.G_OPERATION, [
            ("attributes-charset", ipp.charset("utf-8")), ("attributes-natural-language", ipp.lang("en")),
            ("printer-uri", ipp.uri(f"ipp://127.0.0.1:{port}/ipp/print")), ("requested-attributes", ipp.kw("printer-name", "printer-state"))])])
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("POST", "/ipp/print", body, {"Content-Type": "application/ipp"})
            r = c.getresponse()
            data = r.read()
            c.close()
            resp = ipp.parse_request(data)                      # same layout as a request
            if r.status != 200 or resp.operation != ipp.OK:
                return {"ok": False, "message": f"The printer answered with an error (HTTP {r.status}, IPP 0x{resp.operation:04x})."}
        except Exception as e:
            return {"ok": False, "message": f"No answer on port {port}: {type(e).__name__}"}
        adv = "announced over Bonjour" if self.status.get("advertising") else "NOT announced over Bonjour (add it by address)"
        return {"ok": True, "message": f"Working: it answers on port {port} and is {adv}.", "url": self.status.get("url", "")}
