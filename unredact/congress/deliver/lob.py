"""Physical letters via Lob (https://docs.lob.com)."""
from __future__ import annotations

import httpx

from unredact.config import Settings, get_settings
from unredact.congress.deliver import DeliveryResult

API = "https://api.lob.com/v1/letters"


def office_address(member: dict, office: dict) -> dict:
    a = {
        "name": f"{'Sen.' if member['chamber'] == 'sen' else 'Rep.'} {member['name']}"[:40],
        "address_line1": office["address"],
        "address_city": office["city"],
        "address_state": office["state"],
        "address_zip": office["zip"],
    }
    line2 = " ".join(x for x in (office.get("suite"), office.get("building")) if x)
    if line2:
        a["address_line2"] = line2[:64]
    return a


def send_letter(*, to: dict, from_: dict, html: str, description: str, idempotency_key: str,
                s: Settings | None = None, client: httpx.Client | None = None) -> DeliveryResult:
    s = s or get_settings()
    if not s.lob_ready:
        return DeliveryResult("failed", detail="LOB_API_KEY not set")
    payload = {
        "description": description[:255],
        "to": to,
        "from": from_,
        "file": html,
        "color": False,
        "double_sided": False,
        "address_placement": "top_first_page",
        "use_type": "operational",
        "mail_type": "usps_first_class",
    }
    c = client or httpx.Client(timeout=60)
    try:
        r = c.post(API, json=payload, auth=(s.lob_api_key, ""), headers={"Idempotency-Key": idempotency_key})
    finally:
        if client is None:
            c.close()
    if r.status_code in (200, 201):
        js = r.json()
        eta = js.get("expected_delivery_date")
        return DeliveryResult("sent", js.get("id"), f"Lob letter created; expected delivery {eta}")
    return DeliveryResult("failed", detail=f"Lob {r.status_code}: {r.text[:500]}")
