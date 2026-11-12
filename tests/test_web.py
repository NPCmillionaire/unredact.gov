from unittest.mock import patch

from fastapi.testclient import TestClient

from unredact.congress.geo import GeoResult
from unredact.web.app import app

client = TestClient(app)
GEO = GeoResult("100 W FEDERAL ST, YOUNGSTOWN, OH, 44503", "100 W FEDERAL ST", "YOUNGSTOWN", "OH", "44503", 6, 41.1, -80.65)
FORM = dict(prefix="Mr.", first="Cam", last="Preston", email="c@example.com", phone="", subject="Hello",
            body="Please support the bill.", topic="Health")


def login(c=None, email="web@example.com"):
    c = c or TestClient(app)
    r = c.post("/signup", data={"email": email, "password": "correct horse battery", "name": "Web User"},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def test_pages_render(seeded):
    assert client.get("/").status_code == 200
    assert "Write your members" in client.get("/congress").text
    assert client.get("/foia", follow_redirects=False).status_code == 303  # login required
    c = login()
    assert c.get("/foia").status_code == 200
    assert "Federal Bureau" in c.get("/foia/new?q=FBI").text


def test_congress_flow(seeded):
    with patch("unredact.congress.geo.geocode", return_value=GEO):
        r = client.post("/congress/preview", data={**FORM, "address": "100 W Federal St, Youngstown OH"})
    assert r.status_code == 200 and "Bob Rep" in r.text and "Ann Senator" in r.text
    data = {**FORM, "street": GEO.street, "city": GEO.city, "state": "OH", "zip5": "44503", "lat": "41.1",
            "lon": "-80.65", "bioguide": ["S000001", "R000006"]}
    r = client.post("/congress/send", data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/congress/status/")
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "Open contact form" in page.text


def test_foia_create_and_submit(seeded):
    client = login()
    r = client.post("/foia/new", data={"agency_id": "fbi", "subject": "Test", "description": "Records about X"},
                    follow_redirects=False)
    assert r.status_code == 303
    url = r.headers["location"]
    assert "Records about X" in client.get(url).text
    r = client.post(url + "/submit", data={"channel": "manual", "tracking": "T-12345"}, follow_redirects=True)
    assert "submitted" in r.text and "T-12345" in r.text
