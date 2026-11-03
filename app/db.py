"""Tiny SQLite wrapper (history, print queue, processed emails, calendar events)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    kind TEXT, source TEXT, title TEXT, notes TEXT,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | scheduled | held | printed | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT, printed_at TEXT, last_try TEXT, png TEXT,
    due TEXT                                     -- scheduled jobs: when to print (UTC ISO); status 'scheduled' until then
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, id);
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY, ts TEXT NOT NULL, status TEXT, info TEXT
);
CREATE TABLE IF NOT EXISTS sheet_rows (
    id TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
    status TEXT NOT NULL,                        -- queued | empty | ignored | error | deleted
    jobs TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS ai_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL, job_id INTEGER,       -- the printout (jobs.id) this summary went onto
    subject TEXT, sender TEXT,
    email TEXT,                                  -- exactly what the model was shown (header + labelled thread)
    thread TEXT,                                 -- JSON: the cleaned messages
    used_ai INTEGER NOT NULL DEFAULT 0, model TEXT, seconds REAL, reason TEXT,
    bullets TEXT,                                -- JSON: the summary produced (info bullets + "ACTION: ..." lines)
    printed TEXT,                                -- JSON: {"summary": [...], "actions": [...]} as printed
    status TEXT NOT NULL DEFAULT 'new',          -- new | good | fix | bad
    comment TEXT, corrected TEXT,                -- your note, and your corrected bullets (JSON list)
    train INTEGER NOT NULL DEFAULT 1,
    symbols TEXT                                 -- JSON list of symbol ids it chose (null = none recorded)
);
CREATE TABLE IF NOT EXISTS cal_events (
    key TEXT PRIMARY KEY, cal_id TEXT, start TEXT, end TEXT,
    first_seen TEXT NOT NULL, due TEXT,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | printed | skipped | missed | removed
    data TEXT, printed_at TEXT
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            if "due" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}:      # databases from older versions
                self.conn.execute("ALTER TABLE jobs ADD COLUMN due TEXT")
            if "symbols" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(ai_runs)")}:
                self.conn.execute("ALTER TABLE ai_runs ADD COLUMN symbols TEXT")
            self.conn.commit()

    def execute(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def all(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
            return dict(r) if r else None
