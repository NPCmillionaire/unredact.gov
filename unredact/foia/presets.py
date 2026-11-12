"""Library of ready-made request language (data/presets.yaml)."""
from __future__ import annotations

import re
import string
from functools import lru_cache
from importlib.resources import files

import yaml

AUTO_FIELDS = {"agency_name"}


@lru_cache
def presets() -> dict[str, dict]:
    items = yaml.safe_load(files("unredact.foia").joinpath("data/presets.yaml").read_text())
    return {p["id"]: p for p in items}


def get(preset_id: str) -> dict:
    try:
        return presets()[preset_id]
    except KeyError:
        raise KeyError(f"unknown preset {preset_id!r}; choose from: {', '.join(presets())}") from None


def fields(p: dict) -> list[str]:
    seen: list[str] = []
    for text in (p["subject"], p["description"]):
        for _, name, _, _ in string.Formatter().parse(text):
            if name and name not in seen:
                seen.append(name)
    return seen


def user_fields(p: dict) -> list[str]:
    return [f for f in fields(p) if f not in AUTO_FIELDS]


class _Keep(dict):
    def __missing__(self, key: str) -> str:
        return f"[{key.upper()}]"


def fill(p: dict, values: dict[str, str]) -> tuple[str, str]:
    """Return (subject, description). Missing fields become visible [PLACEHOLDERS] for editing."""
    v = _Keep({k: val for k, val in values.items() if val not in (None, "")})
    return p["subject"].format_map(v), p["description"].format_map(v).strip()


UNFILLED = re.compile(r"\[[A-Z_]+\]")


def unfilled(text: str) -> list[str]:
    return UNFILLED.findall(text)
