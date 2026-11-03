"""User-defined rules: "if the email matches X, do Y".

This is the hook for fitting the printer into a business/work routine without
touching code, e.g.

  * sender contains "@myclient.com"  ->  use the "client" template, title "CLIENT"
  * subject starts with "URGENT"     ->  mark urgent, print 2 copies
  * sender equals "boss@work.com"    ->  heading prefix "[BOSS]"
"""
from __future__ import annotations

import re

FIELDS = ["sender", "subject", "body", "has_image", "is_forward", "calendar"]
OPS = ["contains", "not_contains", "equals", "starts_with", "ends_with", "regex", "is_true"]


def _test(op: str, actual, value: str) -> bool:
    if op == "is_true":
        return bool(actual)
    a, v = str(actual or "").lower(), str(value or "").lower()
    if op == "contains":
        return v in a
    if op == "not_contains":
        return v not in a
    if op == "equals":
        return a == v
    if op == "starts_with":
        return a.startswith(v)
    if op == "ends_with":
        return a.endswith(v)
    if op == "regex":
        try:
            return re.search(value, str(actual or ""), re.I) is not None
        except re.error:
            return False
    return False


def evaluate(rules: list[dict], ctx: dict) -> dict:
    """Return merged actions of every matching rule (later rules win)."""
    result: dict = {"template": "", "title": "", "heading_prefix": "", "copies": 1,
                    "urgent": False, "skip": False, "matched": []}
    for r in rules or []:
        if not r.get("enabled", True):
            continue
        conds = r.get("conditions") or []
        if not conds:
            continue
        outcomes = [_test(c.get("op", "contains"), ctx.get(c.get("field")), c.get("value", "")) for c in conds]
        ok = any(outcomes) if r.get("match") == "any" else all(outcomes)
        if not ok:
            continue
        a = r.get("actions") or {}
        result["matched"].append(r.get("name") or "rule")
        for k in ("template", "title", "heading_prefix"):
            if a.get(k):
                result[k] = a[k]
        result["copies"] = max(result["copies"], int(a.get("copies") or 1))
        result["urgent"] = result["urgent"] or bool(a.get("urgent"))
        result["skip"] = result["skip"] or bool(a.get("skip"))
    result["copies"] = min(result["copies"], 5)
    return result


def apply_to_card(card, res: dict) -> None:
    if res["template"]:
        card.template = res["template"]
    if res["title"]:
        card.vars["_title"] = res["title"]
    if res["heading_prefix"]:
        card.heading = f"{res['heading_prefix']} {card.heading}".strip()
    if res["urgent"]:
        card.priority = True
