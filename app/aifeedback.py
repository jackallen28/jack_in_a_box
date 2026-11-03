"""Review loop for the AI summary: keep what it was shown and what it wrote, let you correct it, feed that back.

A 1 GB Pi cannot retrain a model, so "learning" here means three things that really do change the next summary:
  * your corrected (or approved) summaries become WORKED EXAMPLES shown to the model before a similar email
    (closest first, ahead of the built-in examples);
  * your standing RULES ("always say who it's from") are added to its instructions;
  * everything can be downloaded as chat-format JSONL for real fine-tuning on a bigger computer.
Nothing leaves the Pi except through that download.  All of it lives in data/ (never committed to git).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

MAX_RUNS = 500
MAX_RULES, MAX_RULE_CHARS = 8, 200
_NO_ACTION = re.compile(r"(?i)^\W*(?:ACTION:\s*)?(?:no (?:action|need|reply|response)|nothing (?:to do|needed|required))\b")


# ----------------------------------------------------------------------------- bullet formats
def to_lines(bullets: list[str]) -> list[str]:
    """The summary's bullets ("[ ] x" = a to-do) as the model writes them: plain lines, to-dos as "ACTION: x"."""
    return [("ACTION: " + b[4:]) if b.startswith("[ ] ") else b for b in bullets]


def parse_lines(text: str) -> list[str]:
    """What you typed in the correction box (one bullet per line, "ACTION: ..." for a to-do) -> clean lines."""
    out = []
    for raw in (text or "").splitlines():
        t = re.sub(r"^\s*(?:[-*•]|\d{1,2}[.)])\s*", "", raw).strip()
        t = re.sub(r"^(?:action|to ?do|do)\s*[:\-]\s*", "ACTION: ", t, flags=re.I)
        if t and t != "ACTION:" and not _NO_ACTION.match(t):
            out.append(t[:300])
    return out[:8]


def llm_answer(bullets: list[str], symbols: list[str] | None) -> str:
    text = "\n".join("- " + b for b in bullets)
    return text + ("\nSYMBOLS: " + (", ".join(symbols) or "none") if symbols is not None else "")


# ----------------------------------------------------------------------------- recording
def record(db, trace: dict, job_id: int | None) -> int:
    cur = db.execute(
        "INSERT INTO ai_runs(created, job_id, subject, sender, email, thread, used_ai, model, seconds, reason, bullets, printed, symbols) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), job_id, (trace.get("subject") or "")[:200], (trace.get("sender") or "")[:120],
         trace.get("email", "")[:8000], json.dumps(trace.get("thread", []))[:30000], 1 if trace.get("used_ai") else 0,
         trace.get("model", ""), float(trace.get("seconds") or 0), (trace.get("reason") or "")[:300],
         json.dumps(trace.get("bullets", [])), json.dumps(trace.get("printed", {})),
         json.dumps(trace["symbols"]) if trace.get("symbols") is not None else None))
    return cur.lastrowid


def prune(db, days: int) -> int:
    """Old runs you never acted on go; ones you corrected or approved stay (they are your training data)."""
    cutoff = (datetime.now() - timedelta(days=max(1, int(days)))).isoformat(timespec="seconds")
    n = db.execute("DELETE FROM ai_runs WHERE created<? AND status IN ('new','bad')", (cutoff,)).rowcount
    extra = db.all("SELECT id FROM ai_runs WHERE status IN ('new','bad') ORDER BY id DESC LIMIT -1 OFFSET ?", (MAX_RUNS,))
    for r in extra:
        n += db.execute("DELETE FROM ai_runs WHERE id=?", (r["id"],)).rowcount
    return n


def _row(r: dict, full: bool = False) -> dict:
    j = lambda k, d: json.loads(r[k]) if r.get(k) else d
    out = {"id": r["id"], "created": r["created"], "job_id": r["job_id"], "subject": r["subject"], "sender": r["sender"],
           "used_ai": bool(r["used_ai"]), "model": r["model"], "seconds": r["seconds"], "reason": r["reason"],
           "bullets": j("bullets", []), "printed": j("printed", {}), "status": r["status"], "comment": r["comment"] or "",
           "corrected": j("corrected", None), "train": bool(r["train"]), "symbols": j("symbols", None)}
    if full:
        out["email"] = r["email"]
    return out


def counts(db) -> dict:
    rows = db.all("SELECT status, COUNT(*) n FROM ai_runs GROUP BY status")
    c = {r["status"]: r["n"] for r in rows}
    return {"total": sum(c.values()), "new": c.get("new", 0), "good": c.get("good", 0), "fix": c.get("fix", 0), "bad": c.get("bad", 0)}


def list_runs(db, flt: str = "all", limit: int = 30) -> list[dict]:
    where = {"new": "status='new'", "reviewed": "status!='new'", "ai": "used_ai=1", "all": "1=1"}.get(flt, "1=1")
    return [_row(r) for r in db.all(f"SELECT * FROM ai_runs WHERE {where} ORDER BY id DESC LIMIT ?", (max(1, min(200, int(limit))),))]


def get_run(db, rid: int) -> dict | None:
    r = db.one("SELECT * FROM ai_runs WHERE id=?", (rid,))
    return _row(r, full=True) if r else None


# ----------------------------------------------------------------------------- your feedback
def set_feedback(db, rid: int, verdict: str, corrected: str = "", comment: str = "", symbols: list[str] | None = None) -> dict | None:
    """verdict: good (the summary was right) | fix (here is the right one) | bad (wrong, no replacement) | reset."""
    run = db.one("SELECT * FROM ai_runs WHERE id=?", (rid,))
    if not run:
        return None
    comment = (comment or "").strip()[:500]
    own = json.loads(run["bullets"] or "[]")
    fixed = parse_lines(corrected)
    if verdict == "fix" and (not fixed or fixed == own):
        verdict = "good" if fixed == own and own else "bad"          # nothing changed = it was fine; nothing written = no example
    if symbols is not None:                                              # you corrected the symbols (ids; [] = none)
        db.execute("UPDATE ai_runs SET symbols=? WHERE id=?", (json.dumps(symbols), rid))
    if verdict == "good":
        db.execute("UPDATE ai_runs SET status='good', comment=?, corrected=NULL, train=1 WHERE id=?", (comment, rid))
    elif verdict == "fix":
        db.execute("UPDATE ai_runs SET status='fix', comment=?, corrected=?, train=1 WHERE id=?", (comment, json.dumps(fixed), rid))
    elif verdict == "bad":
        db.execute("UPDATE ai_runs SET status='bad', comment=?, corrected=NULL, train=0 WHERE id=?", (comment, rid))
    else:
        db.execute("UPDATE ai_runs SET status='new', comment=?, corrected=NULL, train=1 WHERE id=?", (comment, rid))
    return get_run(db, rid)


def add_rule(rules: list[str], text: str) -> list[str]:
    t = re.sub(r"\s+", " ", (text or "").strip())[:MAX_RULE_CHARS]
    if not t or any(t.lower() == r.lower() for r in rules):
        return rules
    return [*rules, t][-MAX_RULES:]


def delete(db, rid: int) -> bool:
    return db.execute("DELETE FROM ai_runs WHERE id=?", (rid,)).rowcount > 0


def clear(db, keep_reviewed: bool = True) -> int:
    return db.execute("DELETE FROM ai_runs" + (" WHERE status IN ('new','bad')" if keep_reviewed else "")).rowcount


# ----------------------------------------------------------------------------- feeding it back
def training_rows(db, limit: int = 60, cfg: dict | None = None) -> list[dict]:
    """Reviewed summaries as {about, email, bullets, symbols}: your correction, or the AI's own answer if you marked it good.
    ``symbols`` are symbol NAMES (as the AI writes them); only present when ``cfg`` is given and symbols were recorded."""
    names = {}
    if cfg is not None:
        from . import icons as icon_lib
        names = {c["id"]: c["name"].lower() for c in icon_lib.catalogue(cfg)}
    out = []
    for r in db.all("SELECT * FROM ai_runs WHERE train=1 AND status IN ('good','fix') ORDER BY id DESC LIMIT ?", (limit,)):
        bullets = json.loads(r["corrected"]) if r["status"] == "fix" and r["corrected"] else json.loads(r["bullets"] or "[]")
        if r["email"] and 2 <= len(bullets) <= 8:
            ex = {"about": (r["subject"] or "your email")[:60], "email": r["email"], "bullets": bullets, "mine": True, "id": r["id"]}
            if names and r.get("symbols") is not None:
                ex["symbols"] = [names[i] for i in json.loads(r["symbols"]) if i in names]
            out.append(ex)
    return out


def write_examples(db, path: Path, cfg: dict | None = None) -> int:
    """data/ai_examples.json - read by the AI on every summary (no database access from inside the model code)."""
    rows = training_rows(db, cfg=cfg)
    Path(path).write_text(json.dumps({"mine": rows}, ensure_ascii=False, indent=1), "utf-8")
    return len(rows)


def export_jsonl(db, system: str, ask, cfg: dict | None = None) -> str:
    """Chat-format fine-tuning data, one JSON object per line: system + the email + the answer you approved."""
    lines = []
    for r in training_rows(db, limit=1000, cfg=cfg):
        lines.append(json.dumps({"messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": ask(r["email"])["content"]},
            {"role": "assistant", "content": llm_answer(r["bullets"], r.get("symbols"))}]}, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")
