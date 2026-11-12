"""Render request / follow-up / appeal letters."""
from __future__ import annotations

import datetime as dt

from unredact.core.templating import render
from unredact.foia.deadlines import jurisdiction

FEE_LABELS = {
    "media": "a representative of the news media",
    "educational": "affiliated with an educational institution",
    "scientific": "affiliated with a noncommercial scientific institution",
    "commercial": "a commercial-use requester",
    "other": "an 'all other' requester",
}

PKG = "unredact.foia"


def _fmt(d: str | dt.date | None) -> str | None:
    if not d:
        return None
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    return d.strftime("%B %-d, %Y")


def render_request(agency: dict, req: dict, requester: dict, today: dt.date | None = None) -> str:
    j = jurisdiction(req.get("jurisdiction"))
    return render(
        PKG,
        "request.txt.j2",
        today=_fmt(today or dt.date.today()),
        agency=agency,
        j=j,
        token=req["token"],
        subject=req["subject"],
        description=req["description"].strip(),
        fee_category=req.get("fee_category"),
        fee_label=FEE_LABELS.get(req.get("fee_category") or "", req.get("fee_category") or ""),
        fee_waiver=bool(req.get("fee_waiver")),
        fee_waiver_reason=req.get("fee_waiver_reason") or "",
        fee_limit=req.get("fee_limit"),
        expedited=bool(req.get("expedited")),
        expedited_reason=req.get("expedited_reason") or "",
        requester=requester,
    )


def render_followup(agency: dict, req: dict, requester: dict, today: dt.date | None = None) -> str:
    return render(
        PKG,
        "followup.txt.j2",
        today=_fmt(today or dt.date.today()),
        agency=agency,
        j=jurisdiction(req.get("jurisdiction")),
        token=req["token"],
        tracking_number=req.get("tracking_number"),
        submitted=_fmt(req.get("submitted_at")) or "previously",
        due=_fmt(req.get("due_at")),
        description=req["description"].strip(),
        requester=requester,
    )


def render_appeal(
    agency: dict, req: dict, requester: dict, grounds: str = "", today: dt.date | None = None
) -> str:
    return render(
        PKG,
        "appeal.txt.j2",
        today=_fmt(today or dt.date.today()),
        agency=agency,
        j=jurisdiction(req.get("jurisdiction")),
        token=req["token"],
        tracking_number=req.get("tracking_number"),
        submitted=_fmt(req.get("submitted_at")) or "previously",
        constructive=req.get("status") not in ("denied", "partial", "complete"),
        description=req["description"].strip(),
        grounds=grounds.strip(),
        requester=requester,
    )
