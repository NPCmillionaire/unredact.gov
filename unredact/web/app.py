"""FastAPI web front end.

Public:  /congress  — constituents look up their members, write, optionally pay for printed letters.
Private: /foia      — your FOIA dashboard (HTTP Basic with UNREDACT_ADMIN_PASSWORD; localhost-only if unset).
"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from unredact.config import get_settings
from unredact.db import connect

app = FastAPI(title="unredact")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
basic = HTTPBasic(auto_error=False)


def db():
    con = connect()
    try:
        yield con
    finally:
        con.close()


def admin(request: Request, creds: HTTPBasicCredentials | None = Depends(basic)):
    s = get_settings()
    if not s.admin_password:
        if request.client and request.client.host in ("127.0.0.1", "::1", "testclient"):
            return
        raise HTTPException(403, "Set UNREDACT_ADMIN_PASSWORD to use the FOIA dashboard remotely")
    if not creds or not secrets.compare_digest(creds.password.encode(), s.admin_password.encode()):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"s": get_settings(), **ctx})


def _deliver_all(ids: list[int]) -> None:
    from unredact.congress.send import deliver

    con = connect()
    try:
        for i in ids:
            try:
                deliver(con, i)
            except Exception as exc:  # noqa: BLE001
                con.execute("UPDATE messages SET status='failed', error=? WHERE id=?", (str(exc)[:500], i))
                con.commit()
    finally:
        con.close()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return page(request, "index.html")


# ───────────────────────── Congress ─────────────────────────


@app.get("/congress", response_class=HTMLResponse)
def congress_form(request: Request):
    from unredact.congress.models import PREFIXES, TOPICS

    return page(request, "congress_form.html", prefixes=PREFIXES, topics=TOPICS, error=None, f={})


@app.post("/congress/preview", response_class=HTMLResponse)
def congress_preview(
    request: Request,
    prefix: str = Form(...), first: str = Form(...), last: str = Form(...), email: str = Form(...),
    phone: str = Form(""), address: str = Form(...), subject: str = Form(...), body: str = Form(...),
    topic: str = Form(""), con=Depends(db),
):
    from unredact.congress.compose import render_text
    from unredact.congress.directory import members_for
    from unredact.congress.geo import GeocodeError, geocode
    from unredact.congress.models import PREFIXES, TOPICS, Constituent

    f = dict(prefix=prefix, first=first, last=last, email=email, phone=phone, address=address, subject=subject,
             body=body, topic=topic)
    try:
        g = geocode(address)
    except (GeocodeError, Exception) as exc:  # noqa: BLE001
        return page(request, "congress_form.html", prefixes=PREFIXES, topics=TOPICS, error=str(exc), f=f)
    members = members_for(con, g.state, g.district)
    if not members:
        return page(request, "congress_form.html", prefixes=PREFIXES, topics=TOPICS, f=f,
                    error="No members found. (Admin: run `unredact congress sync`.)")
    c = Constituent(prefix=prefix, first=first, last=last, email=email, phone=phone, street=g.street, city=g.city,
                    state=g.state, zip5=g.zip5, lat=g.lat, lon=g.lon)
    previews = [(m, render_text(m, c, subject, body)) for m in members]
    return page(request, "congress_preview.html", g=g, c=c, f=f, previews=previews)


@app.post("/congress/send")
def congress_send(
    request: Request, background: BackgroundTasks,
    prefix: str = Form(...), first: str = Form(...), last: str = Form(...), email: str = Form(...),
    phone: str = Form(""), street: str = Form(...), city: str = Form(...), state: str = Form(...),
    zip5: str = Form(...), lat: str = Form(""), lon: str = Form(""), subject: str = Form(...), body: str = Form(...),
    topic: str = Form(""), bioguide: list[str] = Form([]), mail: str = Form(""), con=Depends(db),
):
    from unredact.congress import billing
    from unredact.congress import send as sender
    from unredact.congress.directory import get_member
    from unredact.congress.models import Constituent

    s = get_settings()
    members = [m for m in (get_member(con, b) for b in bioguide) if m]
    if not members:
        raise HTTPException(400, "Pick at least one recipient")
    c = Constituent(prefix=prefix, first=first, last=last, email=email, phone=phone, street=street, city=city,
                    state=state, zip5=zip5, lat=float(lat) if lat else None, lon=float(lon) if lon else None)
    batch = sender.new_batch()
    try:
        ids = sender.queue(con, batch=batch, kind="electronic", members=members, c=c, subject=subject, body=body,
                           topic=topic or None)
        if mail and s.stripe_ready:
            order_id = billing.create_order(con, len(members), email, s)
            sender.queue(con, batch=batch, kind="letter", members=members, c=c, subject=subject, body=body,
                         topic=topic or None, order_id=order_id, status="awaiting_payment")
            background.add_task(_deliver_all, ids)
            url = billing.checkout_url(con, order_id, len(members), email, batch, s)
            return RedirectResponse(url, status_code=303)
    except sender.DuplicateMessage as exc:
        raise HTTPException(409, str(exc))
    background.add_task(_deliver_all, ids)
    return RedirectResponse(f"/congress/status/{batch}", status_code=303)


@app.get("/congress/status/{batch}", response_class=HTMLResponse)
def congress_status(request: Request, batch: str, con=Depends(db)):
    from unredact.congress.send import batch_status

    rows = batch_status(con, batch)
    if not rows:
        raise HTTPException(404)
    pending = any(r["status"] in ("queued",) for r in rows)
    return page(request, "congress_status.html", rows=rows, pending=pending,
                paid=request.query_params.get("paid"), canceled=request.query_params.get("canceled"))


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, background: BackgroundTasks):
    from unredact.congress.billing import handle_webhook

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    con = connect()
    try:
        order_id = handle_webhook(con, payload, sig)
        if order_id:
            ids = [r["id"] for r in con.execute("SELECT id FROM messages WHERE order_id=? AND status='queued'", (order_id,))]
            background.add_task(_deliver_all, ids)
    except Exception as exc:  # noqa: BLE001 — bad signature etc.
        return PlainTextResponse(f"webhook error: {exc}", status_code=400)
    finally:
        con.close()
    return PlainTextResponse("ok")


# ───────────────────────── FOIA (admin) ─────────────────────────


@app.get("/foia", response_class=HTMLResponse, dependencies=[Depends(admin)])
def foia_list(request: Request, con=Depends(db)):
    from unredact.foia import tracker

    return page(request, "foia_list.html", rows=tracker.list_requests(con), overdue={r["id"] for r in tracker.overdue(con)})


@app.get("/foia/new", response_class=HTMLResponse, dependencies=[Depends(admin)])
def foia_new(request: Request, q: str = "", agency: str = "", con=Depends(db)):
    from unredact.foia import directory

    return page(request, "foia_new.html", q=q, results=directory.search(con, q) if q else [],
                agency=directory.get(con, agency) if agency else None)


@app.post("/foia/new", dependencies=[Depends(admin)])
def foia_create(
    agency_id: str = Form(...), subject: str = Form(...), description: str = Form(...),
    fee_category: str = Form(""), fee_limit: str = Form("25"), fee_waiver: str = Form(""),
    fee_waiver_reason: str = Form(""), expedited: str = Form(""), expedited_reason: str = Form(""), con=Depends(db),
):
    from unredact.foia import directory, tracker

    a = directory.get(con, agency_id)
    if not a:
        raise HTTPException(400, "unknown agency")
    r = tracker.create(con, agency_id=agency_id, subject=subject, description=description,
                       jurisdiction=a["jurisdiction"], fee_category=fee_category or None,
                       fee_limit=float(fee_limit) if fee_limit else None, fee_waiver=bool(fee_waiver),
                       fee_waiver_reason=fee_waiver_reason or None, expedited=bool(expedited),
                       expedited_reason=expedited_reason or None)
    return RedirectResponse(f"/foia/{r['id']}", status_code=303)


@app.get("/foia/{req_id}", response_class=HTMLResponse, dependencies=[Depends(admin)])
def foia_detail(request: Request, req_id: int, con=Depends(db)):
    from unredact.foia import directory, tracker
    from unredact.foia.compose import render_request

    r = tracker.get(con, req_id)
    if not r:
        raise HTTPException(404)
    a = directory.get(con, r["agency_id"])
    return page(request, "foia_detail.html", r=r, a=a, letter=r["body"] or render_request(a, r, get_settings().requester),
                events=tracker.events(con, req_id), statuses=tracker.STATUSES, msg=request.query_params.get("msg"))


@app.post("/foia/{req_id}/submit", dependencies=[Depends(admin)])
def foia_submit(req_id: int, channel: str = Form("auto"), to: str = Form(""), tracking: str = Form(""), con=Depends(db)):
    from unredact.foia.submit import submit

    try:
        res = submit(con, req_id, channel, to=to or None, tracking_number=tracking or None)
        msg = res.detail
    except Exception as exc:  # noqa: BLE001
        msg = f"Error: {exc}"
    return RedirectResponse(f"/foia/{req_id}?msg={msg}", status_code=303)


@app.post("/foia/{req_id}/status", dependencies=[Depends(admin)])
def foia_status(req_id: int, status: str = Form(...), note: str = Form(""), con=Depends(db)):
    from unredact.foia import tracker

    tracker.set_status(con, req_id, status, note or None)
    return RedirectResponse(f"/foia/{req_id}", status_code=303)
