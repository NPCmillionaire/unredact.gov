from __future__ import annotations

import datetime as dt

from unredact.congress.directory import title
from unredact.congress.models import Constituent
from unredact.core.templating import paragraphs, render

PKG = "unredact.congress"


def salutation(member: dict) -> str:
    return f"Dear {title(member)} {member.get('last') or member['name']},"


def render_text(member: dict, c: Constituent, subject: str, body: str) -> str:
    return render(PKG, "message.txt.j2", salutation=salutation(member), paragraphs=paragraphs(body), c=c)


def render_letter_html(member: dict, office: dict | None, c: Constituent, subject: str, body: str,
                       today: dt.date | None = None) -> str:
    return render(
        PKG,
        "letter.html.j2",
        member=member,
        member_title=title(member),
        office=office,
        c=c,
        subject=subject,
        salutation=salutation(member),
        paragraphs=paragraphs(body),
        today=(today or dt.date.today()).strftime("%B %-d, %Y"),
    )
