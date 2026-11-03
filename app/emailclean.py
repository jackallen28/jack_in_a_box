"""Strip the clutter out of a forwarded email so only the real message is left.

Removes, in this order:
  * image placeholders left behind by signatures/logos: [cid:image001.png@...], [image: Logo], <image001.png>,
    ![alt](url), "Image removed by sender"  (the pictures themselves are never printed for forwards anyway)
  * links (replaced by "(link)" so a long tracking URL can't eat the paper)
  * older messages in a reply chain ("On Mon ... wrote:", "-----Original Message-----", Outlook From:/Sent: blocks)
  * legal disclaimers and "CAUTION: external email" banners
  * the signature: "-- ", "Sent from my iPhone", and the sign-off block ("Kind regards, / Dave Smith / Accounts Manager /
    T: 02 ... / www....")
What's left is the message itself.  Errs on the side of keeping text: a signature is only cut when it looks like one.
"""
from __future__ import annotations

import re

from .textproc import tidy_chars

_IMG = [
    re.compile(r"\[cid:[^\]]*\]", re.I),
    re.compile(r"\[(?:image|img|logo|picture|inline image)[^\]]{0,80}\]", re.I),
    re.compile(r"<\s*image\d*\.\w+\s*>", re.I),
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),
    re.compile(r"^.*\bimage removed by sender\b.*$", re.I | re.M),
    re.compile(r"^\s*\(?see attached file:.*$", re.I | re.M),
]
_URL = re.compile(r"<?\bhttps?://[^\s<>\")]+>?|<?\bwww\.[^\s<>\")]+>?", re.I)

_THREAD = [
    re.compile(r"^on\s.{4,200}?\bwrote:\s*$", re.I | re.M | re.S),
    re.compile(r"^-{2,}\s*(?:original message|forwarded message|reply message)\s*-{2,}\s*$", re.I | re.M),
    re.compile(r"^_{8,}\s*$\n?(?=from:)", re.I | re.M),
    re.compile(r"^from:\s.+\n(?:.*\n){0,4}?(?:sent|date):\s", re.I | re.M),
    re.compile(r"^>.*$\n?", re.M),
]
_DISCLAIMER = re.compile(
    r"^\s*(?:this (?:e-?mail|message)(?: and any (?:attachments|files)| is (?:intended|confidential|private)| may (?:be|contain) (?:confidential|privileged))"
    r"|the information (?:contained )?in this (?:e-?mail|message)|confidentiality notice|disclaimer\s*:?"
    r"|if you (?:are not the intended recipient|have received this (?:e-?mail|message) in error)"
    r"|please consider the environment|warning:.{0,40}virus|privileged (?:and|&) confidential|this transmission is)",
    re.I | re.M)
_BANNER = re.compile(r"^\s*[\[(]?(?:external|caution|warning)[\])]?\s*[:\-]?.{0,120}(?:external|outside|not click|unknown sender).*$", re.I | re.M)
_SENT_FROM = re.compile(r"^\s*(?:sent from my |sent from (?:outlook|mail|yahoo|samsung)|get outlook for |sent via |download outlook)", re.I | re.M)

_SIGNOFF = re.compile(
    r"^\s*(?:(?:kind|best|warm|many|with|king|yours)\s+)?(?:regards|thanks|thank you|cheers|sincerely|best wishes|"
    r"yours (?:sincerely|faithfully|truly)|many thanks|talk soon|speak soon|take care|thx|ta|best|rgds|br)"
    r"\b[\s,!.\-]*(?:[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,2})?[\s,!.]*$", re.I)
_PHONE = re.compile(r"(?:\+?\d[\d\s().\-]{7,}\d)")
_CONTACT = re.compile(
    r"(?:^\s*(?:t|m|p|f|e|w|tel|ph|phone|mob|mobile|fax|email|e-mail|web|web:|office|direct|abn|acn|linkedin|twitter)\s*[:.|]\s*\S)"
    r"|(?:\S+@\S+\.\w{2,})|(?:^\s*(?:[a-z ]{1,12}[:|]\s*)?\(link\)\s*$)|(?:^\s*\+?\d[\d\s().\-]{7,}\d\s*$)", re.I | re.M)
_ADDRESS = re.compile(r"^\s*(?:level|suite|unit|po box|p\.o\. box|floor)\b|^\s*\d+[a-z]?\s+[\w' .-]+\b(?:st|street|rd|road|ave|avenue|pde|parade|dr|drive|ln|lane|way|court|ct|place|pl|hwy|highway|blvd)\b", re.I)


def _is_contact_line(line: str) -> bool:
    if len(line.split()) > 10:             # a real sentence that happens to mention a link/phone/email
        return False
    return bool(_CONTACT.search(line) or _ADDRESS.search(line) or (_PHONE.search(line) and len(line.split()) <= 8))


def _words(line: str) -> int:
    return len(line.split())


def strip_signature(text: str) -> str:
    lines = text.split("\n")
    # 1. explicit markers
    for i, line in enumerate(lines):
        if re.match(r"^--\s*$|^_{2,}\s*$", line.rstrip()) and i > 0:
            lines = lines[:i]
            text = "\n".join(lines).rstrip()
            break
    m = _SENT_FROM.search(text)
    if m:
        text = text[:m.start()].rstrip()
        lines = text.split("\n")
    # 2. the last sign-off line, if only signature-looking lines follow it
    tail_start = max(0, len(lines) - 25)
    for i in range(len(lines) - 1, tail_start - 1, -1):
        if _SIGNOFF.match(lines[i]) and lines[i].strip():
            after = [l for l in lines[i + 1:] if l.strip()]
            if len(after) <= 14 and all(_words(l) <= 12 for l in after):
                return "\n".join(lines[:i]).rstrip()
            break
    # 3. no sign-off: peel contact details (and the name/title lines above them) off the bottom
    end, dropped_contact = len(lines), False
    j = len(lines) - 1
    while j >= 0 and len(lines) - j <= 14:
        l = lines[j].strip()
        if not l:
            j -= 1
            continue
        if _is_contact_line(l):
            dropped_contact = True
            end = j
        elif dropped_contact and _words(l) <= 6 and not re.search(r"[.!?:]$", l):
            end = j                        # "Dave Smith", "Accounts Manager", "Supplier Pty Ltd"
        else:
            break
        j -= 1
    return "\n".join(lines[:end]).rstrip() if dropped_contact else "\n".join(lines).rstrip()


def clean_forwarded(text: str, *, keep_thread: bool = False) -> str:
    """Return just the message: no signature, disclaimer, older replies, link clutter or image placeholders."""
    text = tidy_chars(text or "")
    for rx in _IMG:
        text = rx.sub("", text)
    text = _BANNER.sub("", text)
    if not keep_thread:
        for rx in _THREAD:
            m = rx.search(text)
            if m and m.start() > 0:
                text = text[:m.start()]
            elif m and m.start() == 0 and rx is _THREAD[-1]:
                text = rx.sub("", text)
    m = _DISCLAIMER.search(text)
    if m:
        text = text[:m.start()]
    text = _URL.sub("(link)", text)
    text = strip_signature(text.strip())
    text = re.sub(r"\(link\)(?:\s*\(link\))+", "(link)", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"(?<=\S)[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
