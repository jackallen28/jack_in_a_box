"""Incoming email  ->  one or more Cards (not yet drawn)."""
from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from . import icons as icon_lib, llm, plugins, rules
from .dates import fmt_when
from .mailparse import Incoming
from .models import Card, Section
from . import emailclean
from .focus import detect_image_request, find_focus, parse_focus
from .summarise import bullets as extractive_bullets
from .summarise import finish, overview_and_actions, prepare_thread
from .textproc import _dedupe, build_items, clean_body, fix_text, pop_leading_tags, sort_items, split_blocks


def local_tz(cfg: dict):
    name = (cfg["general"].get("timezone") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo


def local_now(cfg: dict) -> datetime:
    return datetime.now(timezone.utc).astimezone(local_tz(cfg))


def sender_allowed(addr: str, cfg: dict) -> bool:
    """Is this one of YOUR addresses (admin list)?  Entries: me@x.com or @domain.com."""
    addr = (addr or "").lower()
    for entry in cfg["email"].get("allowed_senders", []):
        e = str(entry).strip().lower()
        if e and (e == addr or (e.startswith("@") and addr.endswith(e))):
            return True
    return False


def classify_sender(addr: str, cfg: dict) -> tuple[str | None, dict | None]:
    """("admin", None) | ("friend", friend) | (None, None).  Admin wins if both."""
    if sender_allowed(addr, cfg):
        return "admin", None
    a = (addr or "").strip().lower()
    for f in cfg.get("friends", []):
        if f.get("enabled", True) and str(f.get("email", "")).strip().lower() == a and a:
            return "friend", f
    return None, None


def _name_only(from_header: str) -> str:
    name, addr = parseaddr(from_header or "")
    return name or addr or ""


log = logging.getLogger("forward")


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " ... (cut short)"


def thread_labels(thread: list[dict]) -> list[str]:
    out = []
    for i, m in enumerate(thread):
        who = ", ".join(x for x in (m.get("sender"), m.get("date")) if x)
        out.append(("LATEST MESSAGE" if i == 0 else "EARLIER") + (f" - {who}" if who else ""))
    return out


def summarise_thread(thread: list[dict], context: str, cfg: dict, n: int, use_ai: bool = True,
                     exclude: set | None = None) -> tuple[list[str], list[str], dict]:
    """The whole forwarded conversation -> (bullet points, notes for the history, info).

    Bullets are short points: what it's about, the key facts, then what to do ("[ ] ..." = an action).  The local AI
    writes them if it is switched on and its answer checks out; otherwise they are built from the email's key sentences."""
    sentences, owners = prepare_thread([m["text"] for m in thread])
    keep = [i for i, x in enumerate(sentences) if x not in (exclude or set())]    # not what [focus] answers already show
    rest, rest_owners = [sentences[i] for i in keep], [owners[i] for i in keep]
    notes, info, out = [], {}, None
    if cfg["summary"].get("backend") == "ollama" and use_ai and rest:
        out, info = llm.summarise(rest, n, cfg["llm"], context, rest_owners, thread_labels(thread))
        notes.append(f"AI summary ({info['seconds']}s)" if out else f"AI not used: {info['reason']}")
    if not out and rest:
        out = extractive_bullets(rest, n, rest_owners)
    return out or [], notes, info


def email_card(inc: Incoming, cfg: dict, now: datetime, use_ai: bool = True) -> Card:
    """A forwarded email -> [your [focus] answers] + summary (or the full text) + your ACTIONS.

    Your own notes (above the forwarded message) can contain:
      [reply by friday] / [paper due monday]   pull the relevant sentences out of the email
      [full]                                   print the whole message instead of a summary
      [school] / [!]                           symbols, as everywhere else
    Signatures, disclaimers, older replies in the chain, links and logo placeholders are removed first."""
    scfg = cfg["summary"]
    n = max(2, min(8, int(scfg.get("sentences", 4))))
    notes_raw = clean_body(inc.body)
    want_images, notes_raw = detect_image_request(notes_raw)          # "include image" anywhere in your note
    notes_raw, focus, want_full = parse_focus(notes_raw, cfg)

    # The WHOLE thread, newest message first (each with its own signature removed): the summary and the
    # [focus] lookups can use what was said earlier in the conversation, not just the top message.
    thread = emailclean.thread_messages(inc.forwarded or "")
    message = "\n\n".join(m["text"] for m in thread)
    sentences, owners = prepare_thread([m["text"] for m in thread])
    log.info("Forward: your note %d chars; forwarded thread %d message(s), %d sentences%s", len(notes_raw), len(thread),
             len(sentences), " (no forward marker found - split by a guess)" if inc.guessed_forward else "")
    sections: list[Section] = []
    card_notes: list[str] = []

    shown: set[str] = set()                      # sentences already printed under a [focus] heading
    for f in focus[:6]:
        hits = find_focus(f, sentences)
        shown.update(hits)
        found = [finish(x) for x in hits]
        sections.append(Section("focus", label=f.upper()[:40], text=" ".join(found) if found else "Not mentioned in this email."))

    if want_full:
        parts = []
        for i, m in enumerate(thread):
            who = ", ".join(x for x in (m.get("sender"), m.get("date")) if x)
            parts.append(m["text"] if i == 0 else (f"EARLIER - {who}:\n" if who else "EARLIER:\n") + m["text"])
        text = _cap("\n\n".join(parts), int(scfg.get("max_full_chars", 2500))) or "(The forwarded email had no readable text.)"
        sections.append(Section("full", text=text))
    else:
        ctx = f"From: {_name_only(inc.fwd_from)}\nSubject: {inc.subject or inc.fwd_subject}" if inc.fwd_from else f"Subject: {inc.subject}"
        out, notes, _ = summarise_thread(thread, ctx, cfg, n, use_ai, shown)
        card_notes += notes
        summary_out = out

    images = []
    if want_images:
        images = list(inc.fwd_images)[: int(cfg["email"].get("max_images", 3))]
        card_notes.append(f"included {len(images)} image(s)" if images else "image requested but the email has none (small logos are ignored)")

    subject, s_ids = icon_lib.extract_tags(inc.subject, cfg)
    heading = subject or re.sub(r"^(?:fwd?|fw|re)\s*:\s*", "", inc.fwd_subject or "", flags=re.I).strip()

    actions: list[str] = []
    lead_ids, above = pop_leading_tags(notes_raw, cfg)
    body_ids: list[str] = []
    for b in split_blocks(above, cfg["text"].get("split", True), cfg):
        body_ids += b.get("icons", [])
        for line in [b["text"], *b.get("checklist", [])]:
            t = fix_text(line, cfg, is_task=True)
            if t:
                actions.append(t)
    if not want_full:
        # SUMMARY = overview facts only; ACTIONS = everything to do (yours first, then the email's). Never the same thing twice.
        focus_text = [x for sec in sections if sec.key == "focus" for x in [sec.text]]
        overview, actions = overview_and_actions(summary_out, actions, focus_text)
        if overview:
            sections.insert(len([x for x in sections if x.key == "focus"]), Section("summary", items=overview, bullets=True))
        elif not focus and not actions:
            sections.append(Section("summary", text="(The forwarded email had no readable text.)"))
    actions = actions[: int(scfg.get("max_actions", 10))]
    if actions:
        sections.append(Section("actions", items=actions))

    subline = ""
    if scfg.get("show_original_sender", True) and inc.fwd_from:
        subline = f"From {_name_only(inc.fwd_from)}" + (f" · {len(thread)} messages" if len(thread) > 1 else "")
    return Card(kind="email", heading=heading, subline=subline, sections=sections, icons=_dedupe(s_ids + lead_ids + body_ids),
                images=images, source="email", created=now, notes=card_notes, vars={"date": now.strftime("%a %d %b")})


def reminder_cards(inc: Incoming, cfg: dict, now: datetime, split: bool | None = None) -> list[Card]:
    ecfg, tcfg = cfg["email"], cfg["text"]
    ref = inc.date.astimezone(now.tzinfo) if inc.date else now
    if ref > now:   # clock skew
        ref = now
    heading, s_ids = icon_lib.extract_tags(inc.subject, cfg)     # [tags] in the subject apply to every reminder
    body = inc.body
    has_images = bool(inc.images)
    if not body.strip() and heading and ecfg.get("subject_only_as_task", True) and not has_images:
        body, heading = heading, ""      # the subject *is* the reminder

    # Images: the whole email becomes ONE reminder with the pictures underneath.
    items = build_items(body, ref, cfg, split=False if has_images else split)
    if not items and not has_images:
        return []                       # nothing to print (empty email) - don't waste paper
    if has_images:
        items = items[:1]
        return [_reminder(items[0] if items else None, heading, now, cfg, inc.images, s_ids)]
    items = sort_items(items, tcfg.get("sort", "smart"))
    return [_reminder(i, heading, now, cfg, [], s_ids) for i in items[: int(ecfg.get("max_cards_per_email", 10))]]


def _reminder(item, heading: str, now: datetime, cfg: dict, images, icon_ids=()) -> Card:
    secs: list[Section] = []
    priority = False
    notes: list[str] = []
    ids = list(icon_ids)
    if item is not None:
        priority, notes = item.priority, list(item.notes)
        ids = _dedupe(ids + item.icons)
        if item.text or item.checklist:
            secs.append(Section("text", text=item.text, items=item.checklist))
        if item.deadline:
            secs.append(Section("deadline", text=fmt_when(item.deadline.when, item.deadline.has_time, now,
                                                          cfg["general"].get("time_format", "12h")), strong=True))
    return Card(kind="reminder", heading=heading, sections=secs, images=list(images), priority=priority,
                icons=ids, source="email", created=now, notes=notes)


def plain_card(inc: Incoming, cfg: dict, now: datetime, *, kind: str = "reminder", template: str = "",
               name: str = "", tidy: bool = False) -> Card | None:
    """One printout holding the text exactly as written (friends' messages, 'print exactly')."""
    body = inc.body
    if inc.forwarded:
        body = f"{body}\n\n{inc.forwarded}".strip()
    body = clean_body(body)
    subject, s_ids = icon_lib.extract_tags(inc.subject, cfg)
    body, b_ids = icon_lib.extract_tags(body, cfg)
    notes: list[str] = []
    if tidy and body:
        body = "\n\n".join(fix_text(p, cfg, is_task=False, notes=notes) for p in body.split("\n\n"))
    if not (body or subject or inc.images):
        return None
    secs = [Section("text", text=body)] if body else []
    return Card(kind=kind, heading=subject, sections=secs, images=list(inc.images), template=template,
                icons=_dedupe(s_ids + b_ids),
                source="email", created=now, notes=notes,
                vars={"name": name, "date": now.strftime("%a %d %b")})


def message_card(inc: Incoming, friend: dict, cfg: dict, now: datetime | None = None) -> Card | None:
    m = cfg["messages"]
    tpl = m.get("template") or "message"
    card = plain_card(inc, cfg, now or local_now(cfg), kind="message",
                      template=tpl if tpl in cfg["templates"] else "message",
                      name=friend.get("name") or friend.get("email", ""), tidy=m.get("tidy_text", False))
    if card is not None:
        res = rules.evaluate(cfg.get("rules", []), {"sender": f"{friend.get('name')} <{friend.get('email')}>",
                                                    "subject": inc.subject, "body": inc.body,
                                                    "has_image": bool(inc.images), "is_forward": False})
        if res["skip"]:
            return None
        rules.apply_to_card(card, res)
    return card


def build_cards(inc: Incoming, cfg: dict, now: datetime | None = None,
                split: bool | None = None, use_ai: bool = True) -> tuple[list[Card], int]:
    """Return (cards, copies_each)."""
    now = now or local_now(cfg)
    cards = [email_card(inc, cfg, now, use_ai)] if inc.forwarded is not None else reminder_cards(inc, cfg, now, split)
    ctx_fields = {"sender": f"{inc.sender_name} <{inc.sender_addr}>", "subject": inc.subject,
                  "body": inc.body, "has_image": bool(inc.images), "is_forward": inc.forwarded is not None}
    res = rules.evaluate(cfg.get("rules", []), ctx_fields)
    if res["skip"]:
        return [], 1
    for c in cards:
        rules.apply_to_card(c, res)
        if res["matched"]:
            c.notes.append("rules: " + ", ".join(res["matched"]))
    if cfg["integrations"].get("plugins_enabled", True):
        cards = plugins.process(cards, {"incoming": inc, "config": cfg, "source": "email"})
    return cards, res["copies"]
