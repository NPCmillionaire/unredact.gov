"""unredact CLI."""
from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Optional

import typer

from unredact.config import get_settings
from unredact.db import connect

app = typer.Typer(help="Automate FOIA requests and messages to Congress.", no_args_is_help=True)
foia = typer.Typer(help="FOIA / public-records requests.", no_args_is_help=True)
congress = typer.Typer(help="Contact your members of Congress.", no_args_is_help=True)
app.add_typer(foia, name="foia")
app.add_typer(congress, name="congress")


def _ok(b: bool) -> str:
    return typer.style("yes", fg="green") if b else typer.style("no", fg="red")


@app.command()
def doctor():
    """Show which integrations are configured."""
    s = get_settings()
    con = connect()
    n_ag = con.execute("SELECT count(*) FROM foia_agencies").fetchone()[0]
    n_mem = con.execute("SELECT count(*) FROM members").fetchone()[0]
    typer.echo(f"data dir         {s.data_dir}")
    typer.echo(f"requester        {s.requester_name or '-'} <{s.requester_email or '-'}>")
    typer.echo(f"SMTP             {_ok(s.smtp_ready)}")
    typer.echo(f"IMAP             {_ok(s.imap_ready)}")
    typer.echo(f"FOIA.gov key     {_ok(bool(s.foia_gov_api_key))}   agencies loaded: {n_ag}")
    typer.echo(f"members loaded   {n_mem}")
    typer.echo(f"geocoder         {'Geocodio' if s.geocodio_api_key else 'US Census (free)'}")
    typer.echo(f"House CWC        {_ok(s.cwc_ready)}  host={s.cwc_host}")
    typer.echo(f"web-form defs    {_ok(s.webform_defs_dir.exists())}  {s.webform_defs_dir}")
    typer.echo(f"Lob              {_ok(s.lob_ready)}")
    typer.echo(f"Stripe           {_ok(s.stripe_ready)}  ${s.letter_price_cents / 100:.2f}/letter")
    from unredact.foia import xray

    n_users = con.execute("SELECT count(*) FROM users").fetchone()[0]
    typer.echo(f"users            {n_users}   signup {'open' if s.allow_signup else 'closed'}   admins: {', '.join(s.admin_emails) or '-'}")
    typer.echo(f"inbound webhook  {_ok(bool(s.inbound_key and s.inbound_address))}  {s.inbound_address or '-'}")
    typer.echo(f"x-ray (pymupdf)  {_ok(xray.available())}")
    typer.echo(f"LLM classifier   {_ok(bool(s.anthropic_api_key))}  {s.llm_model if s.anthropic_api_key else 'rules only'}")
    if not s.secret_key or (s.data_dir / '.secret_key').exists():
        typer.echo("SECRET_KEY       using an auto-generated key from the data dir (set SECRET_KEY in production)")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Run the web app."""
    import uvicorn

    uvicorn.run("unredact.web.app:app", host=host, port=port, reload=reload)


# ───────────────────────── FOIA ─────────────────────────

agencies = typer.Typer(help="Agency directory.", no_args_is_help=True)
foia.add_typer(agencies, name="agencies")


@agencies.command("sync")
def agencies_sync():
    """Pull all federal agency components from the FOIA.gov API."""
    from unredact.foia.directory import sync_foia_gov

    n = sync_foia_gov(connect(), get_settings().foia_gov_api_key)
    typer.echo(f"synced {n} agency components")


@agencies.command("search")
def agencies_search(q: str):
    from unredact.foia.directory import search

    for a in search(connect(), q):
        typer.echo(f"{a['id']}\n    {a['title']} ({a['abbreviation'] or '-'}) — {a['agency'] or a['jurisdiction']}"
                   f"\n    email: {a['email'] or '-'}  portal: {a['submission_format'] or '-'}")


@agencies.command("add")
def agencies_add(
    id: str = typer.Option(..., help="Slug, e.g. oh-youngstown-police"),
    title: str = typer.Option(...),
    jurisdiction: str = typer.Option(..., help="Two-letter state code, or 'federal'"),
    email: Optional[str] = None,
    website: Optional[str] = None,
    parent: Optional[str] = typer.Option(None, help="Parent body, e.g. 'City of Youngstown'"),
):
    """Add a state/local agency (not in FOIA.gov)."""
    from unredact.foia.directory import upsert

    con = connect()
    upsert(con, id=id, title=title, agency=parent, email=email, website=website, jurisdiction=jurisdiction.upper()
           if jurisdiction != "federal" else "federal")
    con.commit()
    typer.echo(f"added {id}")


@foia.command("new")
def foia_new(
    agency: str = typer.Option(..., help="Agency id (see `foia agencies search`)"),
    subject: str = typer.Option(..., help="Short title"),
    description: Optional[str] = typer.Option(None, help="Records sought (or use --file)"),
    file: Optional[Path] = typer.Option(None, help="Read description from a file"),
    fee_category: Optional[str] = typer.Option(None, help="media|educational|scientific|commercial|other"),
    fee_limit: Optional[float] = typer.Option(25.0, help="Notify before fees exceed this ($)"),
    fee_waiver: bool = False,
    fee_waiver_reason: Optional[str] = None,
    expedited: bool = False,
    expedited_reason: Optional[str] = None,
):
    """Draft a request (prints the letter)."""
    from unredact.foia import directory, tracker
    from unredact.foia.compose import render_request

    con = connect()
    a = directory.get(con, agency)
    if not a:
        raise typer.BadParameter(f"unknown agency {agency}")
    desc = file.read_text() if file else description
    if not desc:
        raise typer.BadParameter("give --description or --file")
    req = tracker.create(con, agency_id=agency, subject=subject, description=desc, jurisdiction=a["jurisdiction"],
                         fee_category=fee_category, fee_limit=fee_limit, fee_waiver=fee_waiver,
                         fee_waiver_reason=fee_waiver_reason, expedited=expedited, expedited_reason=expedited_reason)
    typer.echo(render_request(a, req, get_settings().requester))
    typer.secho(f"\ndraft #{req['id']} ({req['token']}) — submit with: unredact foia submit {req['id']}", fg="cyan")


@foia.command("submit")
def foia_submit(
    req_id: int,
    channel: str = typer.Option("auto", help="auto|email|manual"),
    to: Optional[str] = typer.Option(None, help="Override destination email"),
    tracking: Optional[str] = typer.Option(None, help="Tracking number if filed manually"),
):
    from unredact.foia.submit import submit

    res = submit(connect(), req_id, channel, to=to, tracking_number=tracking)
    typer.echo(f"[{res.channel}] {res.detail}")


@foia.command("list")
def foia_list(status: Optional[str] = None, overdue: bool = False):
    from unredact.foia import tracker

    con = connect()
    rows = tracker.overdue(con) if overdue else tracker.list_requests(con, status)
    for r in rows:
        typer.echo(f"#{r['id']:<4} {r['token']}  {r['status']:<13} due {r['due_at'] or '-':<10}  "
                   f"{(r.get('agency_title') or '')[:40]:<40}  {r['subject'][:50]}")


@foia.command("show")
def foia_show(req_id: int):
    from unredact.foia import directory, tracker
    from unredact.foia.compose import render_request

    con = connect()
    r = tracker.get(con, req_id)
    if not r:
        raise typer.BadParameter("no such request")
    a = directory.get(con, r["agency_id"])
    typer.echo(r["body"] or render_request(a, r, get_settings().requester))
    typer.secho("\n— events —", fg="cyan")
    for e in tracker.events(con, req_id):
        typer.echo(f"{e['at']}  {e['kind']:<20} {e['detail'] or ''}" + (f"\n    📎 {e['attachment']}" if e["attachment"] else ""))


@foia.command("set")
def foia_set(req_id: int, status: str, note: Optional[str] = None, tracking: Optional[str] = None):
    """Manually set status (e.g. after a phone call)."""
    from unredact.foia import tracker

    con = connect()
    if tracking:
        con.execute("UPDATE foia_requests SET tracking_number=? WHERE id=?", (tracking, req_id))
    tracker.set_status(con, req_id, status, note)
    typer.echo("ok")


@foia.command("followup")
def foia_followup(req_id: int, send: bool = False, to: Optional[str] = None):
    """Draft (or --send) a status inquiry."""
    from unredact.foia import directory, tracker
    from unredact.foia.compose import render_followup
    from unredact.foia.submit import send_letter

    con = connect()
    r = tracker.get(con, req_id)
    a = directory.get(con, r["agency_id"])
    from unredact.users import requester_for

    body = render_followup(a, r, requester_for(con, r))
    typer.echo(body)
    if send:
        dest = to or a.get("email")
        if not dest:
            raise typer.BadParameter("no agency email; pass --to")
        send_letter(con, req_id, "followup", body, dest)
        typer.secho(f"sent to {dest}", fg="green")


@foia.command("appeal")
def foia_appeal(req_id: int, to: Optional[str] = typer.Option(None, help="Appeals office email"),
                grounds: Optional[Path] = typer.Option(None, help="File with specific grounds"), send: bool = False):
    """Draft (or --send) an administrative appeal. Appeals usually go to a different office — check the agency's rules."""
    from unredact.foia import directory, tracker
    from unredact.foia.compose import render_appeal
    from unredact.foia.submit import send_letter

    con = connect()
    r = tracker.get(con, req_id)
    a = directory.get(con, r["agency_id"])
    from unredact.users import requester_for

    body = render_appeal(a, r, requester_for(con, r), grounds.read_text() if grounds else "")
    typer.echo(body)
    if send:
        if not to:
            raise typer.BadParameter("--to (appeals office) is required to send")
        send_letter(con, req_id, "appeal", body, to)
        tracker.set_status(con, req_id, "appealed", f"appeal emailed to {to}")
        typer.secho(f"appeal sent to {to}", fg="green")


@foia.command("poll")
def foia_poll():
    """Check the IMAP inbox for agency replies."""
    from unredact.foia.inbox import poll

    for token, kind in poll(connect()):
        typer.echo(f"{token}: {kind}")


@foia.command("tick")
def foia_tick(send: bool = typer.Option(False, help="Actually email follow-ups"), every_days: int = 10):
    """Daily job: poll inbox, then follow up on overdue requests (at most once per --every-days)."""
    from unredact.foia import directory, tracker
    from unredact.foia.compose import render_followup
    from unredact.foia.inbox import poll
    from unredact.foia.submit import send_letter

    s = get_settings()
    con = connect()
    if s.imap_ready:
        for token, kind in poll(con, s):
            typer.echo(f"reply {token}: {kind}")
    now = dt.datetime.utcnow()
    for r in tracker.overdue(con):
        last = tracker.last_event_at(con, r["id"], "followup")
        if last and (now - last).days < every_days:
            continue
        if not send or not r["agency_email"]:
            typer.echo(f"overdue: #{r['id']} {r['token']} (due {r['due_at']}) {r['agency_title']}")
            continue
        a = directory.get(con, r["agency_id"])
        from unredact.users import requester_for

        send_letter(con, r["id"], "followup", render_followup(a, r, requester_for(con, r, s)), r["agency_email"], s)
        typer.echo(f"followed up #{r['id']} → {r['agency_email']}")


# ───────────────────────── Congress ─────────────────────────


@congress.command("sync")
def congress_sync():
    """Download current members + district offices (unitedstates/congress-legislators)."""
    from unredact.congress.directory import sync

    m, o = sync(connect())
    typer.echo(f"{m} members, {o} district offices")


@congress.command("lookup")
def congress_lookup(address: str):
    """Find your senators + representative for an address."""
    from unredact.congress.directory import members_for
    from unredact.congress.geo import geocode

    g = geocode(address)
    typer.echo(f"{g.matched_address}  →  {g.state}-{g.district:02d}")
    for m in members_for(connect(), g.state, g.district):
        typer.echo(f"  {m['bioguide']}  {'Sen.' if m['chamber'] == 'sen' else 'Rep.'} {m['name']} ({m['party']})"
                   f"  {m['contact_form'] or m['url'] or ''}")


@congress.command("send")
def congress_send(
    address: str = typer.Option(..., help="Full street address incl. ZIP"),
    prefix: str = typer.Option(..., help="Mr.|Mrs.|Ms.|Mx.|Dr."),
    first: str = typer.Option(...),
    last: str = typer.Option(...),
    email: str = typer.Option(...),
    subject: str = typer.Option(...),
    message: Path = typer.Option(..., help="File containing your message"),
    phone: str = "",
    topic: Optional[str] = typer.Option(None, help="Congress.gov policy area"),
    only: Optional[str] = typer.Option(None, help="Comma-separated bioguide ids to limit recipients"),
    mail: bool = typer.Option(False, help="Also mail printed letters via Lob (billed to your Lob account)"),
    dry_run: bool = False,
):
    """Send your message to your senators + representative."""
    from unredact.congress import send as sender
    from unredact.congress.compose import render_text
    from unredact.congress.directory import members_for
    from unredact.congress.geo import geocode
    from unredact.congress.models import Constituent

    con = connect()
    g = geocode(address)
    c = Constituent(prefix=prefix, first=first, last=last, email=email, phone=phone, street=g.street, city=g.city,
                    state=g.state, zip5=g.zip5, lat=g.lat, lon=g.lon)
    members = members_for(con, g.state, g.district)
    if only:
        keep = {x.strip() for x in only.split(",")}
        members = [m for m in members if m["bioguide"] in keep]
    if not members:
        raise typer.BadParameter("no members found — did you run `unredact congress sync`?")
    body = message.read_text()
    if dry_run:
        for m in members:
            typer.secho(f"── {m['name']} via {sender.choose_channel(m, get_settings())}", fg="cyan")
            typer.echo(render_text(m, c, subject, body))
        return
    batch = sender.new_batch()
    ids = sender.queue(con, batch=batch, kind="electronic", members=members, c=c, subject=subject, body=body, topic=topic)
    if mail:
        ids += sender.queue(con, batch=batch, kind="letter", members=members, c=c, subject=subject, body=body, topic=topic)
    for i in ids:
        res = sender.deliver(con, i)
        color = {"sent": "green", "manual": "yellow", "needs_human": "yellow"}.get(res.status, "red")
        typer.secho(f"msg {i}: {res.status} — {res.detail}", fg=color)


@congress.command("webforms-pull")
def congress_webforms_pull():
    """Clone community contact-form definitions (unitedstates/contact-congress) into the data dir."""
    s = get_settings()
    dest = s.data_dir / "contact-congress"
    if dest.exists():
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"], check=True)
    else:
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/unitedstates/contact-congress", str(dest)],
                       check=True)
    typer.echo(f"definitions in {dest / 'members'} — these are community-maintained and may be stale; test before use")


@congress.command("cwc-validate")
def congress_cwc_validate(bioguide: str, address: str):
    """Build a sample CWC payload for a House member and run it through /v2/validate."""
    from unredact.congress.deliver import cwc
    from unredact.congress.directory import get_member
    from unredact.congress.geo import geocode
    from unredact.congress.models import Constituent

    s = get_settings()
    m = get_member(connect(), bioguide)
    g = geocode(address)
    c = Constituent("Mx.", "Test", "User", s.cwc_contact_email or "test@example.com", g.street, g.city, g.state, g.zip5)
    xml = cwc.build_xml(member=m, c=c, subject="Validation test", body="Validation test.", topic=None, s=s)
    typer.echo(xml.decode())
    ok, text = cwc.validate(xml, s)
    typer.secho(("VALID" if ok else "INVALID") + f": {text}", fg="green" if ok else "red")


if __name__ == "__main__":
    app()
