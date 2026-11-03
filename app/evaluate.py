"""Score the AI summary on held-out emails:  python -m app.evaluate

The test emails live in app/summary_examples.json under "eval".  They are NEVER shown to the model as worked
examples (those are under "train"), so the score is an honest picture of how it copes with emails it hasn't seen.

For each email it checks
    * did the AI answer at all, and pass the grounding check (nothing invented)?   -> "accepted"
    * 2-6 bullet points?                                                            -> "shape"
    * are the key facts (amounts, dates ...) in the bullets?                        -> "facts"
    * is there a to-do ("[ ] ...") when the email asks for something - and none when it doesn't? -> "actions"
The built-in (no AI) summary is scored the same way with --builtin, so you can see what the AI adds.

  python -m app.evaluate                       # the Pi's own Ollama and model from your saved settings
  python -m app.evaluate --url http://192.168.1.20:11434 --model qwen2.5:3b     # e.g. a bigger model on your Mac
  python -m app.evaluate --builtin             # no AI
  python -m app.evaluate --cases 3 --show      # only the first 3, print the bullets
  python -m app.evaluate --mine                # score the AI on YOUR corrections (AI review tab), each hidden from the model while tested
Each run on a Pi 3B+ takes about a minute per email.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from . import autosymbols, llm
from .config import ConfigStore, DEFAULTS, merge
from .pipeline import summarise_thread


def score_case(case: dict, bullets: list[str], info: dict, ai: bool, symbols: list[str] | None = None) -> dict:
    text = " ".join(bullets).lower()
    facts = case.get("must_include", [])
    found = [f for f in facts if f.lower() in text]
    has_action = any(b.startswith("[ ] ") for b in bullets)
    return {
        "name": case["name"],
        "accepted": bool(info.get("used_ai")) if ai else None,
        "shape": 2 <= len(bullets) <= 6,
        "facts": f"{len(found)}/{len(facts)}",
        "facts_ok": len(found) == len(facts),
        "actions": has_action == bool(case.get("expect_action", False)),
        "symbols": symbols or [],
        "symbols_safe": all(i in case.get("symbols_ok", []) for i in (symbols or [])),
        "seconds": info.get("seconds", 0.0),
        "reason": info.get("reason", ""),
        "bullets": bullets,
    }


def evaluate(cfg: dict, use_ai: bool = True, cases: int | None = None) -> list[dict]:
    work = copy.deepcopy(cfg)
    work["summary"]["backend"] = "ollama" if use_ai else "extractive"
    out = []
    for case in llm.load_examples().get("eval", [])[:cases]:
        thread = [{"sender": m.get("sender", ""), "date": m.get("date", ""), "text": m["text"]} for m in case["messages"]]
        ctx = f"From: {case.get('from', '')}\nSubject: {case.get('subject', '')}"
        body = " ".join(m["text"] for m in case["messages"])
        auto = work["summary"].get("auto_symbols", True)
        ev = autosymbols.analyse(case.get("subject", ""), case.get("from", ""), body, case.get("raw", body), work) if auto else {}
        short = autosymbols.candidates(ev)
        bullets, _notes, info = summarise_thread(thread, ctx, work, int(work["summary"].get("sentences", 4)), use_ai, None,
                                                 autosymbols.describe(work, short) if short else None)
        ai = autosymbols.resolve(info["symbols_raw"], work) if info.get("used_ai") and info.get("symbols_raw") is not None else None
        symbols = autosymbols.decide(ev, ai) if auto else []
        out.append(score_case(case, bullets, info, use_ai, symbols))
    return out


def evaluate_mine(cfg: dict, db, cases: int | None = None, tmp_dir: Path | None = None) -> list[dict]:
    """Score the AI against YOUR corrections (AI review tab).  Each corrected summary is hidden from the model while it
    is tested (leave-one-out), so the score says how well it handles an email like those, not whether it can copy."""
    import json
    import tempfile
    from .aifeedback import to_lines, training_rows
    from .summarise import similar
    work = copy.deepcopy(cfg)
    work["summary"]["backend"] = "ollama"
    mine = training_rows(db, limit=200)
    fixed = db.all("SELECT * FROM ai_runs WHERE status='fix' AND corrected IS NOT NULL ORDER BY id DESC LIMIT ?", (cases or 20,))
    out, saved = [], llm.USER_EXAMPLES_FILE
    tmp = Path(tmp_dir or tempfile.mkdtemp()) / "loo.json"
    try:
        for r in fixed:
            tmp.write_text(json.dumps({"mine": [e for e in mine if e["id"] != r["id"]]}), "utf-8")
            llm.USER_EXAMPLES_FILE = tmp
            thread = json.loads(r["thread"] or "[]")
            ctx = f"From: {r['sender']}\nSubject: {r['subject']}"
            bullets, _n, info = summarise_thread(thread, ctx, work, int(work["summary"].get("sentences", 4)), True)
            got, want = to_lines(bullets), json.loads(r["corrected"])
            recall = sum(any(similar(w, g) for g in got) for w in want) / max(1, len(want))
            precision = sum(any(similar(g, w) for w in want) for g in got) / max(1, len(got))
            acts = lambda xs: [x for x in xs if x.startswith("ACTION:")]
            out.append({"name": (r["subject"] or "(no subject)")[:34], "accepted": bool(info.get("used_ai")), "recall": recall, "precision": precision,
                        "actions": bool(acts(got)) == bool(acts(want)), "seconds": info.get("seconds", 0.0), "reason": info.get("reason", ""), "bullets": bullets})
    finally:
        llm.USER_EXAMPLES_FILE = saved
    return out


def report_mine(results: list[dict], show: bool = False) -> str:
    lines = []
    for r in results:
        lines.append(f"{r['name']:34}  covers your points {int(100 * r['recall']):>3}%  nothing extra {int(100 * r['precision']):>3}%  "
                     f"actions {'yes' if r['actions'] else 'NO '}  AI accepted {'yes' if r['accepted'] else 'NO '}  {r['seconds']:>6}s")
        if show:
            lines += [f"      - {b}" for b in r["bullets"]]
    n = max(1, len(results))
    lines += ["", f"{len(results)} of your corrected emails: covers your points {int(100 * sum(r['recall'] for r in results) / n)}%, "
              f"nothing extra {int(100 * sum(r['precision'] for r in results) / n)}%, actions right {int(100 * sum(r['actions'] for r in results) / n)}%"]
    return "\n".join(lines)


def report(results: list[dict], ai: bool, show: bool = False) -> str:
    lines = []
    for r in results:
        mark = lambda ok: "yes" if ok else "NO "
        row = f"{r['name'][:34]:34}  shape {mark(r['shape'])}  facts {r['facts']:>4}  actions {mark(r['actions'])}  symbols {mark(r['symbols_safe'])} {','.join(r['symbols']) or '-'}"
        if ai:
            row += f"  accepted {mark(r['accepted'])}  {r['seconds']:>6}s"
        lines.append(row)
        if r["reason"] and ai and not r["accepted"]:
            lines.append(f"      AI not used: {r['reason']}")
        if show:
            lines += [f"      - {b}" for b in r["bullets"]]
    n = max(1, len(results))
    pct = lambda k: f"{100 * sum(1 for r in results if r[k]) // n}%"
    lines.append("")
    lines.append(f"{len(results)} emails:  right shape {pct('shape')}   all key facts {pct('facts_ok')}   "
                 f"actions right {pct('actions')}   symbols safe {pct('symbols_safe')}" + (f"   AI accepted {pct('accepted')}" if ai else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.evaluate", description=__doc__.split("\n\n")[0])
    ap.add_argument("--builtin", action="store_true", help="score the built-in summary (no AI)")
    ap.add_argument("--url", help="Ollama address (default: your saved setting)")
    ap.add_argument("--model", help="model name (default: your saved setting)")
    ap.add_argument("--cases", type=int, help="only the first N emails")
    ap.add_argument("--examples", type=int, help="how many worked examples to show the model (default: your setting)")
    ap.add_argument("--show", action="store_true", help="print the bullet points")
    ap.add_argument("--mine", action="store_true", help="score the AI against YOUR corrections from the AI review tab instead")
    ap.add_argument("--config", help="path to a config.json (default: the app's own)")
    a = ap.parse_args(argv)

    cfg = ConfigStore(Path(a.config)).data if a.config else ConfigStore().data
    cfg = merge(DEFAULTS, cfg)
    if a.url:
        cfg["llm"]["url"] = a.url
    if a.model:
        cfg["llm"]["model"] = a.model
    if a.examples is not None:
        cfg["llm"]["examples"] = a.examples
    use_ai = not a.builtin
    if a.mine:
        from .config import DATA_DIR
        from .db import DB
        st = llm.status(cfg["llm"])
        if not st.get("ok"):
            print("AI isn't ready:", st.get("message"), file=sys.stderr)
            return 2
        res = evaluate_mine(cfg, DB(DATA_DIR / "reminders.db"), a.cases)
        print(report_mine(res, a.show) if res else "You haven't corrected any summaries yet (AI review tab -> Your version).")
        return 0
    if use_ai:
        st = llm.status(cfg["llm"])
        if not st.get("ok"):
            print("AI isn't ready:", st.get("message"), file=sys.stderr)
            return 2
        print(f"Model {cfg['llm']['model']} at {cfg['llm']['url']}, {cfg['llm'].get('examples', 1)} worked example(s) per email\n")
    results = evaluate(cfg, use_ai, a.cases)
    print(report(results, use_ai, a.show))
    return 0


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
