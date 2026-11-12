"""Exhaustion of administrative remedies + lawsuit-ready packet.

Federal FOIA only. State enforcement varies too much to automate; the packet says so.
Not legal advice. The packet tells the reader to have an attorney review it.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sqlite3
import zipfile
from pathlib import Path

from unredact.foia import directory, tracker
from unredact.foia.deadlines import add_business_days, jurisdiction

APPEAL_WINDOW_DAYS = 90  # 5 U.S.C. § 552(a)(6)(A)(i)(III)(aa), as amended by the FOIA Improvement Act of 2016


def _date(v) -> dt.date | None:
    if not v:
        return None
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])


def appealed_on(con: sqlite3.Connection, req_id: int) -> dt.date | None:
    for e in tracker.events(con, req_id):
        if e["kind"] in ("appeal", "status:appealed"):
            return _date(e["at"])
    return None


def exhaustion(con: sqlite3.Connection, req: dict, today: dt.date | None = None) -> dict:
    """{"ripe": True | False | None, "why": str, "appeal_deadline": date | None}"""
    today = today or dt.date.today()
    out = {"ripe": False, "why": "", "appeal_deadline": None}
    if req["jurisdiction"] not in (None, "", "federal"):
        j = jurisdiction(req["jurisdiction"])
        out.update(ripe=None, why=f"{j.get('law', 'State law')}: enforcement differs by state (court, ombudsman or "
                                   "attorney general). Check the statute before filing anything.")
        return out
    st = req["status"]
    if st == "draft":
        out["why"] = "Not submitted yet."
    elif st in ("complete", "withdrawn", "closed"):
        out["why"] = "Closed. Records released or request withdrawn."
    elif st == "appealed":
        ad = appealed_on(con, req["id"])
        if not ad:
            out.update(ripe=None, why="Marked appealed, but no appeal date is recorded.")
        else:
            decision_due = add_business_days(ad, 20)
            if today > decision_due:
                out.update(ripe=True, why=f"Appeal filed {ad}. The 20-business-day decision deadline ({decision_due}) "
                                          "passed with no decision, so administrative remedies are constructively "
                                          "exhausted (5 U.S.C. § 552(a)(6)(A)(ii), (C)(i)).")
            else:
                out["why"] = f"Appeal filed {ad}. Decision due by {decision_due}."
    elif st == "denied":
        closed = _date(req.get("closed_at"))
        if closed:
            out["appeal_deadline"] = closed + dt.timedelta(days=APPEAL_WINDOW_DAYS)
        out["why"] = ("Adverse determination. You generally have to file an administrative appeal before suing"
                      + (f". Appeal deadline: {out['appeal_deadline']}." if out["appeal_deadline"] else "."))
    else:
        due = _date(req.get("due_at"))
        if due and today > due:
            out.update(ripe=True, why=f"No determination by the statutory deadline ({due}). Administrative remedies "
                                      "are constructively exhausted (5 U.S.C. § 552(a)(6)(C)(i)). If the agency "
                                      "responds before you file, courts may require an appeal first (Oglesby v. "
                                      "U.S. Dep't of the Army, 920 F.2d 57 (D.C. Cir. 1990)).")
        else:
            out["why"] = f"Inside the response window (due {due or 'n/a'})."
    return out


def build_packet(con: sqlite3.Connection, req_id: int, attachments_root: Path) -> bytes:
    """Zip: README.md (facts, exhaustion, venue, timeline), letter.txt, records/, xray reports."""
    req = tracker.get(con, req_id)
    agency = directory.get(con, req["agency_id"]) or {"title": req["agency_id"]}
    ev = tracker.events(con, req_id)
    ex = exhaustion(con, req)
    j = jurisdiction(req["jurisdiction"])
    fed = req["jurisdiction"] in (None, "", "federal")

    lines = [
        f"# {req['subject']}  [{req['token']}]", "",
        f"- **Agency:** {agency['title']}" + (f", {agency.get('agency')}" if agency.get("agency") else ""),
        f"- **Law:** {j.get('law', '')}",
        f"- **Agency tracking no.:** {req.get('tracking_number') or 'none assigned'}",
        f"- **Submitted:** {req.get('submitted_at') or 'not submitted'} via {req.get('channel') or '-'}",
        f"- **Statutory response due:** {req.get('due_at') or 'no fixed clock'}",
        f"- **Status:** {req['status']}", "",
        "## Can the requester sue yet?", "",
        {True: "**Yes, ripe.** ", False: "**Not yet.** ", None: "**Unclear.** "}[ex["ripe"]] + ex["why"], "",
    ]
    if fed:
        lines += [
            "Venue (5 U.S.C. § 552(a)(4)(B)): the U.S. district court where the requester lives or has their "
            "principal place of business, where the records are located, or the District of Columbia. "
            "The court reviews de novo, and the agency has the burden of justifying withholdings. Limitations: "
            "6 years (28 U.S.C. § 2401(a)). A substantially prevailing plaintiff may recover attorney fees "
            "(§ 552(a)(4)(E)).", "",
        ]
    lines += ["## Records requested", "", req["description"].strip(), "", "## Timeline", "",
              "| When (UTC) | Event | Detail |", "|---|---|---|"]
    for e in ev:
        detail = (e.get("detail") or "").replace("|", "/").replace("\n", " ")[:300]
        lines.append(f"| {e['at']} | {e['kind']} | {detail} |")
    lines += ["", "_Generated by unredact.me. This is not legal advice. Have an attorney review it before filing._"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", "\n".join(lines) + "\n")
        if req.get("body"):
            z.writestr("letters/00-request.txt", req["body"])
        d = attachments_root / req["token"]
        if d.exists():
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(d)
                    z.write(f, str(rel) if rel.parts[0] == "letters" else f"records/{rel}")
        z.writestr("events.json", json.dumps(ev, indent=2, default=str))
    return buf.getvalue()
