"""Agency directory: FOIA.gov sync + manually added state/local agencies."""
from __future__ import annotations

import json
import re
import sqlite3

import httpx

API = "https://api.foia.gov/api"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _first_email(attrs: dict) -> str | None:
    for key in ("email", "submission_email", "submission_address"):
        v = attrs.get(key)
        if isinstance(v, list):
            v = " ".join(str(x) for x in v)
        if isinstance(v, dict):
            v = json.dumps(v)
        if v:
            m = EMAIL_RE.search(str(v))
            if m:
                return m.group(0)
    return None


def _url(v) -> str | None:
    if isinstance(v, dict):
        return v.get("uri") or v.get("url")
    return v or None


def upsert(con: sqlite3.Connection, **a) -> None:
    a.setdefault("jurisdiction", "federal")
    for k in ("abbreviation", "agency", "agency_abbr", "email", "submission_format", "website", "raw"):
        a.setdefault(k, None)
    con.execute(
        """INSERT INTO foia_agencies (id,title,abbreviation,agency,agency_abbr,email,submission_format,website,jurisdiction,raw)
           VALUES (:id,:title,:abbreviation,:agency,:agency_abbr,:email,:submission_format,:website,:jurisdiction,:raw)
           ON CONFLICT(id) DO UPDATE SET title=excluded.title, abbreviation=excluded.abbreviation,
             agency=excluded.agency, agency_abbr=excluded.agency_abbr,
             email=COALESCE(excluded.email, foia_agencies.email),
             submission_format=excluded.submission_format, website=excluded.website,
             jurisdiction=excluded.jurisdiction, raw=excluded.raw""",
        a,
    )


def sync_foia_gov(con: sqlite3.Connection, api_key: str, client: httpx.Client | None = None) -> int:
    """Pull every federal agency component from the FOIA.gov Agency Component API (JSON:API)."""
    if not api_key:
        raise RuntimeError("FOIA_GOV_API_KEY not set — sign up at https://www.foia.gov/developer/")
    url: str | None = f"{API}/agency_components?include=agency&page[limit]=50"
    c = client or httpx.Client(timeout=60)
    n = 0
    try:
        while url:
            r = c.get(url, headers={"X-API-Key": api_key})
            r.raise_for_status()
            js = r.json()
            parents = {
                i["id"]: i.get("attributes", {})
                for i in js.get("included", [])
                if str(i.get("type", "")).endswith("agency")
            }
            for d in js.get("data", []):
                attrs = d.get("attributes", {}) or {}
                rel = ((d.get("relationships") or {}).get("agency") or {}).get("data") or {}
                parent = parents.get(rel.get("id"), {})
                upsert(
                    con,
                    id=d["id"],
                    title=attrs.get("title") or "(untitled)",
                    abbreviation=attrs.get("abbreviation"),
                    agency=parent.get("name"),
                    agency_abbr=parent.get("abbreviation"),
                    email=_first_email(attrs),
                    submission_format=attrs.get("portal_submission_format"),
                    website=_url(attrs.get("website")),
                    raw=json.dumps(attrs),
                )
                n += 1
            con.commit()
            nxt = (js.get("links") or {}).get("next")
            url = nxt.get("href") if isinstance(nxt, dict) else nxt
    finally:
        if client is None:
            c.close()
    return n


def search(con: sqlite3.Connection, q: str, limit: int = 25) -> list[dict]:
    like = f"%{q}%"
    rows = con.execute(
        """SELECT * FROM foia_agencies
           WHERE title LIKE ? OR abbreviation LIKE ? OR agency LIKE ? OR agency_abbr LIKE ? OR id = ?
           ORDER BY (abbreviation = ?) DESC, title LIMIT ?""",
        (like, like, like, like, q, q.upper(), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get(con: sqlite3.Connection, agency_id: str) -> dict | None:
    r = con.execute("SELECT * FROM foia_agencies WHERE id = ?", (agency_id,)).fetchone()
    return dict(r) if r else None
