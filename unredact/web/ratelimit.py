"""Tiny in-process sliding-window rate limiter (per client IP). Good enough for one uvicorn worker;
use your reverse proxy's limiter if you scale out."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from unredact.config import get_settings


class RateLimiter:
    def __init__(self, window: float = 3600.0):
        self.window = window
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, limit: int, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(request: Request) -> None:
    s = get_settings()
    if s.rate_limit_per_hour > 0 and not limiter.allow(f"{request.url.path}:{client_ip(request)}", s.rate_limit_per_hour):
        raise HTTPException(429, "Too many requests from your network — try again later.")
