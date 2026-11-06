"""Poll an IMAP inbox for agency replies, match them to requests, classify, save attachments."""
from __future__ import annotations

import email
import imaplib
import re
import sqlite3
from email import policy
from email.message import EmailMessage
from pathlib import Path

from redtape.config import Settings, get_settings
from redtape.foia import tracker

TOKEN_RE = re.compile(r"\bRT-[0-9a-f]{6}\b")
TRACKING_RE = re.compile(
    r"(?:tracking|request|case|reference|control)\s*(?:number|no\.?|#)\s*(?:is\s*)?[:#]?\s*([A-Z0-9][A-Z0-9\-]{4,})",
    re.I,
)

# Order matters: most decisive outcomes first.
RULES: list[tuple[str, re.Pattern]] = [
    ("complete", re.compile(r"\b(final response|released? in full|responsive records (are|is) (attached|enclosed)|request (is|has been) (closed|completed))\b", re.I)),
    ("partial", re.compile(r"\b(interim (response|release)|partial(ly)? (grant|release|response)|released in part)\b", re.I)),
    ("denied", re.compile(r"\b(denied|withheld in (full|(their|its) entirety)|no (responsive )?records|unable to locate)\b", re.I)),
    ("fee_quote", re.compile(r"\b(fee estimate|estimated (fee|cost)s?|advance payment|fees? (of|totaling|will be|in the amount))\b", re.I)),
    ("clarification", re.compile(r"\b(clarif\w*|narrow the scope|perfect your request|more specific)\b", re.I)),
    ("acknowledged", re.compile(r"\b(acknowledg\w*|received your (foia |public records )?request|assigned (tracking|request|reference) (number|no))\b", re.I)),
]


def classify(text: str) -> str:
    for kind, rx in RULES:
        if rx.search(text):
            return kind
    return "message"


def _text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    body = part.get_content()
    if part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    return body


def match_request(con: sqlite3.Connection, subject: str, body: str) -> dict | None:
    m = TOKEN_RE.search(subject) or TOKEN_RE.search(body)
    if m:
        return tracker.get_by_token(con, m.group(0))
    t = TRACKING_RE.search(subject + "\n" + body)
    if t:
        r = con.execute("SELECT * FROM foia_requests WHERE tracking_number = ?", (t.group(1),)).fetchone()
        if r:
            return dict(r)
    return None


def process_message(con: sqlite3.Connection, msg: EmailMessage, attachments_dir: Path) -> tuple[dict, str] | None:
    subject = str(msg.get("Subject", ""))
    body = _text(msg)
    req = match_request(con, subject, body)
    if not req:
        return None
    kind = classify(subject + "\n" + body)

    saved = []
    for part in msg.iter_attachments():
        name = part.get_filename() or "attachment.bin"
        name = re.sub(r"[^\w.\-]", "_", name)
        dest_dir = attachments_dir / req["token"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        i = 1
        while dest.exists():
            dest = dest_dir / f"{dest.stem}-{i}{dest.suffix}"
            i += 1
        dest.write_bytes(part.get_payload(decode=True) or b"")
        saved.append(str(dest))

    t = TRACKING_RE.search(subject + "\n" + body)
    if t and not req.get("tracking_number"):
        con.execute("UPDATE foia_requests SET tracking_number=? WHERE id=?", (t.group(1), req["id"]))

    snippet = re.sub(r"\s+", " ", body).strip()[:500]
    tracker.log(con, req["id"], f"reply:{kind}", f"From {msg.get('From')}: {subject} — {snippet}",
                attachment=";".join(saved) or None)
    if kind != "message" and req["status"] not in ("withdrawn", "closed"):
        tracker.set_status(con, req["id"], kind, "auto-classified from reply")
    return req, kind


def poll(con: sqlite3.Connection, s: Settings | None = None) -> list[tuple[str, str]]:
    s = s or get_settings()
    if not s.imap_ready:
        raise RuntimeError("IMAP not configured (IMAP_HOST / IMAP_USER / IMAP_PASSWORD)")
    out: list[tuple[str, str]] = []
    M = imaplib.IMAP4_SSL(s.imap_host)
    try:
        M.login(s.imap_user, s.imap_password)
        M.select(s.imap_folder)
        _, data = M.search(None, "UNSEEN")
        for num in data[0].split():
            _, msgdata = M.fetch(num, "(BODY.PEEK[])")
            raw = msgdata[0][1]
            msg = email.message_from_bytes(raw, policy=policy.default)
            res = process_message(con, msg, s.attachments_dir)
            if res:
                M.store(num, "+FLAGS", "\\Seen")
                out.append((res[0]["token"], res[1]))
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out
