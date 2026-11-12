"""SQLite storage (single file, no ORM). Schema is created and migrated on connect."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from unredact.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  organization TEXT NOT NULL DEFAULT '',
  fee_category TEXT,
  plan TEXT NOT NULL DEFAULT 'free',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS foia_agencies (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  abbreviation TEXT,
  agency TEXT,
  agency_abbr TEXT,
  email TEXT,
  submission_format TEXT,
  website TEXT,
  jurisdiction TEXT NOT NULL DEFAULT 'federal',
  raw TEXT
);

CREATE TABLE IF NOT EXISTS foia_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT UNIQUE NOT NULL,
  agency_id TEXT NOT NULL REFERENCES foia_agencies(id),
  jurisdiction TEXT NOT NULL DEFAULT 'federal',
  subject TEXT NOT NULL,
  description TEXT NOT NULL,
  fee_category TEXT,
  fee_limit REAL,
  fee_waiver INTEGER NOT NULL DEFAULT 0,
  fee_waiver_reason TEXT,
  expedited INTEGER NOT NULL DEFAULT 0,
  expedited_reason TEXT,
  body TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  channel TEXT,
  tracking_number TEXT,
  submitted_at TEXT,
  due_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS foia_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id INTEGER NOT NULL REFERENCES foia_requests(id),
  kind TEXT NOT NULL,
  at TEXT NOT NULL DEFAULT (datetime('now')),
  detail TEXT,
  attachment TEXT
);

CREATE TABLE IF NOT EXISTS members (
  bioguide TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  first TEXT,
  last TEXT,
  chamber TEXT NOT NULL,          -- 'sen' | 'rep'
  state TEXT NOT NULL,
  district INTEGER,               -- reps only; 0 = at-large
  party TEXT,
  url TEXT,
  contact_form TEXT,
  phone TEXT,
  dc_address TEXT,
  raw TEXT
);
CREATE INDEX IF NOT EXISTS members_loc ON members(state, chamber, district);

CREATE TABLE IF NOT EXISTS district_offices (
  id TEXT PRIMARY KEY,
  bioguide TEXT NOT NULL,
  address TEXT,
  suite TEXT,
  building TEXT,
  city TEXT,
  state TEXT,
  zip TEXT,
  phone TEXT,
  lat REAL,
  lon REAL
);
CREATE INDEX IF NOT EXISTS offices_bio ON district_offices(bioguide);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stripe_session TEXT UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending',
  amount_cents INTEGER,
  email TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch TEXT NOT NULL,
  kind TEXT NOT NULL,             -- 'electronic' | 'letter'
  bioguide TEXT NOT NULL,
  constituent TEXT NOT NULL,      -- JSON
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  topic TEXT,
  channel TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  external_id TEXT,
  error TEXT,
  order_id INTEGER REFERENCES orders(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  sent_at TEXT
);
CREATE INDEX IF NOT EXISTS messages_batch ON messages(batch);
"""


# Columns added after v0.1. SQLite can't ADD COLUMN IF NOT EXISTS, so check first.
MIGRATIONS = {
    "foia_requests": [
        ("user_id", "INTEGER REFERENCES users(id)"),
        ("slug", "TEXT"),
        ("public", "INTEGER NOT NULL DEFAULT 0"),
        ("closed_at", "TEXT"),
    ],
}


def migrate(con: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS foia_requests_slug ON foia_requests(slug)")
    con.execute("CREATE INDEX IF NOT EXISTS foia_requests_user ON foia_requests(user_id)")
    con.commit()


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    migrate(con)
    return con


def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None
