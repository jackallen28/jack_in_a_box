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


def _unquote_if_quoted(text: str) -> str:
    """Apple Mail and some others prefix a whole forwarded message with "> ": remove one level, keep the words."""
    lines = text.split("\n")
    nonempty = [l for l in lines if l.strip()]
    if nonempty and sum(l.lstrip().startswith(">") for l in nonempty) / len(nonempty) >= 0.6:
        return "\n".join(re.sub(r"^\s*>\s?", "", l) for l in lines)
    return text


def clean_message(text: str) -> str:
    """One message of a thread: no signature, disclaimer, link clutter or image placeholders."""
    text = _unquote_if_quoted(tidy_chars(text or ""))
    for rx in _IMG:
        text = rx.sub("", text)
    text = _BANNER.sub("", text)
    m = _DISCLAIMER.search(text)
    if m:
        text = text[:m.start()]
    text = _URL.sub("(link)", text)
    text = strip_signature(text.strip())
    text = re.sub(r"\(link\)(?:\s*\(link\))+", "(link)", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"(?<=\S)[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- reply chains
_WROTE_END = re.compile(r"wrote:\s*$", re.I)
_HDR = re.compile(r"^\s*(from|sent|date|to|cc|subject)\s*:\s*(.*)$", re.I)


def _on_wrote(lines: list[str], i: int) -> tuple[int, dict] | None:
    """Gmail/Apple: 'On Fri, 30 Oct 2026 at 9:15 AM, Jack <j@x.com> wrote:' (may wrap onto the next line or two)."""
    first = lines[i].strip()
    if not re.match(r"^on\s", first, re.I):
        return None
    joined = first
    for n in range(1, 4):
        if _WROTE_END.search(joined):
            body = re.sub(r"^on\s+", "", joined, flags=re.I)
            body = _WROTE_END.sub("", body).strip().rstrip(",")
            body = re.sub(r"\s*<[^>]*>", "", body)                 # drop <email@x.com>
            sender = body.rsplit(",", 1)[-1].strip() if "," in body else body
            date = body[: len(body) - len(sender)].strip(" ,") if "," in body else ""
            return n, {"sender": sender, "date": date}
        if i + n >= len(lines) or len(joined) > 300:
            return None
        joined = f"{joined} {lines[i + n].strip()}"
    return None


def _find_boundary(lines: list[str]):
    """First place where an older message begins: (index, lines used, kind, header) or None."""
    for i, line in enumerate(lines):
        if re.match(r"^\s*-{2,}\s*(?:original message|forwarded message|reply message)\s*-{2,}\s*$", line, re.I):
            return i, 1, "block", {}
        if re.match(r"^\s*_{8,}\s*$", line) and i + 1 < len(lines) and re.match(r"^\s*from\s*:", lines[i + 1], re.I):
            return i, 1, "block", {}
        ow = _on_wrote(lines, i)
        if ow:
            return i, ow[0], "quoted", ow[1]
        if re.match(r"^\s*from\s*:\s*\S", line, re.I) and any(re.match(r"^\s*(sent|date)\s*:", l, re.I) for l in lines[i + 1:i + 6]):
            return i, 0, "block", {}
    return None


def _take_quoted(rest: list[str]) -> tuple[list[str], list[str]]:
    """The '> ' block right after an 'On ... wrote:' line, one level removed, plus whatever follows it."""
    quoted: list[str] = []
    i = 0
    while i < len(rest):
        l = rest[i]
        if l.lstrip().startswith(">"):
            quoted.append(re.sub(r"^\s*>\s?", "", l))
        elif not l.strip() and any(x.lstrip().startswith(">") for x in rest[i + 1:i + 3]):
            quoted.append("")
        else:
            break
        i += 1
    return quoted, rest[i:]


def thread_messages(text: str) -> list[dict]:
    """Split a forwarded email into the messages of its reply chain, NEWEST FIRST.

    Each item: {"sender", "date", "text"} with the text cleaned of signature/disclaimer/links/logo placeholders.
    Handles Gmail/Apple "On ... wrote:" (> quoted), Outlook "From:/Sent:" blocks and "-----Original Message-----"."""
    text = tidy_chars(text or "")
    out: list[dict] = []

    def walk(chunk: str, header: dict, depth: int) -> None:
        lines = chunk.split("\n")
        b = _find_boundary(lines) if depth < 10 else None
        if b is None:
            out.append({**header, "text": clean_message(chunk)})
            return
        idx, used, kind, hdr = b
        head = "\n".join(lines[:idx])
        rest = lines[idx + used:]
        if kind == "quoted":
            quoted, after = _take_quoted(rest)
            if "".join(after).strip():                      # reply typed BELOW the quote belongs to the newer message
                head += "\n" + "\n".join(after)
            out.append({**header, "text": clean_message(head)})
            walk("\n".join(quoted), hdr, depth + 1)
        else:                                               # a header block, then the older message (not quoted)
            while rest and not rest[0].strip():
                rest = rest[1:]
            info: dict = {}
            while rest:
                hm = _HDR.match(rest[0])
                if hm:
                    info[hm.group(1).lower()] = hm.group(2).strip()
                elif not (info and rest[0][:1] in " \t" and rest[0].strip()):
                    break
                rest = rest[1:]
            out.append({**header, "text": clean_message(head)})
            sender = re.sub(r"\s*<[^>]*>", "", info.get("from", "")).strip()
            walk("\n".join(rest), {"sender": sender, "date": info.get("sent") or info.get("date", "")}, depth + 1)

    walk(text, {}, 0)
    return [m for m in out if m["text"].strip()]


def clean_forwarded(text: str, *, keep_thread: bool = False) -> str:
    """The message with its signature, disclaimer, links and logo placeholders removed.
    By default only the newest message; ``keep_thread=True`` returns the whole reply chain, newest first."""
    msgs = thread_messages(text)
    if not msgs:
        return ""
    return "\n\n".join(m["text"] for m in msgs) if keep_thread else msgs[0]["text"]
