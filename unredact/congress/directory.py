"""Members of Congress + district offices from the public-domain unitedstates/congress-legislators repo."""
from __future__ import annotations

import json
import math
import sqlite3

import httpx
import yaml

try:  # libyaml is ~10x faster on the 2 MB legislators file
    from yaml import CSafeLoader as Loader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as Loader

RAW = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/{}"


def _load(client: httpx.Client, name: str):
    r = client.get(RAW.format(name), follow_redirects=True)
    r.raise_for_status()
    return yaml.load(r.text, Loader=Loader)


def load_members(con: sqlite3.Connection, legislators: list[dict]) -> int:
    con.execute("DELETE FROM members")
    n = 0
    for m in legislators:
        t = m["terms"][-1]
        nm = m.get("name", {})
        name = nm.get("official_full") or f"{nm.get('first', '')} {nm.get('last', '')}".strip()
        con.execute(
            """INSERT INTO members (bioguide,name,first,last,chamber,state,district,party,url,contact_form,phone,dc_address,raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                m["id"]["bioguide"], name, nm.get("first"), nm.get("last"), t["type"], t["state"],
                t.get("district"), t.get("party"), t.get("url"), t.get("contact_form"), t.get("phone"),
                t.get("address"), json.dumps({"id": m["id"], "term": t}, default=str),
            ),
        )
        n += 1
    con.commit()
    return n


def load_offices(con: sqlite3.Connection, offices: list[dict]) -> int:
    con.execute("DELETE FROM district_offices")
    n = 0
    for entry in offices or []:
        bio = entry["id"]["bioguide"]
        for o in entry.get("offices", []) or []:
            con.execute(
                """INSERT OR REPLACE INTO district_offices (id,bioguide,address,suite,building,city,state,zip,phone,lat,lon)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    o.get("id") or f"{bio}-{n}", bio, o.get("address"), o.get("suite"), o.get("building"),
                    o.get("city"), o.get("state"), str(o.get("zip") or ""), o.get("phone"),
                    o.get("latitude"), o.get("longitude"),
                ),
            )
            n += 1
    con.commit()
    return n


def sync(con: sqlite3.Connection, client: httpx.Client | None = None) -> tuple[int, int]:
    c = client or httpx.Client(timeout=120)
    try:
        members = load_members(con, _load(c, "legislators-current.yaml"))
        offices = load_offices(con, _load(c, "legislators-district-offices.yaml"))
    finally:
        if client is None:
            c.close()
    return members, offices


def members_for(con: sqlite3.Connection, state: str, district: int | None) -> list[dict]:
    rows = con.execute(
        """SELECT * FROM members WHERE state = ? AND (chamber = 'sen' OR (chamber = 'rep' AND district = ?))
           ORDER BY chamber DESC, last""",
        (state.upper(), district),
    ).fetchall()
    return [dict(r) for r in rows]


def get_member(con: sqlite3.Connection, bioguide: str) -> dict | None:
    r = con.execute("SELECT * FROM members WHERE bioguide = ?", (bioguide,)).fetchone()
    return dict(r) if r else None


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def pick_office(con: sqlite3.Connection, bioguide: str, near: tuple[float, float] | None = None) -> dict | None:
    """District/state office to mail paper letters to (DC mail is irradiated and delayed for weeks)."""
    offices = [dict(r) for r in con.execute(
        "SELECT * FROM district_offices WHERE bioguide = ? AND address IS NOT NULL AND zip != ''", (bioguide,))]
    if not offices:
        return None
    if near and all(o["lat"] is not None for o in offices):
        offices.sort(key=lambda o: _haversine(near, (o["lat"], o["lon"])))
    return offices[0]


def title(member: dict) -> str:
    return "Senator" if member["chamber"] == "sen" else "Representative"
