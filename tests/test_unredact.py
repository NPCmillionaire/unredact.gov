"""unredact.me features: accounts, isolation, inbound webhook, x-ray, public pages, packet, escalation."""
import base64
import datetime as dt
import io
import zipfile
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from unredact.config import get_settings
from unredact.web.app import app

PW = "correct horse battery"


def signup(email, name="Test User"):
    c = TestClient(app)
    assert c.post("/signup", data={"email": email, "password": PW, "name": name}, follow_redirects=False).status_code == 303
    return c


def new_request(c, public="1"):
    r = c.post("/foia/new", data={"agency_id": "fbi", "subject": "Drone contracts", "description": "All drone contracts.",
                                  "public": public}, follow_redirects=False)
    return int(r.headers["location"].rsplit("/", 1)[1])


@pytest.fixture
def inbound(monkeypatch):
    monkeypatch.setenv("INBOUND_KEY", "k3y")
    monkeypatch.setenv("INBOUND_ADDRESS", "requests@in.unredact.me")
    get_settings.cache_clear()


def test_passwords_and_login(seeded):
    from unredact import users
    h = users.hash_password("hunter2hunter2")
    assert users.verify_password("hunter2hunter2", h) and not users.verify_password("nope", h)
    signup("a@example.com")
    c = TestClient(app)
    assert "Wrong email or password" in c.post("/login", data={"email": "a@example.com", "password": "bad"}).text
    r = c.post("/login", data={"email": "A@example.com", "password": PW, "next": "//evil.com"}, follow_redirects=False)
    assert r.headers["location"] == "/foia"  # open-redirect blocked
    assert "at least 10 characters" in signup_err("b@example.com", "short")


def signup_err(email, pw):
    return TestClient(app).post("/signup", data={"email": email, "password": pw}).text


def test_users_cannot_see_each_others_requests(seeded):
    alice, bob = signup("alice@example.com"), signup("bob@example.com")
    rid = new_request(alice, public="")
    assert alice.get(f"/foia/{rid}").status_code == 200
    assert bob.get(f"/foia/{rid}").status_code == 404
    assert bob.post(f"/foia/{rid}/status", data={"status": "withdrawn"}).status_code == 404
    assert "Drone contracts" not in bob.get("/foia").text


def test_letter_uses_inbox_not_personal_email(seeded, inbound):
    c = signup("private@example.com", name="Jane Q Public")
    rid = new_request(c)
    page = c.get(f"/foia/{rid}").text
    assert "Jane Q Public" in page and "requests@in.unredact.me" in page
    letter = page.split("<pre>")[1].split("</pre>")[0]
    assert "private@example.com" not in letter


def _pdf_with_fake_redaction() -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_text((72, 100), "Informant: John Q. Secret", fontsize=12)
    pg.draw_rect(pymupdf.Rect(130, 88, 260, 104), color=(0, 0, 0), fill=(0, 0, 0))
    return doc.tobytes()


def test_inbound_webhook_classifies_and_xrays(seeded, inbound):
    from unredact.db import connect
    from unredact.foia import tracker
    c = signup("owner@example.com")
    rid = new_request(c)
    c.post(f"/foia/{rid}/submit", data={"channel": "manual"})
    token = tracker.get(connect(), rid)["token"]

    assert TestClient(app).post("/inbound/email?key=wrong", json={}).status_code == 403
    payload = {"From": "foia@fbi.example", "To": "requests@in.unredact.me",
               "Subject": f"RE: Public records request [{token}]",
               "TextBody": "This is our final response. Responsive records are attached.",
               "Attachments": [{"Name": "release.pdf", "ContentType": "application/pdf",
                                "Content": base64.b64encode(_pdf_with_fake_redaction()).decode()}]}
    r = TestClient(app).post("/inbound/email?key=k3y", json=payload).json()
    assert r == {"matched": True, "token": token, "kind": "complete"}

    page = c.get(f"/foia/{rid}").text
    assert "leaky redactions found" in page and "release.pdf" in page
    assert tracker.get(connect(), rid)["closed_at"] == dt.date.today().isoformat()

    unmatched = TestClient(app).post("/inbound/email?key=k3y", json={"Subject": "spam", "TextBody": "hi"}).json()
    assert unmatched["matched"] is False


def test_public_page_and_files(seeded, inbound):
    from unredact.db import connect
    from unredact.foia import tracker
    c = signup("pub@example.com")
    rid = new_request(c, public="1")
    c.post(f"/foia/{rid}/upload", files={"file": ("records.txt", b"hello records", "text/plain")})
    c.post(f"/foia/{rid}/letter", data={"kind": "followup"})  # draft letter: owner-only
    slug = tracker.get(connect(), rid)["slug"]
    anon = TestClient(app)
    page = anon.get(f"/r/{slug}").text
    assert "Drone contracts" in page and "records.txt" in page and "followup" not in page
    assert "pub@example.com" not in page
    assert slug in anon.get("/").text  # listed on the homepage
    link = page.split(f'/r/{slug}/file/')[1].split('"')[0]
    assert anon.get(f"/r/{slug}/file/{link}").content == b"hello records"
    c.post(f"/foia/{rid}/visibility", data={"public": ""})
    assert anon.get(f"/r/{slug}").status_code == 404


def test_upload_xray_packet_and_escalate(seeded):
    c = signup("lit@example.com", name="Liti Gant")
    rid = new_request(c)
    c.post(f"/foia/{rid}/submit", data={"channel": "manual", "tracking": "FOIPA-99"})
    r = c.post(f"/foia/{rid}/upload", files={"file": ("bad.pdf", _pdf_with_fake_redaction(), "application/pdf")},
               follow_redirects=False)
    assert "leaky" in unquote(r.headers["location"])

    z = zipfile.ZipFile(io.BytesIO(c.get(f"/foia/{rid}/packet.zip").content))
    names = z.namelist()
    assert "README.md" in names and "records/bad.pdf" in names and "letters/00-request.txt" in names
    readme = z.read("README.md").decode()
    assert "FOIPA-99" in readme and "552(a)(4)(B)" in readme

    r = c.get(f"/foia/{rid}/escalate", follow_redirects=False)
    assert r.headers["location"].startswith("/congress?subject=")
    form = c.get(r.headers["location"]).text
    assert "casework" in form and "lit@example.com" in form and "Liti" in form


def test_exhaustion_rules(seeded):
    from unredact.foia import litigation
    from unredact.db import connect
    con = connect()
    base = {"id": 0, "jurisdiction": "federal", "closed_at": None}
    today = dt.date(2026, 6, 1)
    assert litigation.exhaustion(con, {**base, "status": "processing", "due_at": "2026-05-01"}, today)["ripe"] is True
    assert litigation.exhaustion(con, {**base, "status": "processing", "due_at": "2026-07-01"}, today)["ripe"] is False
    d = litigation.exhaustion(con, {**base, "status": "denied", "due_at": None, "closed_at": "2026-05-01"}, today)
    assert d["ripe"] is False and d["appeal_deadline"] == dt.date(2026, 7, 30)
    assert litigation.exhaustion(con, {**base, "jurisdiction": "OH", "status": "submitted"}, today)["ripe"] is None


def test_llm_classifier_falls_back(monkeypatch):
    from unredact.foia import inbox
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    get_settings.cache_clear()
    monkeypatch.setattr(inbox, "classify_llm", lambda text, s=None: None)  # e.g. network failure
    assert inbox.classify("We acknowledge receipt of your request") == "acknowledged"
    monkeypatch.setattr(inbox, "classify_llm", lambda text, s=None: "denied")
    assert inbox.classify("anything") == "denied"
