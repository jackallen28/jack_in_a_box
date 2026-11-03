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

SYSTEM = ("You write the summary of a forwarded email for a small receipt printout. "
          "Use ONLY facts stated in the email text. Put requests, actions and deadlines FIRST. "
          "Write 3 to 5 short, plain sentences. Never add names, dates, numbers or amounts that are not in the text.")

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
            if len(w2) > 2 and w2[0].isupper() and w2.lower() not in src_words and w2.lower().rstrip("s") not in src_words:
                bad.append(w2)
    content = [w for w in re.findall(r"[a-z]{4,}", text.lower())]
    if content and sum(w in src_words or w.rstrip("s") in src_words for w in content) / len(content) < 0.55:
        bad.append("(mostly new wording)")
    return (not bad, sorted(set(bad), key=bad.index))


def _parse(output: str, n: int) -> list[str]:
    out = re.sub(r"\*\*|__|`", "", output).strip()
    out = re.sub(r"^\s*(?:summary|email summary|here(?:'s| is)[^:\n]*)\s*:\s*", "", out, flags=re.I)
    out = re.sub(r"(?m)^\s*(?:[-*•]|\d{1,2}[.)])\s+", "", out)
    out = re.sub(r"\s+", " ", out)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", out) if len(s.split()) >= 3]
    sents = [s if s[-1] in ".!?" else s + "." for s in sents]
    return sents[:n]


def excerpt(sentences: list[str], limit_chars: int, owners: list[int] | None = None) -> str:
    """The sentences worth showing the model: requests/deadlines first in rank, then in the order written."""
    from .summarise import pick
    chosen = pick(sentences, min(len(sentences), 12), priority_first=False, owners=owners)
    out, used = [], 0
    for i in chosen:
        if used + len(sentences[i]) > limit_chars:
            continue
        out.append(sentences[i])
        used += len(sentences[i]) + 1
    return " ".join(out)


# --------------------------------------------------------------------------- the call
def run(sentences: list[str], n: int, cfg: dict, context: str = "", owners: list[int] | None = None) -> tuple[list[str] | None, dict]:
    """Ask the model.  Returns (summary or None, info) - info explains what happened (for logs and the Test button)."""
    info: dict = {"used_ai": False, "seconds": 0.0, "reason": ""}
    if not sentences:
        info["reason"] = "nothing to summarise"
        return None, info
    text = excerpt(sentences, int(cfg.get("max_input_chars", 1600)), owners)
    head = f"{context.strip()}\n" if context.strip() else ""      # e.g. "From: Dave Smith / Subject: Invoice" (also allowed in the answer)
    payload = {
        "model": cfg.get("model", "qwen2.5:0.5b"), "stream": False,
        "keep_alive": cfg.get("keep_alive", 0),
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": f"EMAIL:\n{head}{text}\n\nWrite the summary now (requests and deadlines first):"}],
        "options": {"temperature": 0.1, "num_ctx": int(cfg.get("num_ctx", 2048)),
                    "num_predict": int(cfg.get("num_predict", 160)), "num_thread": 4},
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
            ok, bad = grounded(sents, f"{context} " + " ".join(sentences))
            if ok:
                info["used_ai"] = True
                return sents, info
            info["reason"] = "rejected - mentioned things not in the email: " + ", ".join(bad[:6])
    finally:
        _busy_add(-1)
    info["seconds"] = info["seconds"] or round(time.time() - t0, 1)
    log.warning("AI summary not used (%s) - using the built-in summary", info["reason"])
    return None, info


def summarise(sentences: list[str], n: int, cfg: dict, context: str = "", owners: list[int] | None = None) -> tuple[list[str] | None, dict]:
    return run(sentences, n, cfg, context, owners)


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
