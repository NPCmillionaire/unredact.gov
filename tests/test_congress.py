import xml.etree.ElementTree as ET

import pytest

from unredact.config import get_settings
from unredact.congress import send as sender
from unredact.congress.compose import render_letter_html, render_text
from unredact.congress.deliver import cwc
from unredact.congress.deliver.lob import office_address
from unredact.congress.deliver.webform import build_vars, pick_option, resolve
from unredact.congress.directory import members_for, pick_office
from unredact.congress.geo import parse_census
from unredact.congress.models import Constituent

C = Constituent(prefix="Mr.", first="Cam", last="Preston", email="Cam@Example.com", street="100 W Federal St",
                city="Youngstown", state="OH", zip5="44503", phone="330 555 0100", lat=41.1, lon=-80.65)

CENSUS_FIXTURE = {"result": {"addressMatches": [{
    "matchedAddress": "100 W FEDERAL ST, YOUNGSTOWN, OH, 44503",
    "coordinates": {"x": -80.65, "y": 41.10},
    "addressComponents": {"fromAddress": "100", "preDirection": "W", "streetName": "FEDERAL", "suffixType": "ST",
                          "suffixDirection": "", "city": "YOUNGSTOWN", "state": "OH", "zip": "44503"},
    "geographies": {"States": [{"STUSAB": "OH"}],
                    "119th Congressional Districts": [{"CD119": "06", "BASENAME": "6"}]},
}]}}


def test_parse_census():
    g = parse_census(CENSUS_FIXTURE)
    assert (g.state, g.district, g.street, g.zip5) == ("OH", 6, "100 W FEDERAL ST", "44503")


def test_parse_census_delegate_district():
    fx = {"result": {"addressMatches": [{**CENSUS_FIXTURE["result"]["addressMatches"][0],
                                         "geographies": {"119th Congressional Districts": [{"CD119": "98"}]}}]}}
    assert parse_census(fx).district == 0


def test_members_for(seeded):
    names = [m["name"] for m in members_for(seeded, "OH", 6)]
    assert names == ["Ann Senator", "Bob Rep"]


def test_pick_nearest_office(seeded):
    assert pick_office(seeded, "R000006", near=(41.1, -80.65))["id"] == "R000006-near"
    o = pick_office(seeded, "R000006", near=(41.1, -80.65))
    m = {"chamber": "rep", "name": "Bob Rep"}
    assert office_address(m, o)["address_line2"] == "Suite 5"


def test_cwc_xml():
    assert cwc.office_code("oh", 6) == "HOH06"
    assert cwc.office_code("AK", 0) == "HAK00"
    s = get_settings()
    xml = cwc.build_xml(member={"state": "OH", "district": 6}, c=C, subject="Hi", body="Body & <stuff>",
                        topic="Health", s=s, delivery_id="abc")
    root = ET.fromstring(xml)
    assert root.findtext("CWCVersion") == "2.0"
    assert root.findtext("Recipient/MemberOffice") == "HOH06"
    assert root.findtext("Constituent/StateAbbreviation") == "OH"
    assert root.findtext("Message/ConstituentMessage") == "Body & <stuff>"
    assert root.findtext("Message/LibraryOfCongressTopics/LibraryOfCongressTopic") == "Health"


def test_compose(seeded):
    m = members_for(seeded, "OH", 6)[1]
    t = render_text(m, C, "Subj", "Para one.\n\nPara two.")
    assert t.startswith("Dear Representative Rep,") and "Para two." in t and "Youngstown, OH 44503" in t
    html = render_letter_html(m, None, C, "Subj <b>", "x < y")
    assert "Subj &lt;b&gt;" in html and "x &lt; y" in html


def test_webform_vars():
    v = build_vars(C, "S", "M", "Health")
    assert v["$PHONE"] == "330-555-0100" and v["$ADDRESS_STATE_FULL"] == "Ohio"
    assert resolve("$NAME_FIRST", v) == "Cam" and resolve("literal", v) == "literal"
    field = {"value": "$TOPIC", "options": {"Choose": "", "Health Care": "HC", "Taxes": "TX"}}
    assert pick_option(field, v) == ("value", "HC")


def test_queue_dedupe_and_manual_fallback(seeded):
    members = members_for(seeded, "OH", 6)
    batch = sender.new_batch()
    ids = sender.queue(seeded, batch=batch, kind="electronic", members=members, c=C, subject="S", body="B")
    with pytest.raises(sender.DuplicateMessage):
        sender.queue(seeded, batch="b2", kind="electronic", members=members[:1], c=C, subject="S", body="B")
    # No CWC creds, no web-form defs → honest manual fallback with the office's contact URL
    res = sender.deliver(seeded, ids[0])
    assert res.status == "manual" and "senator.example/contact" in res.detail
    rows = sender.batch_status(seeded, batch)
    assert {r["status"] for r in rows} >= {"manual"}


def test_letter_without_lob_key_fails_cleanly(seeded):
    members = members_for(seeded, "OH", 6)[1:]
    ids = sender.queue(seeded, batch="b3", kind="letter", members=members, c=C, subject="L", body="B")
    res = sender.deliver(seeded, ids[0])
    assert res.status == "failed" and "LOB_API_KEY" in res.detail
