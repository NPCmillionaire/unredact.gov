from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeliveryResult:
    status: str  # sent | failed | manual | needs_human
    external_id: str | None = None
    detail: str = ""
