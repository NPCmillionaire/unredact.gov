from unittest.mock import patch

from fastapi.testclient import TestClient

from unredact.config import get_settings
from unredact.foia import presets as P
from unredact.foia import tracker
from unredact.web.ratelimit import RateLimiter


def test_presets_load_and_fill():
    ps = P.presets()
    assert {"police-use-of-force", "foia-log", "surveillance-tech", "official-emails"} <= set(ps)
    for p in ps.values():  # every preset renders with no values without crashing
        subj, desc = P.fill(p, {})
        assert subj and desc
    p = P.get("police-use-of-force")
    assert P.user_fields(p) == ["start", "end"]
    subj, desc = P.fill(p, {"agency_name": "Youngstown PD", "start": "Jan 1, 2025", "end": "Oct 31, 2026"})
    assert "Youngstown PD" in desc and "Jan 1, 2025" in subj and not P.unfilled(subj + desc)
    subj, desc = P.fill(P.get("body-camera"), {"date": "Nov 1, 2026"})
    assert "[LOCATION]" in subj and P.unfilled(desc)


def test_rate_limiter_window():
    rl = RateLimiter(window=60)
    assert all(rl.allow("ip", 3, now=t) for t in (0, 1, 2))
    assert not rl.allow("ip", 3, now=3)
    assert rl.allow("other", 3, now=3)
    assert rl.allow("ip", 3, now=70)


def test_export_csv(seeded):
    tracker.create(seeded, agency_id="fbi", subject="Contracts, 2025", description="d")
    csv = tracker.export_csv(seeded)
    assert csv.splitlines()[0].startswith("id,token,status")
    assert '"Contracts, 2025"' in csv


def test_web_preset_prefill_and_agency_add(seeded):
    from unredact.web.app import app

    c = TestClient(app)
    r = c.get("/foia/new?agency=fbi&preset=foia-log")
    assert r.status_code == 200 and "Federal Bureau of Investigation between [START]" in r.text
    r = c.post("/foia/agencies/new", data={"id": "OH Mahoning Sheriff", "title": "Mahoning County Sheriff",
                                           "jurisdiction": "oh"}, follow_redirects=False)
    assert r.headers["location"] == "/foia/new?agency=oh-mahoning-sheriff"
    assert c.get("/foia/export.csv").headers["content-type"].startswith("text/csv")


def test_web_followup_and_appeal(seeded):
    from unredact.foia.submit import submit
    from unredact.web.app import app

    c = TestClient(app)
    r = tracker.create(seeded, agency_id="fbi", subject="S", description="D")
    submit(seeded, r["id"], "manual")
    with patch("unredact.foia.submit.send_email") as se:
        c.post(f"/foia/{r['id']}/followup", data={})
        assert se.call_args.args[0] == "foia@fbi.example"
        assert "Status inquiry" in se.call_args.args[1]
        assert "appeal" in c.get(f"/foia/{r['id']}/appeal").text.lower()
        c.post(f"/foia/{r['id']}/appeal", data={"to": "appeals@x.example", "body": "I appeal."})
        assert se.call_args.args[0] == "appeals@x.example"
    assert tracker.get(seeded, r["id"])["status"] == "appealed"


def test_email_verification_gates_delivery(seeded, monkeypatch):
    from unredact.congress.geo import GeoResult  # noqa: F401
    from unredact.web.app import app

    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SMTP_FROM", "x@example.com")
    get_settings.cache_clear()
    c = TestClient(app)
    data = dict(prefix="Ms.", first="Ada", last="L", email="ada@example.com", phone="", subject="Verify me",
                body="Hello.", topic="", street="1 Main St", city="Youngstown", state="OH", zip5="44503",
                lat="", lon="", bioguide=["R000006"])
    with patch("unredact.congress.verify.send_email") as se:
        r = c.post("/congress/send", data=data, follow_redirects=False)
    status_url = r.headers["location"]
    link = se.call_args.args[2].split("Confirm and send it:\n")[1].split("\n")[0]
    token = link.rsplit("/", 1)[1]
    page = c.get(status_url).text
    assert "Check your email" in page
    assert seeded.execute("SELECT status FROM messages WHERE subject='Verify me'").fetchone()[0] == "unverified"

    r = c.get(f"/congress/verify/{token}", follow_redirects=False)
    assert r.headers["location"] == status_url
    # background delivery ran → no automated channel for this office → manual hand-off
    assert seeded.execute("SELECT status FROM messages WHERE subject='Verify me'").fetchone()[0] == "manual"
    assert "expired" in c.get("/congress/verify/bogus").text.lower()
