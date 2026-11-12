"""unredact.me web app.

Public:   /             landing + recent public requests
          /r/{slug}     public request pages (opt-in per request)
          /congress     constituents write their members; optional paid printed letters
Accounts: /signup /login /account
          /foia         each user's FOIA dashboard: draft, submit, follow up, appeal, upload, x-ray, packet
Hooks:    /inbound/email?key=…   Postmark inbound webhook (agency replies)
          /stripe/webhook
"""
from __future__ import annotations

import secrets
from pathlib import Path

from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from unredact import users
from unredact.config import get_settings
from unredact.db import connect

app = FastAPI(title="unredact.me")
app.add_middleware(SessionMiddleware, secret_key=get_settings().secret_key, session_cookie="unredact",
                   same_site="lax", https_only=get_settings().base_url.startswith("https"), max_age=60 * 60 * 24 * 30)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def db():
    con = connect()
    try:
        yield con
    finally:
        con.close()


def current_user(request: Request, con=Depends(db)) -> dict | None:
    return users.get(con, request.session.get("uid"))


def require_user(request: Request, user=Depends(current_user)) -> dict:
    if not user:
        raise HTTPException(303, headers={"Location": f"/login?next={quote(str(request.url.path))}"})
    return user


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    con = connect()
    try:
        user = users.get(con, request.session.get("uid"))
    finally:
        con.close()
    return templates.TemplateResponse(request, name, {"s": get_settings(), "user": user, **ctx})


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
def index(request: Request, con=Depends(db)):
    from unredact.foia import tracker

    return page(request, "index.html", recent=tracker.list_requests(con, public=True, limit=8))


@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


# ───────────────────────── Accounts ─────────────────────────


def _safe_next(n: str | None) -> str:
    return n if n and n.startswith("/") and not n.startswith("//") else "/foia"


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, next: str = ""):
    return page(request, "auth.html", mode="signup", error=None, next=next, email="")


@app.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form(""),
           next: str = Form(""), con=Depends(db)):
    if not get_settings().allow_signup:
        raise HTTPException(403, "Sign-ups are closed")
    try:
        u = users.create(con, email, password, name)
    except users.SignupError as exc:
        return page(request, "auth.html", mode="signup", error=str(exc), next=next, email=email)
    request.session.clear()
    request.session["uid"] = u["id"]
    return RedirectResponse("/account?welcome=1" if not next else _safe_next(next), status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = ""):
    return page(request, "auth.html", mode="login", error=None, next=next, email="")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form(""), con=Depends(db)):
    u = users.authenticate(con, email, password)
    if not u:
        return page(request, "auth.html", mode="login", error="Wrong email or password.", next=next, email=email)
    request.session.clear()
    request.session["uid"] = u["id"]
    return RedirectResponse(_safe_next(next), status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, welcome: str = "", user=Depends(require_user)):
    return page(request, "account.html", u=user, welcome=bool(welcome), saved=request.query_params.get("saved"))


@app.post("/account")
def account_save(name: str = Form(""), phone: str = Form(""), address: str = Form(""), organization: str = Form(""),
                 fee_category: str = Form(""), user=Depends(require_user), con=Depends(db)):
    users.update_profile(con, user["id"], name=name, phone=phone, address=address, organization=organization,
                         fee_category=fee_category)
    return RedirectResponse("/account?saved=1", status_code=303)


# ───────────────────────── Congress ─────────────────────────


@app.get("/congress", response_class=HTMLResponse)
def congress_form(request: Request, subject: str = "", body: str = "", user=Depends(current_user)):
    from unredact.congress.models import PREFIXES, TOPICS

    f = {"subject": subject, "body": body}
    if user:
        first, _, last = (user["name"] or "").partition(" ")
        f.update(first=first, last=last, email=user["email"], phone=user["phone"], address=user["address"].replace("\n", ", "))
    return page(request, "congress_form.html", prefixes=PREFIXES, topics=TOPICS, error=None, f=f)


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


# ───────────────────────── FOIA ─────────────────────────


def owned(con, req_id: int, user: dict) -> dict:
    """Load a request the user may act on (their own; admins also get operator/CLI requests)."""
    from unredact.foia import tracker

    r = tracker.get(con, req_id)
    if not r:
        raise HTTPException(404)
    if r["user_id"] != user["id"] and not (r["user_id"] is None and users.is_admin(user)):
        raise HTTPException(404)
    return r


def back(req_id: int, msg: str = "") -> RedirectResponse:
    return RedirectResponse(f"/foia/{req_id}" + (f"?msg={quote(msg)}" if msg else ""), status_code=303)


@app.get("/foia", response_class=HTMLResponse)
def foia_list(request: Request, user=Depends(require_user), con=Depends(db)):
    from unredact.foia import tracker

    rows = tracker.list_requests(con, user_id=user["id"])
    if users.is_admin(user):
        rows += tracker.list_requests(con, user_id=None)
    return page(request, "foia_list.html", rows=rows, overdue={r["id"] for r in tracker.overdue(con)})


@app.get("/foia/new", response_class=HTMLResponse)
def foia_new(request: Request, q: str = "", agency: str = "", user=Depends(require_user), con=Depends(db)):
    from unredact.foia import directory

    return page(request, "foia_new.html", q=q, results=directory.search(con, q) if q else [],
                agency=directory.get(con, agency) if agency else None, u=user)


@app.post("/foia/new")
def foia_create(
    agency_id: str = Form(...), subject: str = Form(...), description: str = Form(...),
    fee_category: str = Form(""), fee_limit: str = Form("25"), fee_waiver: str = Form(""),
    fee_waiver_reason: str = Form(""), expedited: str = Form(""), expedited_reason: str = Form(""),
    public: str = Form(""), user=Depends(require_user), con=Depends(db),
):
    from unredact.foia import directory, tracker

    a = directory.get(con, agency_id)
    if not a:
        raise HTTPException(400, "unknown agency")
    r = tracker.create(con, agency_id=agency_id, subject=subject, description=description,
                       jurisdiction=a["jurisdiction"], fee_category=fee_category or user.get("fee_category") or None,
                       fee_limit=float(fee_limit) if fee_limit else None, fee_waiver=bool(fee_waiver),
                       fee_waiver_reason=fee_waiver_reason or None, expedited=bool(expedited),
                       expedited_reason=expedited_reason or None, user_id=user["id"], public=bool(public))
    tracker.log(con, r["id"], "drafted")
    return RedirectResponse(f"/foia/{r['id']}", status_code=303)


def _files(events: list[dict]) -> list[dict]:
    """Downloadable files referenced by events (received records, uploads, saved letters)."""
    out = []
    for e in events:
        for i, path in enumerate((e.get("attachment") or "").split(";")):
            if path and not path.endswith(".xray.json"):
                out.append({"event": e["id"], "i": i, "name": Path(path).name, "kind": e["kind"]})
    return out


@app.get("/foia/{req_id}", response_class=HTMLResponse)
def foia_detail(request: Request, req_id: int, user=Depends(require_user), con=Depends(db)):
    from unredact.foia import directory, litigation, tracker
    from unredact.foia.compose import render_request

    r = owned(con, req_id, user)
    a = directory.get(con, r["agency_id"])
    ev = tracker.events(con, req_id)
    return page(request, "foia_detail.html", r=r, a=a, events=ev, files=_files(ev), statuses=tracker.STATUSES,
                letter=r["body"] or render_request(a, r, users.requester_for(con, r)),
                ex=litigation.exhaustion(con, r), leaks=[e for e in ev if e["kind"] == "xray:leak"],
                msg=request.query_params.get("msg"))


@app.post("/foia/{req_id}/submit")
def foia_submit(req_id: int, channel: str = Form("auto"), to: str = Form(""), tracking: str = Form(""),
                user=Depends(require_user), con=Depends(db)):
    from unredact.foia.submit import submit

    owned(con, req_id, user)
    try:
        msg = submit(con, req_id, channel, to=to or None, tracking_number=tracking or None).detail
    except Exception as exc:  # noqa: BLE001
        msg = f"Error: {exc}"
    return back(req_id, msg)


@app.post("/foia/{req_id}/status")
def foia_status(req_id: int, status: str = Form(...), note: str = Form(""), tracking: str = Form(""),
                user=Depends(require_user), con=Depends(db)):
    from unredact.foia import tracker

    owned(con, req_id, user)
    if tracking:
        con.execute("UPDATE foia_requests SET tracking_number=? WHERE id=?", (tracking.strip(), req_id))
    tracker.set_status(con, req_id, status, note or None)
    return back(req_id)


@app.post("/foia/{req_id}/letter")
def foia_letter(req_id: int, kind: str = Form(...), to: str = Form(""), grounds: str = Form(""),
                send: str = Form(""), user=Depends(require_user), con=Depends(db)):
    """Generate a follow-up or appeal. Saved as a draft unless send=1 (then emailed to the agency / appeals office)."""
    from unredact.foia import directory, tracker
    from unredact.foia.compose import render_appeal, render_followup
    from unredact.foia.submit import save_letter, send_letter

    r = owned(con, req_id, user)
    if kind not in ("followup", "appeal"):
        raise HTTPException(400)
    a = directory.get(con, r["agency_id"])
    who = users.requester_for(con, r)
    body = render_followup(a, r, who) if kind == "followup" else render_appeal(a, r, who, grounds)
    if not send:
        tracker.log(con, req_id, f"{kind}:draft", "draft saved", attachment=save_letter(r, kind, body))
        return back(req_id, f"{kind.title()} drafted. It's in Files below.")
    dest = to.strip() or (a.get("email") if kind == "followup" else "")
    if not dest:
        return back(req_id, "Error: give an address to send to (appeals usually go to a separate appeals office).")
    try:
        send_letter(con, req_id, kind, body, dest)
    except Exception as exc:  # noqa: BLE001
        return back(req_id, f"Error: {exc}")
    if kind == "appeal":
        tracker.set_status(con, req_id, "appealed", f"appeal emailed to {dest}")
    return back(req_id, f"{kind.title()} sent to {dest}.")


@app.post("/foia/{req_id}/upload")
async def foia_upload(req_id: int, file: UploadFile = File(...), user=Depends(require_user), con=Depends(db)):
    """Attach records you received some other way (mail, portal download). PDFs are x-rayed."""
    import re

    from unredact.foia import tracker
    from unredact.foia.inbox import xray_files

    r = owned(con, req_id, user)
    name = re.sub(r"[^\w.\-]", "_", file.filename or "upload.bin")[:120]
    d = get_settings().attachments_dir / r["token"]
    d.mkdir(parents=True, exist_ok=True)
    dest = d / name
    i = 1
    while dest.exists():
        dest = d / f"{Path(name).stem}-{i}{Path(name).suffix}"
        i += 1
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        return back(req_id, "Error: file is over 50 MB.")
    dest.write_bytes(data)
    tracker.log(con, req_id, "upload", f"uploaded {dest.name} ({len(data):,} bytes)", attachment=str(dest))
    leaks = xray_files(con, req_id, [str(dest)])
    return back(req_id, f"Uploaded {dest.name}." + (f" X-ray: {leaks} leaky redaction(s) found!" if leaks else ""))


@app.post("/foia/{req_id}/visibility")
def foia_visibility(req_id: int, public: str = Form(""), user=Depends(require_user), con=Depends(db)):
    owned(con, req_id, user)
    con.execute("UPDATE foia_requests SET public=? WHERE id=?", (int(bool(public)), req_id))
    con.commit()
    return back(req_id, "Now public." if public else "Now private.")


@app.get("/foia/{req_id}/packet.zip")
def foia_packet(req_id: int, user=Depends(require_user), con=Depends(db)):
    from unredact.foia import litigation, tracker

    r = owned(con, req_id, user)
    data = litigation.build_packet(con, req_id, get_settings().attachments_dir)
    tracker.log(con, req_id, "packet", "lawsuit packet downloaded")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{r["token"]}-packet.zip"'})


@app.get("/foia/{req_id}/escalate")
def foia_escalate(req_id: int, user=Depends(require_user), con=Depends(db)):
    """Hand a stalled request to the Congress flow, pre-filled as a casework request."""
    from unredact.foia import directory, tracker

    r = owned(con, req_id, user)
    a = directory.get(con, r["agency_id"])
    track = f" (agency tracking no. {r['tracking_number']})" if r["tracking_number"] else ""
    late = f" The statutory deadline was {r['due_at']} and has passed." if r["due_at"] and r["id"] in {
        x["id"] for x in tracker.overdue(con)} else ""
    body = (f"I am a constituent asking for casework help with a public-records request I submitted to "
            f"{a['title']} on {r['submitted_at'] or '[date]'}{track} about: {r['subject']}.{late} Its current status "
            f"is '{r['status']}'.\n\nPlease make a congressional inquiry with the agency's FOIA office or FOIA Public "
            f"Liaison about the status of this request and an estimated completion date. I'm happy to sign your "
            f"office's privacy release form.\n\nReference: {r['token']}")
    tracker.log(con, req_id, "escalate", "opened congressional casework message")
    return RedirectResponse(f"/congress?subject={quote('FOIA casework request: ' + r['subject'][:150])}"
                            f"&body={quote(body)}", status_code=303)


def _event_file(con, req: dict, event_id: int, i: int) -> FileResponse:
    from unredact.foia import tracker

    e = next((e for e in tracker.events(con, req["id"]) if e["id"] == event_id), None)
    paths = (e.get("attachment") or "").split(";") if e else []
    if i < 0 or i >= len(paths) or not paths[i]:
        raise HTTPException(404)
    p = Path(paths[i]).resolve()
    root = (get_settings().attachments_dir / req["token"]).resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.get("/foia/{req_id}/file/{event_id}/{i}")
def foia_file(req_id: int, event_id: int, i: int, user=Depends(require_user), con=Depends(db)):
    return _event_file(con, owned(con, req_id, user), event_id, i)


# ───────────────────────── Public request pages ─────────────────────────

PUBLIC_FILE_KINDS = ("reply:", "upload")


@app.get("/r/{slug}", response_class=HTMLResponse)
def public_request(request: Request, slug: str, con=Depends(db)):
    from unredact.foia import directory, tracker

    r = tracker.get_by_slug(con, slug)
    if not r or not r["public"]:
        raise HTTPException(404)
    ev = tracker.events(con, r["id"])
    files = [f for f in _files(ev) if f["kind"].startswith(PUBLIC_FILE_KINDS)]
    shown = [e for e in ev if e["kind"].startswith(("drafted", "submitted", "status:", "reply:", "followup",
                                                    "appeal", "upload", "xray:leak", "escalate"))
             and not e["kind"].endswith(":draft")]
    return page(request, "public_request.html", r=r, a=directory.get(con, r["agency_id"]), events=shown, files=files,
                leaks=sum(1 for e in ev if e["kind"] == "xray:leak"))


@app.get("/r/{slug}/file/{event_id}/{i}")
def public_file(slug: str, event_id: int, i: int, con=Depends(db)):
    from unredact.foia import tracker

    r = tracker.get_by_slug(con, slug)
    if not r or not r["public"]:
        raise HTTPException(404)
    e = next((e for e in tracker.events(con, r["id"]) if e["id"] == event_id), None)
    if not e or not e["kind"].startswith(PUBLIC_FILE_KINDS):
        raise HTTPException(404)
    return _event_file(con, r, event_id, i)


# ───────────────────────── Inbound mail webhook ─────────────────────────


@app.post("/inbound/email")
async def inbound_email(request: Request, key: str = ""):
    """Postmark inbound webhook. Point your INBOUND_ADDRESS domain's MX at Postmark and set the webhook URL to
    {BASE_URL}/inbound/email?key={INBOUND_KEY}. Replies are matched to requests by token / tracking number."""
    from unredact.foia.inbox import message_from_postmark, process_message

    s = get_settings()
    if not s.inbound_key or not secrets.compare_digest(key, s.inbound_key):
        raise HTTPException(403)
    payload = await request.json()
    con = connect()
    try:
        res = process_message(con, message_from_postmark(payload), s.attachments_dir)
    finally:
        con.close()
    # Always 200 so the provider doesn't retry unmatched mail forever.
    return {"matched": bool(res), "token": res[0]["token"] if res else None, "kind": res[1] if res else None}
