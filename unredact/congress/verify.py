"""Confirm a constituent controls the email address before anything is delivered in their name."""
from __future__ import annotations

import secrets
import sqlite3

from unredact.config import Settings, get_settings
from unredact.core.mailer import send_email

EXPIRY = "-2 days"


def start(con: sqlite3.Connection, batch: str, email: str, order_id: int | None, n_offices: int,
          s: Settings | None = None) -> str:
    s = s or get_settings()
    token = secrets.token_urlsafe(24)
    con.execute("INSERT INTO verifications (token, batch, email, order_id) VALUES (?,?,?,?)",
                (token, batch, email.lower(), order_id))
    con.commit()
    link = f"{s.base_url}/congress/verify/{token}"
    send_email(
        email,
        "Confirm your message to Congress",
        f"Someone (hopefully you) asked unredact.me to send a message to {n_offices} congressional "
        f"office(s) in your name.\n\nConfirm and send it:\n{link}\n\n"
        "If this wasn't you, ignore this email and nothing will be sent. The link expires in 48 hours.\n",
        settings=s,
    )
    return token


def confirm(con: sqlite3.Connection, token: str) -> dict | None:
    """Mark verified and release the batch's electronic messages. Idempotent; None if unknown/expired."""
    row = con.execute(
        f"SELECT * FROM verifications WHERE token = ? AND created_at > datetime('now', '{EXPIRY}')", (token,)
    ).fetchone()
    if not row:
        return None
    if not row["verified_at"]:
        con.execute("UPDATE verifications SET verified_at = datetime('now') WHERE token = ?", (token,))
        con.execute("UPDATE messages SET status = 'queued' WHERE batch = ? AND status = 'unverified'", (row["batch"],))
        con.commit()
    return dict(con.execute("SELECT * FROM verifications WHERE token = ?", (token,)).fetchone())
