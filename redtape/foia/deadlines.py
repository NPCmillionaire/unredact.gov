"""Statutory response-deadline math."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from importlib.resources import files

import holidays
import yaml


@lru_cache
def rules() -> dict:
    return yaml.safe_load(files("redtape.foia").joinpath("data/jurisdictions.yaml").read_text())


def jurisdiction(code: str | None) -> dict:
    r = rules()
    code = (code or "federal").strip()
    return r.get(code) or r.get(code.upper()) or r.get(code.lower()) or r["default"]


def _holidays(code: str | None):
    subdiv = code.upper() if code and len(code) == 2 else None
    try:
        return holidays.country_holidays("US", subdiv=subdiv)
    except NotImplementedError:
        return holidays.country_holidays("US")


def add_business_days(start: dt.date, n: int, code: str | None = None) -> dt.date:
    hol = _holidays(code)
    d, added = start, 0
    while added < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d not in hol:
            added += 1
    return d


def due_date(code: str | None, submitted: dt.date) -> dt.date | None:
    j = jurisdiction(code)
    days = j.get("days")
    if not days:
        return None
    if j.get("unit") == "calendar":
        return submitted + dt.timedelta(days=days)
    return add_business_days(submitted, days, None if code in (None, "federal") else code)
