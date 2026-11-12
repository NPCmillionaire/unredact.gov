"""Address → state + congressional district.

Default: free US Census Geocoder. Optional: Geocodio (set GEOCODIO_API_KEY) for better match rates.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from unredact.config import Settings, get_settings

CENSUS = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


class GeocodeError(Exception):
    pass


@dataclass
class GeoResult:
    matched_address: str
    street: str
    city: str
    state: str
    zip5: str
    district: int  # 0 = at-large / delegate
    lat: float | None
    lon: float | None


def _district(code) -> int:
    d = int(str(code).strip() or 0)
    return 0 if d in (98, 99) else d  # Census uses 98 for DC/territory delegates


def parse_census(js: dict) -> GeoResult:
    matches = (js.get("result") or {}).get("addressMatches") or []
    if not matches:
        raise GeocodeError("address not found — check spelling or include ZIP")
    m = matches[0]
    geos = m.get("geographies") or {}
    cd_key = next((k for k in geos if "Congressional District" in k), None)
    if not cd_key or not geos[cd_key]:
        raise GeocodeError("no congressional district returned for that address")
    cd = geos[cd_key][0]
    code = next((v for k, v in cd.items() if k.startswith("CD") and k[2:].isdigit()), cd.get("BASENAME", "0"))
    comp = m.get("addressComponents") or {}
    state = comp.get("state") or (geos.get("States") or [{}])[0].get("STUSAB", "")
    street = " ".join(
        str(comp.get(k) or "") for k in ("fromAddress", "preDirection", "streetName", "suffixType", "suffixDirection")
    )
    coords = m.get("coordinates") or {}
    return GeoResult(
        matched_address=m.get("matchedAddress", ""),
        street=" ".join(street.split()),
        city=comp.get("city", ""),
        state=state,
        zip5=str(comp.get("zip", "")),
        district=_district(code),
        lat=coords.get("y"),
        lon=coords.get("x"),
    )


def parse_geocodio(js: dict) -> GeoResult:
    results = js.get("results") or []
    if not results:
        raise GeocodeError("address not found")
    r = results[0]
    comp = r.get("address_components") or {}
    cds = (r.get("fields") or {}).get("congressional_districts") or []
    if not cds:
        raise GeocodeError("no congressional district returned")
    loc = r.get("location") or {}
    street = " ".join(str(comp.get(k) or "") for k in ("number", "predirectional", "street", "suffix", "postdirectional"))
    return GeoResult(
        matched_address=r.get("formatted_address", ""),
        street=" ".join(street.split()),
        city=comp.get("city", ""),
        state=comp.get("state", ""),
        zip5=str(comp.get("zip", "")),
        district=_district(cds[0].get("district_number", 0)),
        lat=loc.get("lat"),
        lon=loc.get("lng"),
    )


def geocode(address: str, s: Settings | None = None, client: httpx.Client | None = None) -> GeoResult:
    s = s or get_settings()
    c = client or httpx.Client(timeout=30)
    try:
        if s.geocodio_api_key:
            r = c.get(s.geocodio_url, params={"q": address, "fields": "cd", "api_key": s.geocodio_api_key})
            r.raise_for_status()
            return parse_geocodio(r.json())
        r = c.get(CENSUS, params={
            "address": address, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
        })
        r.raise_for_status()
        return parse_census(r.json())
    finally:
        if client is None:
            c.close()
