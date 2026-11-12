"""Queue + dispatch constituent messages across channels."""
from __future__ import annotations

import json
import secrets
import sqlite3

from unredact.config import Settings, get_settings
from unredact.congress import directory
from unredact.congress.compose import render_letter_html, render_text
from unredact.congress.deliver import DeliveryResult, cwc, lob, webform
from unredact.congress.models import Constituent


class DuplicateMessage(Exception):
    pass


def new_batch() -> str:
    return secrets.token_urlsafe(12)


def queue(con: sqlite3.Connection, *, batch: str, kind: str, members: list[dict], c: Constituent, subject: str,
          body: str, topic: str | None = None, order_id: int | None = None, status: str = "queued") -> list[int]:
    """One row per member. Rejects the same constituent sending the same subject to the same office twice in 24h."""
    ids = []
    for m in members:
        dup = con.execute(
            """SELECT 1 FROM messages WHERE bioguide=? AND kind=? AND subject=?
               AND json_extract(constituent,'$.email')=? AND created_at > datetime('now','-1 day')""",
            (m["bioguide"], kind, subject, c.email.lower()),
        ).fetchone()
        if dup:
            raise DuplicateMessage(f"already sent '{subject}' to {m['name']} in the last 24h")
        d = c.to_dict()
        d["email"] = c.email.lower()
        cur = con.execute(
            """INSERT INTO messages (batch, kind, bioguide, constituent, subject, body, topic, order_id, status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (batch, kind, m["bioguide"], json.dumps(d), subject, body, topic, order_id, status),
        )
        ids.append(cur.lastrowid)
    con.commit()
    return ids


def choose_channel(member: dict, s: Settings) -> str:
    if member["chamber"] == "rep" and s.cwc_ready:
        return "cwc"
    if webform.load_definition(s.webform_defs_dir, member["bioguide"]):
        return "webform"
    return "manual"


def _finish(con: sqlite3.Connection, msg_id: int, channel: str, res: DeliveryResult) -> DeliveryResult:
    con.execute(
        """UPDATE messages SET channel=?, status=?, external_id=?, error=?,
             sent_at=CASE WHEN ?='sent' THEN datetime('now') ELSE sent_at END WHERE id=?""",
        (channel, res.status, res.external_id, None if res.status == "sent" else res.detail, res.status, msg_id),
    )
    con.commit()
    return res


def deliver(con: sqlite3.Connection, msg_id: int, s: Settings | None = None) -> DeliveryResult:
    s = s or get_settings()
    msg = dict(con.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone())
    if msg["status"] in ("sent",):
        return DeliveryResult("sent", msg["external_id"], "already sent")
    member = directory.get_member(con, msg["bioguide"])
    c = Constituent.from_dict(json.loads(msg["constituent"]))

    if msg["kind"] == "letter":
        near = (c.lat, c.lon) if c.lat is not None and c.lon is not None else None
        office = directory.pick_office(con, member["bioguide"], near)
        if not office:
            return _finish(con, msg_id, "lob", DeliveryResult("failed", detail="no district office address on file"))
        html = render_letter_html(member, office, c, msg["subject"], msg["body"])
        res = lob.send_letter(
            to=lob.office_address(member, office), from_=c.lob_address(), html=html,
            description=f"unredact msg {msg_id} to {member['bioguide']}", idempotency_key=f"unredact-{msg_id}", s=s,
        )
        return _finish(con, msg_id, "lob", res)

    text = render_text(member, c, msg["subject"], msg["body"])
    channel = choose_channel(member, s)
    if channel == "cwc":
        res = cwc.send(member, c, msg["subject"], text, msg["topic"], s)
    elif channel == "webform":
        defn = webform.load_definition(s.webform_defs_dir, member["bioguide"])
        res = webform.run(defn, webform.build_vars(c, msg["subject"], text, msg["topic"]))
    else:
        url = member.get("contact_form") or member.get("url") or ""
        res = DeliveryResult("manual", detail=f"No automated channel for this office yet — submit at {url}")
    return _finish(con, msg_id, channel, res)


def batch_status(con: sqlite3.Connection, batch: str) -> list[dict]:
    rows = con.execute(
        """SELECT m.*, mem.name AS member_name, mem.chamber, mem.contact_form FROM messages m
           JOIN members mem ON mem.bioguide = m.bioguide WHERE m.batch = ? ORDER BY m.kind, m.id""",
        (batch,),
    ).fetchall()
    return [dict(r) for r in rows]
