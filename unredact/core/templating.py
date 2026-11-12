"""Jinja environments for letter templates."""
from __future__ import annotations

from functools import lru_cache

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape


@lru_cache
def env(package: str) -> Environment:
    return Environment(
        loader=PackageLoader(package, "templates"),
        undefined=StrictUndefined,
        autoescape=select_autoescape(["html", "html.j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render(package: str, name: str, **ctx) -> str:
    return env(package).get_template(name).render(**ctx)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
