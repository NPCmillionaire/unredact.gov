"""Outbound SMTP."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from unredact.config import Settings, get_settings


def send_email(
    to: str | list[str],
    subject: str,
    body: str,
    *,
    reply_to: str | None = None,
    headers: dict[str, str] | None = None,
    settings: Settings | None = None,
) -> str:
    """Send a plain-text email. Returns the Message-ID."""
    s = settings or get_settings()
    if not s.smtp_ready:
        raise RuntimeError("SMTP not configured (set SMTP_HOST and SMTP_FROM)")
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(to) if isinstance(to, list) else to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    domain = s.smtp_from.split("@")[-1].strip(">") or None
    msg["Message-ID"] = make_msgid(domain=domain)
    if reply_to:
        msg["Reply-To"] = reply_to
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if s.smtp_port == 465:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=30) as smtp:
            _login_send(smtp, s, msg)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ctx)
            _login_send(smtp, s, msg)
    return msg["Message-ID"]


def _login_send(smtp: smtplib.SMTP, s: Settings, msg: EmailMessage) -> None:
    if s.smtp_user:
        smtp.login(s.smtp_user, s.smtp_password)
    smtp.send_message(msg)
