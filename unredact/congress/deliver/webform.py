"""Fill a member's official contact form with Playwright.

Reads form definitions in the unitedstates/contact-congress YAML format
(one file per member, named <BIOGUIDE>.yaml) from WEBFORM_DEFS_DIR. Those community definitions
are old and offices redesign their sites often — treat every definition as something you verify,
and expect CAPTCHAs on some offices (those return status 'needs_human').

    pip install 'unredact[webforms]' && playwright install chromium
"""
from __future__ import annotations

from pathlib import Path

import yaml

from unredact.congress.deliver import DeliveryResult
from unredact.congress.models import STATE_NAMES, Constituent


def build_vars(c: Constituent, subject: str, message: str, topic: str | None) -> dict[str, str]:
    digits = "".join(ch for ch in c.phone if ch.isdigit())[-10:]
    return {
        "$NAME_PREFIX": c.prefix,
        "$NAME_FIRST": c.first,
        "$NAME_LAST": c.last,
        "$NAME_FULL": c.full_name,
        "$ADDRESS_STREET": c.street,
        "$ADDRESS_STREET_2": c.street2,
        "$ADDRESS_CITY": c.city,
        "$ADDRESS_STATE_POSTAL_ABBREV": c.state,
        "$ADDRESS_STATE_FULL": STATE_NAMES.get(c.state, c.state),
        "$ADDRESS_ZIP5": c.zip5,
        "$ADDRESS_ZIP4": c.zip4,
        "$ADDRESS_ZIP_PLUS_4": f"{c.zip5}-{c.zip4}" if c.zip4 else c.zip5,
        "$EMAIL": c.email,
        "$PHONE": f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else c.phone,
        "$PHONE_PARENTHESES": f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if len(digits) == 10 else c.phone,
        "$SUBJECT": subject,
        "$MESSAGE": message,
        "$TOPIC": topic or "",
    }


def resolve(value, vars: dict[str, str]) -> str:
    if isinstance(value, str) and value.startswith("$"):
        return vars.get(value, "")
    return "" if value is None else str(value)


def pick_option(field: dict, vars: dict[str, str]) -> tuple[str, str]:
    """Return (mode, value) where mode is 'value' or 'label'."""
    want = resolve(field.get("value"), vars)
    opts = field.get("options")
    if field.get("value") == "$TOPIC" and opts:
        pairs = list(opts.items()) if isinstance(opts, dict) else [(o, o) for o in opts]
        words = {w.lower() for w in want.replace(",", " ").split() if len(w) > 3}
        for label, val in pairs:
            if words & {w.lower() for w in str(label).replace(",", " ").split()}:
                return "value", str(val)
        return "value", str(pairs[0][1])
    if isinstance(opts, dict):
        for label, val in opts.items():
            if want in (str(label), str(val)):
                return "value", str(val)
    return "value", want


def load_definition(defs_dir: Path, bioguide: str) -> dict | None:
    for name in (f"{bioguide}.yaml", f"{bioguide}.yml"):
        p = defs_dir / name
        if p.exists():
            return yaml.safe_load(p.read_text())
    return None


def needs_captcha(defn: dict) -> bool:
    text = yaml.safe_dump(defn).lower()
    return "captcha" in text


def run(defn: dict, vars: dict[str, str], headless: bool = True, timeout_ms: int = 30000) -> DeliveryResult:
    if needs_captcha(defn):
        return DeliveryResult("needs_human", detail="form requires a CAPTCHA")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return DeliveryResult("failed", detail="playwright not installed: pip install 'unredact[webforms]'")

    steps = (defn.get("contact_form") or {}).get("steps") or []
    success = (defn.get("contact_form") or {}).get("success") or {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            for step in steps:
                (action, args), = step.items()
                if action == "visit":
                    page.goto(args, wait_until="domcontentloaded")
                elif action == "fill_in":
                    for f in args:
                        v = resolve(f.get("value"), vars)
                        if not v and not f.get("required"):
                            continue
                        if f.get("max_length"):
                            v = v[: int(f["max_length"])]
                        page.fill(f["selector"], v)
                elif action == "select":
                    for f in args:
                        _, v = pick_option(f, vars)
                        if not v and not f.get("required"):
                            continue
                        try:
                            page.select_option(f["selector"], value=v)
                        except Exception:
                            page.select_option(f["selector"], label=STATE_NAMES.get(v, v))
                elif action in ("check", "choose"):
                    for f in args:
                        page.check(f["selector"])
                elif action == "uncheck":
                    for f in args:
                        page.uncheck(f["selector"])
                elif action == "click_on":
                    for f in args:
                        page.click(f["selector"])
                elif action == "find":
                    for f in args:
                        page.wait_for_selector(f["selector"])
                elif action == "wait":
                    page.wait_for_timeout(int(float(args) * 1000))
                elif action == "javascript":
                    page.evaluate(args)
                else:
                    return DeliveryResult("failed", detail=f"unsupported step {action!r}")
            page.wait_for_load_state("networkidle")
            contains = (success.get("body") or {}).get("contains")
            if contains and contains not in page.content():
                return DeliveryResult("failed", detail=f"success text {contains!r} not found after submit")
            return DeliveryResult("sent", detail=f"web form submitted ({page.url})")
        except Exception as exc:  # noqa: BLE001 — surface any Playwright failure as a delivery failure
            return DeliveryResult("failed", detail=f"web form error: {exc}")
        finally:
            browser.close()
