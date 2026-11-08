"""Asking the local AI to double-check what the rules found, before a slip prints (only when AI mode is on).

The built-in rules find the birthday / the movie title; the AI then reads the original message and says whether that makes sense and is what a
message like that would be expected to say.  It can confirm, correct (only with words that are really in the message) or reject.

It must never hold a print up for long (a Raspberry Pi 3B+ can take minutes to answer): the question has a time limit (``llm.check_seconds``, default
20 s).  If the AI isn't done in time, or is still busy with an earlier question, or fails, the slip prints exactly as the rules read it.
"""
from __future__ import annotations

import logging
import re
import threading

from . import llm

log = logging.getLogger("aicheck")
_busy = threading.Event()          # an earlier check is still running (e.g. it timed out): don't queue another behind it


def available(cfg: dict, flag: bool = True) -> bool:
    return bool(flag) and cfg.get("summary", {}).get("backend") == "ollama"


def ask(system: str, user: str, cfg: dict, num_predict: int = 60) -> str | None:
    """The AI's answer, or None (AI off / too slow / busy / failed)."""
    seconds = float(cfg.get("llm", {}).get("check_seconds", 20) or 20)
    if _busy.is_set():
        log.info("AI check skipped: the AI is still busy with an earlier one")
        return None
    out: dict = {}

    def work():
        try:
            out["text"], out["info"] = llm.generate(system, user, cfg["llm"], num_predict=num_predict)
        except Exception as e:                                              # noqa: BLE001
            log.info("AI check failed: %s", e)
        finally:
            _busy.clear()

    _busy.set()
    t = threading.Thread(target=work, name="ai-check", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        log.info("AI check took longer than %.0fs: printing without it", seconds)
        return None
    if not out.get("text"):
        log.info("AI check gave no answer (%s)", (out.get("info") or {}).get("reason", ""))
        return None
    return out["text"]


def first_line(reply: str) -> str:
    for ln in (reply or "").splitlines():
        ln = re.sub(r"^[\s>*\-•`\"']+|[`\"'\s]+$", "", ln.replace("**", ""))
        if ln:
            return ln
    return ""


def in_text(value: str, text: str) -> bool:
    """Is ``value`` really in ``text`` (ignoring case and punctuation)?  Anything the AI says must pass this."""
    norm = lambda s: " " + re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip() + " "
    v = norm(value).strip()
    return bool(v) and f" {v} " in norm(text)
