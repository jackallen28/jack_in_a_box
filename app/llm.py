"""Local AI summaries through Ollama - built for a 1 GB Raspberry Pi 3B+.

Why this is cautious:
  * A tiny model (qwen2.5:0.5b, ~400 MB) is fast enough but will happily invent facts.  So it is never trusted:
    - it only sees a SHORT excerpt (the sentences the built-in ranker already thinks matter: requests and deadlines
      first), which also keeps the prompt small enough to run in about a minute on a 3B+;
    - every number, date, day/month name and name in its answer must appear in the email, or the answer is thrown
      away and the built-in summary is used instead.
  * The model is unloaded straight after each summary (``keep_alive: 0``) so the Pi's RAM is free the rest of the time,
    only one summary runs at a time, and the context window is capped (``num_ctx``).
  * Any failure (Ollama off, model missing, too slow, rejected answer) falls back to the built-in summary, so
    turning this on can never lose a printout.
[focus] lookups and [full] never use the model - they only quote the email.
"""
from __future__ import annotations

import json
import logging
import platform
import re
import threading
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("llm")
_LOCK = threading.Lock()          # one model run at a time (RAM)
_busy = 0                         # >0 while a summary is waiting for / running on the model (the fan watches this)
_busy_lock = threading.Lock()


def is_busy() -> bool:
    return _busy > 0


def _busy_add(n: int) -> None:
    global _busy
    with _busy_lock:
        _busy += n

SYSTEM = ("You summarise a forwarded email for a small printed slip. Read the WHOLE email, then write 3 to 5 short bullet points in your own plain words "
          "(do not copy sentences or greetings): first what the email is about, then the key facts (amounts, dates, names, places), and last anything the reader "
          "must DO, starting that bullet with 'ACTION:' and including the deadline. If nothing needs doing, write no ACTION bullet and never say whether a reply is needed. "
          "Use ONLY facts stated in the email. Never invent names, dates, numbers or amounts.")

EXAMPLES_FILE = Path(__file__).with_name("summary_examples.json")
USER_EXAMPLES_FILE: Path | None = None     # data/ai_examples.json - your reviewed summaries (set at start-up; see aifeedback.py)
MINE_BONUS = 1.6                           # your own examples win ties over the built-in ones


def system_prompt(cfg: dict | None = None) -> str:
    """The instructions, plus your standing rules (max 8, kept short so a small model still follows them)."""
    rules = [str(r).strip()[:200] for r in (cfg or {}).get("rules", []) if str(r).strip()][:8]
    if not rules:
        return SYSTEM
    return SYSTEM + " The reader's own rules, always follow them: " + " ".join(f"({i}) {r.rstrip('.')}." for i, r in enumerate(rules, 1))


def ask(body: str) -> dict:
    return {"role": "user", "content": f"EMAIL:\n{body}\n\nBULLET POINTS:"}


def answer_text(bullets: list[str]) -> str:
    return "\n".join("- " + b for b in bullets)


def load_user_examples() -> list[dict]:
    try:
        return list(json.loads(USER_EXAMPLES_FILE.read_text("utf-8")).get("mine", [])) if USER_EXAMPLES_FILE else []
    except (OSError, ValueError, AttributeError):
        return []


@lru_cache(maxsize=1)
def load_examples() -> dict:
    try:
        return json.loads(EXAMPLES_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"train": [], "eval": []}


def pick_examples(text: str, k: int, use_mine: bool = True) -> list[dict]:
    """The k worked examples most like this email (by shared words) - shown to the model before the real one.
    Your own reviewed summaries count extra, so what you corrected is what it learns from first."""
    if k <= 0:
        return []
    words = lambda t: set(re.findall(r"[a-z]{4,}", t.lower()))
    mine = words(text)
    pool = [(e, 1.0) for e in load_examples().get("train", [])]
    if use_mine:
        pool += [(e, MINE_BONUS) for e in load_user_examples()]
    score = lambda pair: len(mine & words(pair[0]["email"])) / (1 + len(words(pair[0]["email"])) ** 0.5) * pair[1]
    return [e for e, _ in sorted(pool, key=lambda p: -score(p))[:k]]


_WEEK = r"mon|tue|wed|thu|fri|sat|sun"
_MONTH = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"


def _post(cfg: dict, path: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(cfg.get("url", "http://127.0.0.1:11434").rstrip("/") + path,
                                 data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(cfg: dict, path: str, timeout: float) -> dict:
    with urllib.request.urlopen(cfg.get("url", "http://127.0.0.1:11434").rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------------------- checking the answer
def grounded(sentences: list[str], source: str) -> tuple[bool, list[str]]:
    """True if nothing in the summary is missing from the email.  Returns (ok, the offending words)."""
    src = source.lower()
    src_words = set(re.findall(r"[a-z0-9$]+", src))
    src_digits = set(re.findall(r"\d+", src.replace(",", "")))
    text = " ".join(sentences)
    bad: list[str] = []
    for d in re.findall(r"\d+", text.replace(",", "")):
        if d not in src_digits:
            bad.append(d)
    for w in re.findall(rf"\b((?:{_WEEK}|{_MONTH})[a-z]*)\b", text, re.I):
        if w[:3].lower() not in {x[:3] for x in src_words}:
            bad.append(w)
    for s in sentences:
        for w in s.split()[1:]:                      # capitalised mid-sentence words = names
            w2 = re.sub(r"[^\w'-]", "", w)
            w2 = re.sub(r"(?:'s|’s)$", "", w2)           # "Jack's" is the name Jack
            if len(w2) > 2 and w2[0].isupper() and w2.lower() not in src_words and w2.lower().rstrip("s") not in src_words:
                bad.append(w2)
    content = [w for w in re.findall(r"[a-z]{4,}", text.lower())]
    if content and sum(w in src_words or w.rstrip("s") in src_words for w in content) / len(content) < 0.55:
        bad.append("(mostly new wording)")
    return (not bad, sorted(set(bad), key=bad.index))


def _parse(output: str, n: int) -> list[str]:
    """The model's answer -> bullets.  "ACTION: ..." becomes "[ ] ..." (printed with a tick-box)."""
    out = re.sub(r"\*\*|__|`", "", output or "").strip()
    out = re.sub(r"^\s*(?:bullet points|summary|email summary|here(?:'s| is)[^:\n]*)\s*:\s*", "", out, flags=re.I)
    lines = [re.sub(r"^\s*(?:[-*•]|\d{1,2}[.)])\s*", "", l).strip() for l in out.split("\n") if l.strip()]
    if len(lines) < 2:                                              # one paragraph: cut it into sentences instead
        lines = [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", re.sub(r"\s+", " ", out)) if x.strip()]
    bullets = []
    for l in lines:
        m = re.match(r"^(?:ACTION|TO ?DO|DO)\s*[:\-]\s*(.+)$", l, re.I)
        text = (m.group(1) if m else l).strip()
        if len(text.split()) < 3 or re.match(r"(?i)(?:no (?:action|need|reply|response)|nothing (?:to do|needed|required))\b", text):
            continue                                                # never "no action needed" - the reader decides that
        text = text.rstrip(".") if m else (text if text[-1] in ".!?" else text + ".")
        bullets.append(("[ ] " + text[:1].upper() + text[1:]) if m else text[:1].upper() + text[1:])
    return bullets[:n]


def condense(sentences: list[str], limit_chars: int, owners: list[int] | None = None, labels: list[str] | None = None) -> str:
    """The WHOLE email for the model, message by message in order.  Only if it won't fit are the least useful
    sentences dropped (never the tail), so the model still sees how the email began and ended."""
    from .focus import action_score
    from .summarise import rank
    keep = list(range(len(sentences)))
    if sum(len(x) + 1 for x in sentences) > limit_chars:
        scores = rank(sentences)
        droppable = sorted((i for i in keep if action_score(sentences[i]) < 1.0), key=lambda i: scores[i])
        total = sum(len(x) + 1 for x in sentences)
        for i in droppable:
            if total <= limit_chars:
                break
            keep.remove(i)
            total -= len(sentences[i]) + 1
        while total > limit_chars and len(keep) > 3:                # still too long: drop the weakest remaining
            worst = min(keep, key=lambda i: scores[i])
            keep.remove(worst)
            total -= len(sentences[worst]) + 1
    if not owners or len(set(owners)) < 2:
        return " ".join(sentences[i] for i in keep)
    groups: dict[int, list[str]] = {}
    for i in keep:
        groups.setdefault(owners[i], []).append(sentences[i])
    lines = []
    for o in sorted(groups):                                        # newest message first, as in the thread
        lines.append(f"{labels[o] if labels and o < len(labels) else ('LATEST MESSAGE' if o == 0 else 'EARLIER')}:")
        lines.append(" ".join(groups[o]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- the call
def run(sentences: list[str], n: int, cfg: dict, context: str = "", owners: list[int] | None = None,
        labels: list[str] | None = None) -> tuple[list[str] | None, dict]:
    """Ask the model for bullet points.  Returns (bullets or None, info) - info explains what happened."""
    info: dict = {"used_ai": False, "seconds": 0.0, "reason": ""}
    if not sentences:
        info["reason"] = "nothing to summarise"
        return None, info
    text = condense(sentences, int(cfg.get("max_input_chars", 3000)), owners, labels)
    head = f"{context.strip()}\n" if context.strip() else ""      # e.g. "From: Dave Smith / Subject: Invoice" (also allowed in the answer)
    messages = [{"role": "system", "content": system_prompt(cfg)}]
    for ex in pick_examples(head + text, int(cfg.get("examples", 1)), bool(cfg.get("use_my_examples", True))):      # worked examples ("training data")
        messages += [ask(ex["email"]), {"role": "assistant", "content": answer_text(ex["bullets"])}]
    messages.append(ask(f"{head}{text}"))
    payload = {
        "model": cfg.get("model", "qwen2.5:0.5b"), "stream": False,
        "keep_alive": cfg.get("keep_alive", 0),
        "messages": messages,
        "options": {"temperature": 0.1, "num_ctx": int(cfg.get("num_ctx", 2048)),
                    "num_predict": int(cfg.get("num_predict", 220)), "num_thread": 4},
    }
    t0 = time.time()
    _busy_add(1)
    try:
        with _LOCK:
            data = _post(cfg, "/api/chat", payload, float(cfg.get("timeout", 240)))
    except urllib.error.HTTPError as e:
        info["reason"] = "model not installed - run: ollama pull " + payload["model"] if e.code == 404 else f"Ollama error {e.code}"
    except Exception as e:                                             # off, unreachable, timed out ...
        info["reason"] = f"Ollama not reachable or too slow ({type(e).__name__})"
    else:
        info["seconds"] = round(time.time() - t0, 1)
        raw = (data.get("message") or {}).get("content", "") or data.get("response", "")
        info["raw"] = raw
        sents = _parse(raw, n)
        if len(sents) < 1 or sum(len(s) for s in sents) < 25:
            info["reason"] = "the model's answer was empty or too short"
        else:
            plain = [re.sub(r"^\[ \] ", "", b) for b in sents]
            ok, bad = grounded(plain, f"{context} {' '.join(labels or [])} " + " ".join(sentences))
            if ok:
                info["used_ai"] = True
                return sents, info
            info["reason"] = "rejected - mentioned things not in the email: " + ", ".join(bad[:6])
    finally:
        _busy_add(-1)
    info["seconds"] = info["seconds"] or round(time.time() - t0, 1)
    log.warning("AI summary not used (%s) - using the built-in summary", info["reason"])
    return None, info


def summarise(sentences: list[str], n: int, cfg: dict, context: str = "", owners: list[int] | None = None,
              labels: list[str] | None = None) -> tuple[list[str] | None, dict]:
    return run(sentences, n, cfg, context, owners, labels)


# --------------------------------------------------------------------------- status / test (for the settings page)
def _mem() -> tuple[int | None, int | None]:
    try:
        d = dict(l.split(":", 1) for l in open("/proc/meminfo").read().splitlines() if ":" in l)
        mb = lambda k: int(d[k].split()[0]) // 1024
        return mb("MemAvailable"), mb("MemTotal")
    except Exception:
        return None, None


def status(cfg: dict) -> dict:
    arch = platform.machine()
    avail, total = _mem()
    st = {"arch": arch, "mem_available_mb": avail, "mem_total_mb": total, "reachable": False, "model_installed": False,
          "model": cfg.get("model", "qwen2.5:0.5b"), "ok": False}
    local = any(h in cfg.get("url", "") for h in ("127.0.0.1", "localhost"))
    if local and arch in ("armv7l", "armv6l", "arm"):
        st["message"] = ("This Pi is running a 32-bit system, and Ollama needs 64-bit. Reflash the SD card with "
                         "'Raspberry Pi OS Lite (64-bit)' (a 3B+ supports it), or point the address at another computer.")
        return st
    try:
        tags = _get(cfg, "/api/tags", 4)
        st["reachable"] = True
        names = [m.get("name", "") for m in tags.get("models", [])]
        st["models"] = names
        st["model_installed"] = any(n == st["model"] or n.split(":")[0] == st["model"].split(":")[0] and ":" not in st["model"] for n in names)
        st["message"] = ("Ollama is running and the model is installed." if st["model_installed"]
                         else f"Ollama is running but '{st['model']}' isn't downloaded. On the Pi run:  ollama pull {st['model']}")
        st["ok"] = st["model_installed"]
    except Exception:
        st["message"] = (f"Can't reach Ollama at {cfg.get('url')}. Install it with deploy/ai-setup.sh (see the README), "
                         "or check the address.")
    return st


SAMPLE = ("Hi Jack, I hope you are well. Our records show invoice 4471 for $12,480 has not been received and is 20 days overdue. "
          "Please let me know by Friday so we can close the quarter. We have also noticed invoice 4402 was paid twice, so there is "
          "a credit of $3,200 on your account. Our bank details change from 1 December. Kind regards, Dave")

_test_state: dict = {"running": False, "result": None}


def start_test(cfg: dict, n: int = 4) -> bool:
    """Run the sample email through the model in the background (it can take minutes on a Pi)."""
    if _test_state["running"]:
        return False
    from .summarise import prepare

    def work():
        _test_state.update(running=True, result=None)
        try:
            sents, info = run(prepare(SAMPLE), n, cfg)
            _test_state["result"] = {"summary": sents, **info}
        except Exception as e:   # pragma: no cover
            _test_state["result"] = {"summary": None, "reason": str(e), "seconds": 0, "used_ai": False}
        _test_state["running"] = False

    _test_state["running"] = True
    threading.Thread(target=work, daemon=True).start()
    return True


def test_state() -> dict:
    return dict(_test_state)
