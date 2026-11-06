import pytest

from redtape.config import get_settings


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REDTAPE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQUESTER_NAME", "Test Requester")
    monkeypatch.setenv("REQUESTER_EMAIL", "req@example.com")
    for k in ("SMTP_HOST", "SMTP_FROM", "CWC_API_KEY", "LOB_API_KEY", "STRIPE_SECRET_KEY", "GEOCODIO_API_KEY"):
        monkeypatch.setenv(k, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def con():
    from redtape.db import connect

    c = connect()
    yield c
    c.close()


@pytest.fixture
def seeded(con):
    from redtape.congress.directory import load_members, load_offices
    from redtape.foia.directory import upsert

    upsert(con, id="fbi", title="Federal Bureau of Investigation", abbreviation="FBI",
           agency="Department of Justice", email="foia@fbi.example", jurisdiction="federal")
    upsert(con, id="oh-ytown-pd", title="Youngstown Police Department", jurisdiction="OH", email="records@city.example")
    con.commit()
    load_members(con, [
        {"id": {"bioguide": "S000001"}, "name": {"first": "Ann", "last": "Senator", "official_full": "Ann Senator"},
         "terms": [{"type": "sen", "state": "OH", "party": "Independent", "contact_form": "https://senator.example/contact"}]},
        {"id": {"bioguide": "R000006"}, "name": {"first": "Bob", "last": "Rep", "official_full": "Bob Rep"},
         "terms": [{"type": "rep", "state": "OH", "district": 6, "party": "Independent", "url": "https://rep.example"}]},
        {"id": {"bioguide": "R000013"}, "name": {"first": "Cy", "last": "Other"},
         "terms": [{"type": "rep", "state": "OH", "district": 13, "party": "Independent"}]},
    ])
    load_offices(con, [
        {"id": {"bioguide": "R000006"}, "offices": [
            {"id": "R000006-far", "address": "1 Far Rd", "city": "Marietta", "state": "OH", "zip": "45750",
             "latitude": 39.41, "longitude": -81.45},
            {"id": "R000006-near", "address": "2 Near St", "suite": "Suite 5", "city": "Youngstown", "state": "OH",
             "zip": "44503", "latitude": 41.10, "longitude": -80.65},
        ]},
    ])
    return con
