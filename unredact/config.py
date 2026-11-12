"""Settings loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    # FOIA requester identity (used in letters)
    requester_name: str
    requester_email: str
    requester_phone: str
    requester_address: str
    # Outbound mail
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_starttls: bool
    # Inbound mail (agency replies)
    imap_host: str
    imap_user: str
    imap_password: str
    imap_folder: str
    # Data sources
    foia_gov_api_key: str
    geocodio_api_key: str
    geocodio_url: str
    # House CWC
    cwc_api_key: str
    cwc_host: str
    cwc_delivery_agent: str
    cwc_ack_email: str
    cwc_contact_name: str
    cwc_contact_email: str
    cwc_contact_phone: str
    cwc_organization: str
    cwc_campaign_id: str
    # Paid letters
    lob_api_key: str
    stripe_secret_key: str
    stripe_webhook_secret: str
    letter_price_cents: int
    # Web
    base_url: str
    admin_password: str
    webform_defs_dir: Path
    # Multi-user web app
    secret_key: str
    admin_emails: tuple
    inbound_address: str       # shared reply-to for user requests (agency replies matched by token)
    inbound_key: str           # shared secret for POST /inbound/email
    allow_signup: bool
    anthropic_api_key: str
    llm_model: str

    @property
    def db_path(self) -> Path:
        legacy = self.data_dir / "redtape.db"
        new = self.data_dir / "unredact.db"
        return legacy if legacy.exists() and not new.exists() else new

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def requester(self) -> dict:
        return {
            "name": self.requester_name,
            "email": self.requester_email,
            "phone": self.requester_phone,
            "address": self.requester_address,
        }

    @property
    def smtp_ready(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def imap_ready(self) -> bool:
        return bool(self.imap_host and self.imap_user)

    @property
    def cwc_ready(self) -> bool:
        return bool(self.cwc_api_key and self.cwc_delivery_agent)

    @property
    def lob_ready(self) -> bool:
        return bool(self.lob_api_key)

    @property
    def stripe_ready(self) -> bool:
        return bool(self.stripe_secret_key and self.lob_api_key)


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    e = os.environ.get
    data_dir = Path(e("UNREDACT_DATA_DIR") or e("REDTAPE_DATA_DIR") or Path.home() / ".local/share/unredact").expanduser()
    return Settings(
        data_dir=data_dir,
        requester_name=e("REQUESTER_NAME", ""),
        requester_email=e("REQUESTER_EMAIL", ""),
        requester_phone=e("REQUESTER_PHONE", ""),
        requester_address=e("REQUESTER_ADDRESS", "").replace("\\n", "\n"),
        smtp_host=e("SMTP_HOST", ""),
        smtp_port=int(e("SMTP_PORT", "587")),
        smtp_user=e("SMTP_USER", ""),
        smtp_password=e("SMTP_PASSWORD", ""),
        smtp_from=e("SMTP_FROM", ""),
        smtp_starttls=_bool(e("SMTP_STARTTLS"), True),
        imap_host=e("IMAP_HOST", ""),
        imap_user=e("IMAP_USER", ""),
        imap_password=e("IMAP_PASSWORD", ""),
        imap_folder=e("IMAP_FOLDER", "INBOX"),
        foia_gov_api_key=e("FOIA_GOV_API_KEY", ""),
        geocodio_api_key=e("GEOCODIO_API_KEY", ""),
        geocodio_url=e("GEOCODIO_URL", "https://api.geocod.io/v1.9/geocode"),
        cwc_api_key=e("CWC_API_KEY", ""),
        cwc_host=e("CWC_HOST", "test-cwc.house.gov"),
        cwc_delivery_agent=e("CWC_DELIVERY_AGENT", ""),
        cwc_ack_email=e("CWC_DELIVERY_AGENT_ACK_EMAIL", ""),
        cwc_contact_name=e("CWC_DELIVERY_AGENT_CONTACT_NAME", ""),
        cwc_contact_email=e("CWC_DELIVERY_AGENT_CONTACT_EMAIL", ""),
        cwc_contact_phone=e("CWC_DELIVERY_AGENT_CONTACT_PHONE", ""),
        cwc_organization=e("CWC_ORGANIZATION", ""),
        cwc_campaign_id=e("CWC_CAMPAIGN_ID", "unredact"),
        lob_api_key=e("LOB_API_KEY", ""),
        stripe_secret_key=e("STRIPE_SECRET_KEY", ""),
        stripe_webhook_secret=e("STRIPE_WEBHOOK_SECRET", ""),
        letter_price_cents=int(e("LETTER_PRICE_CENTS", "300")),
        base_url=e("BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        admin_password=e("UNREDACT_ADMIN_PASSWORD") or e("REDTAPE_ADMIN_PASSWORD", ""),
        webform_defs_dir=Path(e("WEBFORM_DEFS_DIR") or data_dir / "contact-congress" / "members").expanduser(),
        secret_key=e("SECRET_KEY", "") or _persisted_secret(data_dir),
        admin_emails=tuple(x.strip().lower() for x in e("ADMIN_EMAILS", "").split(",") if x.strip()),
        inbound_address=e("INBOUND_ADDRESS", ""),
        inbound_key=e("INBOUND_KEY", ""),
        allow_signup=_bool(e("ALLOW_SIGNUP"), True),
        anthropic_api_key=e("ANTHROPIC_API_KEY", ""),
        llm_model=e("UNREDACT_LLM_MODEL", "claude-haiku-5-5"),
    )


def _persisted_secret(data_dir: Path) -> str:
    """Stable session-signing key for dev installs without SECRET_KEY."""
    import secrets

    f = data_dir / ".secret_key"
    try:
        if f.exists():
            return f.read_text().strip()
        data_dir.mkdir(parents=True, exist_ok=True)
        k = secrets.token_hex(32)
        f.write_text(k)
        f.chmod(0o600)
        return k
    except OSError:
        return secrets.token_hex(32)
