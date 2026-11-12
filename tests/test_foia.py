import datetime as dt
from email.message import EmailMessage

from unredact.foia import tracker
from unredact.foia.compose import render_appeal, render_followup, render_request
from unredact.foia.deadlines import add_business_days, due_date
from unredact.foia.inbox import classify, process_message
from unredact.foia.submit import submit


def test_federal_deadline_skips_weekends_and_holidays():
    # Fri Nov 6 2026 + 20 business days, skipping Veterans Day (11/11) and Thanksgiving (11/26)
    assert add_business_days(dt.date(2026, 11, 6), 20) == dt.date(2026, 12, 8)
    assert due_date("federal", dt.date(2026, 11, 6)) == dt.date(2026, 12, 8)


def test_state_rules():
    assert due_date("CA", dt.date(2026, 11, 6)) == dt.date(2026, 11, 16)  # calendar days
    assert due_date("OH", dt.date(2026, 11, 6)) is None  # "promptly", no fixed clock


def test_request_letter(seeded):
    r = tracker.create(seeded, agency_id="fbi", subject="Drone contracts", description="All contracts for X.",
                       fee_limit=50, fee_waiver=True, fee_category="media", expedited=True)
    a = {"title": "Federal Bureau of Investigation", "agency": "Department of Justice"}
    text = render_request(a, r, {"name": "Cam", "email": "c@x.com", "phone": "", "address": "Youngstown, OH"},
                          today=dt.date(2026, 11, 6))
    assert "5 U.S.C. § 552" in text
    assert r["token"] in text
    assert "$50.00" in text
    assert "representative of the news media" in text
    assert "true and correct" in text
    assert "20 business days" in text
    assert "November 6, 2026" in text


def test_ohio_letter_has_no_deadline_sentence(seeded):
    r = tracker.create(seeded, agency_id="oh-ytown-pd", subject="Use of force", description="Policies.", jurisdiction="OH")
    text = render_request({"title": "YPD", "agency": None}, r, {"name": "C", "email": "e", "phone": "", "address": ""})
    assert "R.C. 149.43" in text and "as the statute provides" not in text


def test_manual_submit_sets_due_and_followup(seeded):
    r = tracker.create(seeded, agency_id="fbi", subject="S", description="D")
    res = submit(seeded, r["id"], "manual", tracking_number="FOIPA-123")
    assert res.ok and res.channel == "manual"
    r2 = tracker.get(seeded, r["id"])
    assert r2["status"] == "submitted" and r2["due_at"] and r2["tracking_number"] == "FOIPA-123"
    assert tracker.overdue(seeded, today=dt.date.today() + dt.timedelta(days=60))[0]["id"] == r["id"]
    fu = render_followup({"title": "FBI"}, r2, {"name": "C", "email": "e"})
    assert "FOIPA-123" in fu and r["token"] in fu
    ap = render_appeal({"title": "FBI"}, r2, {"name": "C", "email": "e", "address": ""})
    assert "failure to respond" in ap


def test_classifier():
    assert classify("We have received your FOIA request and assigned tracking number 2026-001") == "acknowledged"
    assert classify("The estimated fees total $120. Advance payment is required.") == "fee_quote"
    assert classify("Responsive records are attached. This is our final response.") == "complete"
    assert classify("A search was conducted and no responsive records were located.") == "denied"
    assert classify("Please clarify the date range.") == "clarification"


def test_inbox_matches_token_and_saves_attachment(seeded, tmp_path):
    r = tracker.create(seeded, agency_id="fbi", subject="S", description="D")
    submit(seeded, r["id"], "manual")
    msg = EmailMessage()
    msg["From"] = "foia@fbi.example"
    msg["Subject"] = f"RE: Public records request [{r['token']}]"
    msg.set_content("We acknowledge receipt. Your request number is FOIPA-998877.")
    msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="ack letter.pdf")
    req, kind = process_message(seeded, msg, tmp_path / "att")
    assert kind == "acknowledged"
    r2 = tracker.get(seeded, r["id"])
    assert r2["status"] == "acknowledged" and r2["tracking_number"] == "FOIPA-998877"
    assert (tmp_path / "att" / r["token"] / "ack_letter.pdf").exists()
