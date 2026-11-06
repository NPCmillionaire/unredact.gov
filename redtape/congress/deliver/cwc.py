"""House Communicating with Congress (CWC) v2 XML delivery.

Requires approval as an Advocacy Vendor (https://www.house.gov/doing-business-with-the-house/communicating-with-congress-cwc)
and a whitelisted source IP — even for the test host. Validate payloads with `redtape congress cwc-validate`
before switching CWC_HOST from test-cwc.house.gov to cwc.house.gov.
"""
from __future__ import annotations

import datetime as dt
import uuid
import xml.etree.ElementTree as ET

import httpx

from redtape.config import Settings, get_settings
from redtape.congress.deliver import DeliveryResult
from redtape.congress.models import TOPICS, Constituent


def office_code(state: str, district: int | None) -> str:
    return f"H{state.upper()}{int(district or 0):02d}"


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def build_xml(*, member: dict, c: Constituent, subject: str, body: str, topic: str | None, s: Settings,
              delivery_id: str | None = None, today: dt.date | None = None) -> bytes:
    root = ET.Element("CWC")
    _sub(root, "CWCVersion", "2.0")

    d = _sub(root, "Delivery")
    _sub(d, "DeliveryId", delivery_id or uuid.uuid4().hex)
    _sub(d, "DeliveryDate", (today or dt.date.today()).strftime("%Y%m%d"))
    _sub(d, "DeliveryAgent", s.cwc_delivery_agent)
    _sub(d, "DeliveryAgentAckEmailAddress", s.cwc_ack_email)
    dc = _sub(d, "DeliveryAgentContact")
    _sub(dc, "DeliveryAgentContactName", s.cwc_contact_name)
    _sub(dc, "DeliveryAgentContactEmail", s.cwc_contact_email)
    _sub(dc, "DeliveryAgentContactPhone", s.cwc_contact_phone)
    if s.cwc_organization:
        _sub(d, "Organization", s.cwc_organization)
    _sub(d, "CampaignId", s.cwc_campaign_id)

    r = _sub(root, "Recipient")
    _sub(r, "MemberOffice", office_code(member["state"], member.get("district")))
    _sub(r, "IsResponseRequested", "Y")
    _sub(r, "NewsletterOptIn", "N")

    k = _sub(root, "Constituent")
    _sub(k, "Prefix", c.prefix)
    _sub(k, "FirstName", c.first)
    _sub(k, "LastName", c.last)
    _sub(k, "Address1", c.street)
    if c.street2:
        _sub(k, "Address2", c.street2)
    _sub(k, "City", c.city)
    _sub(k, "StateAbbreviation", c.state)
    _sub(k, "Zip", c.zip5)
    if c.phone:
        _sub(k, "Phone", c.phone)
    _sub(k, "Email", c.email)

    m = _sub(root, "Message")
    _sub(m, "Subject", subject[:200])
    topics = _sub(m, "LibraryOfCongressTopics")
    _sub(topics, "LibraryOfCongressTopic", topic if topic in TOPICS else "Government Operations and Politics")
    _sub(m, "ConstituentMessage", body)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _post(path: str, xml: bytes, s: Settings, client: httpx.Client | None = None) -> httpx.Response:
    c = client or httpx.Client(timeout=30)
    try:
        return c.post(f"https://{s.cwc_host}{path}", params={"apikey": s.cwc_api_key}, content=xml,
                      headers={"Content-Type": "application/xml"})
    finally:
        if client is None:
            c.close()


def validate(xml: bytes, s: Settings | None = None) -> tuple[bool, str]:
    s = s or get_settings()
    r = _post("/v2/validate", xml, s)
    return r.status_code == 200, r.text


def send(member: dict, c: Constituent, subject: str, body: str, topic: str | None, s: Settings | None = None) -> DeliveryResult:
    s = s or get_settings()
    if not s.cwc_ready:
        return DeliveryResult("failed", detail="CWC not configured")
    delivery_id = uuid.uuid4().hex
    xml = build_xml(member=member, c=c, subject=subject, body=body, topic=topic, s=s, delivery_id=delivery_id)
    r = _post("/v2/message", xml, s)
    if r.status_code == 200:
        return DeliveryResult("sent", delivery_id, f"CWC accepted ({s.cwc_host})")
    return DeliveryResult("failed", delivery_id, f"CWC {r.status_code}: {r.text[:500]}")
