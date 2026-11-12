"""Poll an IMAP inbox for agency replies, match them to requests, classify, save attachments."""
from __future__ import annotations

import email
import imaplib
import json
import re
import sqlite3
from email import policy
from email.message import EmailMessage
from pathlib import Path

from unredact.config import Settings, get_settings
from unredact.foia import tracker

TOKEN_RE = re.compile(r"\b(?:UR|RT)-[0-9a-f]{6}\b")
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


def classify_rules(text: str) -> str:
    for kind, rx in RULES:
        if rx.search(text):
            return kind
    return "message"


LLM_KINDS = ("acknowledged", "clarification", "fee_quote", "processing", "partial", "complete", "denied", "message")
LLM_PROMPT = (
    "You triage email from a government records office about a FOIA / public-records request. Reply with ONLY "
    "one word from this list: " + ", ".join(LLM_KINDS) + ". 'message' means the email doesn't change the request's "
    "state. 'denied' includes 'no responsive records'. 'complete' means a final response."
)


def classify_llm(text: str, s: Settings | None = None) -> str | None:
    s = s or get_settings()
    if not s.anthropic_api_key:
        return None
    import httpx

    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages", timeout=30,
            headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
            json={"model": s.llm_model, "max_tokens": 5, "system": LLM_PROMPT,
                  "messages": [{"role": "user", "content": text[:12000]}]},
        )
        r.raise_for_status()
        word = "".join(b.get("text", "") for b in r.json().get("content", [])).strip().lower().strip(".")
        return word if word in LLM_KINDS else None
    except Exception:  # noqa: BLE001 - any failure falls back to rules
        return None


def classify(text: str, s: Settings | None = None) -> str:
    """Rules first; if an LLM key is configured it gets the final say (and rules are the fallback)."""
    return classify_llm(text, s) or classify_rules(text)


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
    leaks = xray_files(con, req["id"], saved)
    notify_owner(con, req, kind, subject, snippet, leaks)
    return req, kind


def xray_files(con: sqlite3.Connection, req_id: int, paths: list[str]) -> int:
    """Scan saved PDFs for leaky redactions; write <file>.xray.json and log hits. Returns total leaks."""
    from unredact.foia import xray

    if not xray.available():
        return 0
    total = 0
    for p in paths:
        if not p.lower().endswith(".pdf"):
            continue
        try:
            res = xray.scan(p)
        except Exception as exc:  # noqa: BLE001 - a corrupt PDF must not break mail processing
            tracker.log(con, req_id, "xray:error", f"{Path(p).name}: {exc}")
            continue
        Path(p + ".xray.json").write_text(json.dumps(res, indent=2))
        n = len(res["findings"])
        total += n
        if n:
            tracker.log(con, req_id, "xray:leak", f"{Path(p).name}: {n} redaction(s) with recoverable text "
                                                  f"(pages {sorted({f['page'] for f in res['findings']})})",
                        attachment=p + ".xray.json")
    return total


def notify_owner(con: sqlite3.Connection, req: dict, kind: str, subject: str, snippet: str, leaks: int = 0) -> None:
    """Email the request's owner that the agency replied. Silent if SMTP isn't set up."""
    from unredact import users
    from unredact.core.mailer import send_email

    s = get_settings()
    u = users.get(con, req.get("user_id"))
    to = u["email"] if u else s.requester_email
    if not (to and s.smtp_ready):
        return
    extra = f"\n\nX-ray: {leaks} redaction(s) in the released files still contain recoverable text." if leaks else ""
    try:
        send_email(to, f"[{req['token']}] Agency replied: {kind}",
                   f"{req['subject']}\n\nNew reply ({kind}): {subject}\n\n{snippet}{extra}\n\n"
                   f"{s.base_url}/foia/{req['id']}", settings=s)
    except Exception:  # noqa: BLE001
        pass


def message_from_postmark(payload: dict) -> EmailMessage:
    """Turn a Postmark inbound-webhook JSON payload into an EmailMessage for process_message()."""
    import base64

    msg = EmailMessage()
    msg["From"] = payload.get("From", "")
    msg["To"] = payload.get("To", "")
    msg["Subject"] = payload.get("Subject", "")
    text = payload.get("TextBody") or re.sub(r"<[^>]+>", " ", payload.get("HtmlBody") or "")
    msg.set_content(text or "")
    for a in payload.get("Attachments") or []:
        data = base64.b64decode(a.get("Content") or "")
        maintype, _, subtype = (a.get("ContentType") or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=a.get("Name") or "attachment.bin")
    return msg


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
