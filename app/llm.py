"""Optional: abstractive summaries from a small local model via Ollama.

Off by default.  A Pi 3B+ has 1 GB of RAM, so only sub-1B models are realistic
(e.g. qwen2.5:0.5b) and a summary takes tens of seconds - fine for a printer
queue, but quality is below the big cloud models.  Any failure falls back to the
built-in extractive summariser, so enabling this can never lose a print.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request

log = logging.getLogger("llm")


def summarise(text: str, n: int, cfg: dict) -> list[str] | None:
    if not cfg.get("enabled"):
        return None
    prompt = (
        f"Summarise the email below in {max(3, n - 1)} to {n} short, plain sentences. "
        "Keep names, numbers, dates and amounts exact. Output only the summary, no preamble.\n\n"
        f"EMAIL:\n{text[:2500]}\n\nSUMMARY:"
    )
    body = json.dumps({"model": cfg.get("model", "qwen2.5:0.5b"), "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.2, "num_predict": 220}}).encode()
    req = urllib.request.Request(cfg.get("url", "http://127.0.0.1:11434").rstrip("/") + "/api/generate",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=int(cfg.get("timeout", 180))) as r:
            out = json.loads(r.read().decode())["response"].strip()
    except Exception as e:  # network down, model missing, timeout...
        log.warning("Local LLM unavailable (%s) - using built-in summariser", e)
        return None
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", out)) if len(s.split()) >= 3]
    return sents[:n] or None
