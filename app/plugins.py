"""Drop-in Python plugins for integrations the settings page can't cover.

Put a ``.py`` file in ``plugins/``.  Any of these optional functions are called:

    process(card, ctx)      -> Card | list[Card] | None   (None drops the card)
    on_printed(card_info)   -> None                       (after a successful print)
    register(app)           -> None                       (add your own Flask routes)

``ctx`` is a dict: {"incoming": Incoming | None, "config": dict, "source": str}.
See plugins/example_tagger.py.  Plugins run with full privileges - only install
code you trust.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from .config import ROOT

log = logging.getLogger("plugins")
_loaded: list = []


def load(enabled: bool = True) -> None:
    _loaded.clear()
    if not enabled:
        return
    d = ROOT / "plugins"
    for p in sorted(d.glob("*.py")) if d.exists() else []:
        if p.name.startswith("_") or p.stem.startswith("example_") and not p.with_suffix(".enabled").exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"plugin_{p.stem}", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            _loaded.append(mod)
            log.info("Loaded plugin %s", p.name)
        except Exception:
            log.exception("Plugin %s failed to load", p.name)


def names() -> list[str]:
    return [m.__name__.removeprefix("plugin_") for m in _loaded]


def process(cards: list, ctx: dict) -> list:
    for mod in _loaded:
        fn = getattr(mod, "process", None)
        if not fn:
            continue
        out = []
        for c in cards:
            try:
                r = fn(c, ctx)
            except Exception:
                log.exception("Plugin %s.process failed", mod.__name__)
                r = c
            if r is None:
                continue
            out.extend(r if isinstance(r, list) else [r])
        cards = out
    return cards


def on_printed(info: dict) -> None:
    for mod in _loaded:
        fn = getattr(mod, "on_printed", None)
        if fn:
            try:
                fn(info)
            except Exception:
                log.exception("Plugin %s.on_printed failed", mod.__name__)


def register_routes(app) -> None:
    for mod in _loaded:
        fn = getattr(mod, "register", None)
        if fn:
            try:
                fn(app)
            except Exception:
                log.exception("Plugin %s.register failed", mod.__name__)
