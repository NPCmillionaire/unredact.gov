"""Submission channels for FOIA requests."""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass

from unredact.config import Settings, get_settings
from unredact.core.mailer import send_email
from unredact.foia import directory, tracker
from unredact.foia.compose import render_request
from unredact.users import requester_for

FOIA_GOV_PORTAL = "https://www.foia.gov/agency-search.html"


@dataclass
class SubmitResult:
    channel: str
    ok: bool
    detail: str


def build_body(con: sqlite3.Connection, req_id: int, s: Settings | None = None) -> tuple[dict, dict, str]:
    s = s or get_settings()
    req = tracker.get(con, req_id)
    if not req:
        raise LookupError(f"no request #{req_id}")
    agency = directory.get(con, req["agency_id"])
    return req, agency, render_request(agency, req, requester_for(con, req, s))


def submit(
    con: sqlite3.Connection,
    req_id: int,
    channel: str = "auto",
    *,
    to: str | None = None,
    tracking_number: str | None = None,
    s: Settings | None = None,
) -> SubmitResult:
    """channel: auto | email | manual.

    * email  — sends the letter to the agency's FOIA inbox (or `to`), tagged with the request token.
    * manual — you filed it yourself (FOIA.gov portal, GovQA, NextRequest, fax, paper); records the
               submission date so deadlines + follow-ups still work.

    Direct FOIA.gov portal submission is deliberately not automated: the portal's submission API is
    meant for agency case-management integrations, so file through the portal UI and record it with
    --channel manual --tracking <number>.
    """
    s = s or get_settings()
    req, agency, body = build_body(con, req_id, s)
    if req["status"] != "draft":
        raise RuntimeError(f"request {req['token']} is already {req['status']}")
    who = requester_for(con, req, s)
    if not who.get("name") or not who.get("email"):
        raise RuntimeError("requester name/email missing: set REQUESTER_NAME and REQUESTER_EMAIL (operator) or "
                           "INBOUND_ADDRESS and a profile name (web users)")

    dest = to or agency.get("email")
    if channel == "auto":
        channel = "email" if dest and s.smtp_ready else "manual"

    if channel == "email":
        if not dest:
            raise RuntimeError(f"no email on file for {agency['title']}; pass --to or use --channel manual")
        subject = f"{tracker_subject(req)}"
        msg_id = send_email(
            dest,
            subject,
            body,
            reply_to=who["email"],
            headers={"X-Unredact-Token": req["token"]},
            settings=s,
        )
        tracker.mark_submitted(con, req_id, "email", body, detail=f"emailed {dest} ({msg_id})")
        return SubmitResult("email", True, f"Sent to {dest}")

    if channel == "manual":
        tracker.mark_submitted(con, req_id, "manual", body, tracking_number=tracking_number,
                               detail="filed manually" + (f", tracking {tracking_number}" if tracking_number else ""))
        where = agency.get("website") or FOIA_GOV_PORTAL
        return SubmitResult("manual", True, f"Recorded as submitted. File it at: {where}")

    raise ValueError(f"unknown channel {channel!r}")


def tracker_subject(req: dict) -> str:
    return f"Public records request: {req['subject']} [{req['token']}]"


def send_letter(con: sqlite3.Connection, req_id: int, kind: str, body: str, to: str, s: Settings | None = None) -> None:
    """Send a follow-up / appeal email and log it."""
    s = s or get_settings()
    req = tracker.get(con, req_id)
    prefix = {"followup": "Status inquiry", "appeal": "APPEAL"}.get(kind, kind)
    send_email(to, f"{prefix}: {req['subject']} [{req['token']}]", body, reply_to=requester_for(con, req, s)["email"],
               headers={"X-Unredact-Token": req["token"]}, settings=s)
    tracker.log(con, req_id, kind, f"emailed {to} on {dt.date.today().isoformat()}",
                attachment=save_letter(req, kind, body, s))


def save_letter(req: dict, kind: str, body: str, s: Settings | None = None) -> str:
    """Keep the full text of every outbound letter next to the request's records (goes in the packet)."""
    s = s or get_settings()
    d = s.attachments_dir / req["token"] / "letters"
    d.mkdir(parents=True, exist_ok=True)
    n = len(list(d.glob("*.txt"))) + 1
    f = d / f"{n:02d}-{kind}-{dt.date.today().isoformat()}.txt"
    f.write_text(body)
    return str(f)
