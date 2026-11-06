"""Request lifecycle: create, status transitions, events, overdue detection."""
from __future__ import annotations

import datetime as dt
import secrets
import sqlite3

from redtape.foia.deadlines import due_date

STATUSES = [
    "draft",
    "submitted",
    "acknowledged",
    "clarification",
    "fee_quote",
    "processing",
    "partial",
    "complete",
    "denied",
    "appealed",
    "withdrawn",
    "closed",
]
OPEN = ("submitted", "acknowledged", "clarification", "fee_quote", "processing", "partial", "appealed")


def new_token() -> str:
    return "RT-" + secrets.token_hex(3)


def create(con: sqlite3.Connection, *, agency_id: str, subject: str, description: str, jurisdiction: str = "federal", **opts) -> dict:
    token = new_token()
    con.execute(
        """INSERT INTO foia_requests (token, agency_id, jurisdiction, subject, description, fee_category, fee_limit,
             fee_waiver, fee_waiver_reason, expedited, expedited_reason)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            token,
            agency_id,
            jurisdiction,
            subject,
            description,
            opts.get("fee_category"),
            opts.get("fee_limit"),
            int(bool(opts.get("fee_waiver"))),
            opts.get("fee_waiver_reason"),
            int(bool(opts.get("expedited"))),
            opts.get("expedited_reason"),
        ),
    )
    con.commit()
    return get_by_token(con, token)


def get(con: sqlite3.Connection, req_id: int) -> dict | None:
    r = con.execute("SELECT * FROM foia_requests WHERE id = ?", (req_id,)).fetchone()
    return dict(r) if r else None


def get_by_token(con: sqlite3.Connection, token: str) -> dict | None:
    r = con.execute("SELECT * FROM foia_requests WHERE token = ?", (token,)).fetchone()
    return dict(r) if r else None


def list_requests(con: sqlite3.Connection, status: str | None = None) -> list[dict]:
    q = """SELECT r.*, a.title AS agency_title, a.abbreviation AS agency_abbr
           FROM foia_requests r JOIN foia_agencies a ON a.id = r.agency_id"""
    args: tuple = ()
    if status:
        q += " WHERE r.status = ?"
        args = (status,)
    return [dict(x) for x in con.execute(q + " ORDER BY r.id DESC", args).fetchall()]


def log(con: sqlite3.Connection, req_id: int, kind: str, detail: str | None = None, attachment: str | None = None) -> None:
    con.execute(
        "INSERT INTO foia_events (request_id, kind, detail, attachment) VALUES (?,?,?,?)",
        (req_id, kind, detail, attachment),
    )
    con.commit()


def events(con: sqlite3.Connection, req_id: int) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM foia_events WHERE request_id = ? ORDER BY id", (req_id,))]


def set_status(con: sqlite3.Connection, req_id: int, status: str, detail: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; choose from {', '.join(STATUSES)}")
    con.execute("UPDATE foia_requests SET status = ? WHERE id = ?", (status, req_id))
    log(con, req_id, f"status:{status}", detail)


def mark_submitted(con: sqlite3.Connection, req_id: int, channel: str, body: str, on: dt.date | None = None,
                   tracking_number: str | None = None, detail: str | None = None) -> dict:
    req = get(con, req_id)
    on = on or dt.date.today()
    due = due_date(req["jurisdiction"], on)
    con.execute(
        """UPDATE foia_requests SET status='submitted', channel=?, body=?, submitted_at=?, due_at=?,
             tracking_number=COALESCE(?, tracking_number) WHERE id=?""",
        (channel, body, on.isoformat(), due.isoformat() if due else None, tracking_number, req_id),
    )
    log(con, req_id, "submitted", detail or f"via {channel}")
    return get(con, req_id)


def overdue(con: sqlite3.Connection, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    marks = ",".join("?" * len(OPEN))
    rows = con.execute(
        f"""SELECT r.*, a.title AS agency_title, a.email AS agency_email FROM foia_requests r
            JOIN foia_agencies a ON a.id = r.agency_id
            WHERE r.status IN ({marks}) AND r.due_at IS NOT NULL AND r.due_at < ?""",
        (*OPEN, today.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def last_event_at(con: sqlite3.Connection, req_id: int, kind: str) -> dt.datetime | None:
    r = con.execute(
        "SELECT at FROM foia_events WHERE request_id=? AND kind=? ORDER BY id DESC LIMIT 1", (req_id, kind)
    ).fetchone()
    return dt.datetime.fromisoformat(r["at"]) if r else None
