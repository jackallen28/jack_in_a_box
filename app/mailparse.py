"""Parse a raw RFC 822 email into the bits the printer cares about."""
from __future__ import annotations

import email
import email.policy
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from .textproc import tidy_chars

try:  # iPhone photos are HEIC; optional extra
    from pillow_heif import register_heif_opener  # type: ignore
    register_heif_opener()
except Exception:
    pass
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse decompression bombs


@dataclass
class Incoming:
    message_id: str = ""
    sender_name: str = ""
    sender_addr: str = ""
    subject: str = ""
    date: datetime | None = None
    body: str = ""                      # text above any forwarded content
    forwarded: str | None = None        # the forwarded email's body, if any
    fwd_from: str = ""
    fwd_subject: str = ""
    fwd_date: str = ""
    images: list[Image.Image] = field(default_factory=list)
    fwd_images: list[Image.Image] = field(default_factory=list)   # pictures in a FORWARDED email (printed only if you say "include image")
    fwd_all_images: list[Image.Image] = field(default_factory=list)   # ...EVERY picture in it, minus ones known to be a signature (for "[images attached]")
    fwd_signature_skipped: int = 0      # how many pictures were left out of fwd_all_images because they sit in a signature
    auth_ok: bool = True
    guessed_forward: bool = False       # "Fwd:" subject but no forward marker: split note/forward by a guess


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote"}

    def __init__(self, mark_images: bool = False):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.mark_images = mark_images        # leave "\ue000cid\ue001" where a cid: picture sits (to tell where it is in the message)

    def handle_starttag(self, tag, attrs):
        if tag == "img" and self.mark_images and not self.skip:
            src = dict(attrs).get("src") or ""
            if src.lower().startswith("cid:"):
                self.out.append(f"\n\ue000{src[4:].strip().strip('<>')}\ue001\n")
        elif tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
            if tag == "li":
                self.out.append("- ")
        elif tag in ("td", "th"):
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str, mark_images: bool = False) -> str:
    p = _HTMLText(mark_images)
    p.feed(html)
    text = "".join(p.out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# A picture is only treated as a signature graphic when we KNOW it is: it sits after a sign-off line ("Kind regards", "Thanks,", "--",
# "Sent from my iPhone") within the same message, before the next quoted message starts.  Anything else is kept.
_SIGN_WORDS = r"(?:(?:kind|best|warm|many|with|my)\s+)?(?:regards|thanks|thank you|cheers|sincerely|best wishes|yours (?:sincerely|faithfully|truly)|best|ta|cheerio)"
_SIGNOFF = re.compile(
    rf"^\W*{_SIGN_WORDS}\W*$"                                      # "Kind regards,"
    rf"|^\W*{_SIGN_WORDS}\s*,\s*[\w'.-]+(?:\s+[\w'.-]+){{0,2}}\W*$"      # "Regards, Dave" / "Thanks, Dave Smith" (needs the comma, so "Thanks for the photos" isn't one)
    r"|^--\s*$|^sent from my\b|^get outlook for\b|^sent via\b", re.I)
_BOUNDARY = re.compile(
    r"^\W*(?:from|sent|date|to|cc|subject)\s*:|^on .{5,140}wrote:\s*$|^-{2,}\s*(?:original|forwarded) message|^_{5,}\s*$|^begin forwarded message", re.I)


def signature_cids(html: str) -> tuple[set[str], set[str]]:
    """(cids of pictures known to be in a signature at the bottom of a message, cids of all other pictures) in an HTML body."""
    text = html_to_text(html, mark_images=True)
    sig, other, in_sig = set(), set(), False
    for line in text.split("\n"):
        line = line.strip()
        m = re.fullmatch(r"\ue000(.+?)\ue001", line)
        if m:
            (sig if in_sig else other).add(m.group(1).lower())
            continue
        if _BOUNDARY.match(line):
            in_sig = False
        elif _SIGNOFF.match(line):
            in_sig = True
    return sig, other - sig


def clean_subject(subject: str, strip_fwd: bool = False) -> str:
    s = re.sub(r"\s+", " ", subject or "").strip()
    if strip_fwd:
        while True:
            n = re.sub(r"^\s*(?:fwd?|fw|re|aw|tr)\s*:\s*", "", s, flags=re.I)
            if n == s:
                break
            s = n
    if s.lower() in ("(no subject)", "no subject"):
        return ""
    return tidy_chars(s).strip()


_FWD_MARKERS = [
    re.compile(r"^[>\s]*-{3,}\s*forwarded message\s*-{3,}\s*$", re.I | re.M),
    re.compile(r"^[>\s]*begin forwarded message:?\s*$", re.I | re.M),
    re.compile(r"^[>\s]*-{3,}\s*original message\s*-{3,}\s*$", re.I | re.M),
    re.compile(r"^_{10,}\s*\n(?=from:)", re.I | re.M),
]
_HEADER_LINE = re.compile(r"^\s*(from|date|sent|subject|to|cc|reply-to)\s*:\s*(.*)$", re.I)


def split_forward(text: str) -> tuple[str, str | None, dict]:
    """Return (what-you-typed, forwarded-body, forwarded-headers)."""
    best = None
    for rx in _FWD_MARKERS:
        m = rx.search(text)
        if m and (best is None or m.start() < best.start()):
            best = m
    start = end = None
    if best is not None:
        start, end = best.start(), best.end()
    else:  # Outlook style: bare "From: ... Sent: ..." header block
        m = re.search(r"^from:\s.+\n(?:.*\n){0,3}?(?:sent|date):\s", text, re.I | re.M)
        if m:
            start = end = m.start()
    if start is None:
        return text, None, {}
    above = text[:start].strip()
    rest = text[end:].lstrip("\n")
    rest_lines = rest.split("\n")
    nonempty = [l for l in rest_lines if l.strip()]
    if nonempty and sum(l.lstrip().startswith(">") for l in nonempty) / len(nonempty) >= 0.6:
        rest = "\n".join(re.sub(r"^\s*>\s?", "", l) for l in rest_lines)      # Apple Mail: whole forward is "> " quoted
    headers: dict = {}
    lines = rest.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines):
        hm = _HEADER_LINE.match(lines[i])
        if hm:
            headers[hm.group(1).lower()] = hm.group(2).strip()
        elif not (headers and lines[i][:1] in " \t" and lines[i].strip()):  # wrapped header
            break
        i += 1
    body = "\n".join(lines[i:]).strip()
    return above, body, headers


def guess_forward(text: str) -> tuple[str, str]:
    """No forward marker found.  A short first paragraph is probably your note; everything else is the forward."""
    parts = re.split(r"\n\s*\n", text.strip(), maxsplit=1)
    if len(parts) == 2 and len(parts[0].split()) <= 40:
        return parts[0].strip(), parts[1].strip()
    return "", text.strip()


def _text_of(msg: email.message.EmailMessage) -> str:
    """Best plain-text rendering of a message (prefers text/plain)."""
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        try:
            txt = plain.get_content()
            if txt and txt.strip():
                return txt
        except Exception:
            pass
    html = msg.get_body(preferencelist=("html",))
    if html is not None:
        try:
            return html_to_text(html.get_content())
        except Exception:
            pass
    return ""


def _load_image(data: bytes, min_px: int) -> Image.Image | None:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    im = ImageOps.exif_transpose(im)
    if min(im.size) < min_px:  # signature logos, tracking pixels
        return None
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, "white")
        bg.alpha_composite(im)
        im = bg
    return im.convert("RGB")


def parse(raw: bytes, *, min_image_px: int = 150, max_images: int = 3) -> Incoming:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    inc = Incoming(
        message_id=str(msg.get("Message-ID", "")).strip(),
        sender_name=name,
        sender_addr=addr.lower(),
    )
    try:
        inc.date = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        inc.date = None

    auth = " ".join(str(h) for h in msg.get_all("Authentication-Results", []) or [])
    if auth:
        inc.auth_ok = bool(re.search(r"\b(?:dkim|spf)=pass\b", auth, re.I))

    raw_subject = str(msg.get("Subject", ""))
    is_fwd_subject = bool(re.match(r"\s*(?:fwd?|fw)\s*:", raw_subject, re.I))
    text = tidy_chars(_text_of(msg))

    # Forward-as-attachment (message/rfc822)
    attached_msg = None
    for part in msg.walk():
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            if payload:
                attached_msg = payload[0]
                break

    above, fwd_body, headers = split_forward(text)
    if attached_msg is not None:
        above, fwd_body = text.strip(), tidy_chars(_text_of(attached_msg))
        headers = {"from": str(attached_msg.get("From", "")),
                   "subject": str(attached_msg.get("Subject", "")),
                   "date": str(attached_msg.get("Date", ""))}
    if fwd_body is None and is_fwd_subject and attached_msg is None and text.strip():
        above, fwd_body = guess_forward(text)
        inc.guessed_forward = True
    if fwd_body is not None or is_fwd_subject:
        inc.forwarded = fwd_body if fwd_body is not None else ""
        inc.body = above
        inc.fwd_from = headers.get("from", "")
        inc.fwd_subject = headers.get("subject", "")
        inc.fwd_date = headers.get("date") or headers.get("sent", "")
    else:
        inc.body = text.strip()
    inc.subject = clean_subject(raw_subject, strip_fwd=inc.forwarded is not None)

    sig_cids: set[str] = set()                         # pictures we KNOW sit in a signature (after a sign-off, in the HTML)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            try:
                sig_cids |= signature_cids(part.get_content())[0]
            except Exception:
                pass
    attached, inline, everything, seen_hashes = [], [], [], set()
    for part in msg.walk():
        if part.get_content_maintype() == "image" or (
                part.get_content_type() == "application/octet-stream"
                and str(part.get_filename() or "").lower().endswith((".heic", ".heif", ".jpg", ".jpeg", ".png"))):
            try:
                data = part.get_payload(decode=True) or b""
            except Exception:
                data = b""
            digest = hash(data)
            if data and digest not in seen_hashes and len(everything) < 40:                  # every picture, once, whatever its size
                seen_hashes.add(digest)
                im = _load_image(data, 20)                                                  # only invisible tracking pixels are dropped
                if im is not None:
                    cid = str(part.get("Content-ID") or "").strip().strip("<>").lower()
                    everything.append((im, cid in sig_cids and part.get_content_disposition() != "attachment"))
    inc.fwd_all_images = [im for im, in_sig in everything if not in_sig]
    inc.fwd_signature_skipped = sum(1 for _, in_sig in everything if in_sig)
    for part in msg.walk():
        if len(attached) + len(inline) >= max_images * 3:
            break
        if part.get_content_maintype() == "image" or (
                part.get_content_type() == "application/octet-stream"
                and str(part.get_filename() or "").lower().endswith((".heic", ".heif", ".jpg", ".jpeg", ".png"))):
            try:
                data = part.get_payload(decode=True)
            except Exception:
                continue
            im = _load_image(data or b"", min_image_px)          # skips signature logos / tracking pixels
            if im is not None:
                (attached if part.get_content_disposition() == "attachment" else inline).append(im)
    if inc.forwarded is None:
        inc.images = (attached + inline)[:max_images]
    else:
        # real attachments beat inline pictures, which are mostly signature graphics
        inc.fwd_images = (attached or inline)[:max_images]
    return inc
