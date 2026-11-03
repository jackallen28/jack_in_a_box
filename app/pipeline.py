"""Incoming email  ->  one or more Cards (not yet drawn)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from . import icons as icon_lib, llm, plugins, rules
from .dates import fmt_when
from .mailparse import Incoming
from .models import Card, Section
from .summarise import summarise
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


def email_card(inc: Incoming, cfg: dict, now: datetime) -> Card:
    scfg = cfg["summary"]
    n = max(2, min(8, int(scfg.get("sentences", 4))))
    source_text = clean_body(inc.forwarded or "", strip_quotes=False)
    sents = llm.summarise(source_text, n, cfg["llm"]) if scfg.get("backend") == "ollama" else None
    sents = sents or summarise(source_text, n)
    subject, s_ids = icon_lib.extract_tags(inc.subject, cfg)
    heading = subject or re.sub(r"^(?:fwd?|fw|re)\s*:\s*", "", inc.fwd_subject or "", flags=re.I).strip()

    actions: list[str] = []
    lead_ids, above = pop_leading_tags(clean_body(inc.body), cfg)
    body_ids: list[str] = []
    for b in split_blocks(above, cfg["text"].get("split", True), cfg):
        body_ids += b.get("icons", [])
        for line in [b["text"], *b.get("checklist", [])]:
            t = fix_text(line, cfg, is_task=True)
            if t:
                actions.append(t)
    actions = actions[: int(scfg.get("max_actions", 10))]

    subline = ""
    if scfg.get("show_original_sender", True) and inc.fwd_from:
        subline = f"From {_name_only(inc.fwd_from)}"
    sections = [Section("summary", text=" ".join(sents) if sents else "(The forwarded email had no readable text.)")]
    if actions:
        sections.append(Section("actions", items=actions))
    return Card(kind="email", heading=heading, subline=subline, sections=sections, icons=_dedupe(s_ids + lead_ids + body_ids),
                source="email", created=now, vars={"date": now.strftime("%a %d %b")})


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
                split: bool | None = None) -> tuple[list[Card], int]:
    """Return (cards, copies_each)."""
    now = now or local_now(cfg)
    cards = [email_card(inc, cfg, now)] if inc.forwarded is not None else reminder_cards(inc, cfg, now, split)
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
