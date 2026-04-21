"""
Per-hotkey sliding-window rate limiter for the owner API.

Protects write endpoints from a single authenticated validator (or an attacker
pretending to be one) flooding the DB. Default policy: 2 requests per 10 minutes
per hotkey across write endpoints.

Tuned for normal validator cadence — one cycle is ~30 min, during which a
validator does 1 live-start + 1 batch submit + at most 1 cancel. 2/10min is
well above that.

In-memory sliding window is fine for a single-process uvicorn worker. If we
scale out workers, replace with Redis later.
"""

import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "2"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "600"))

# Path prefixes that count against the limit. GETs are not rate-limited here;
# they're idempotent reads against cached snapshots. Admin routes are included
# so a leaked admin key cannot mass-block hotkeys.
_RATE_LIMITED_PREFIXES: Tuple[str, ...] = (
    "/evaluations",
    "/graph",
    "/blocklist",
)
_RATE_LIMITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class _SlidingWindow:
    """O(1) amortized sliding-window counter keyed by hotkey."""

    def __init__(self, max_requests: int, window_seconds: int):
        self._max = max_requests
        self._window = window_seconds
        self._log: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, hotkey: str) -> Tuple[bool, int]:
        """Record an attempt and return (allowed, retry_after_seconds)."""
        now = time.time()
        horizon = now - self._window
        q = self._log[hotkey]
        # Drop requests older than the window
        while q and q[0] < horizon:
            q.popleft()
        if len(q) >= self._max:
            # retry_after = seconds until the oldest logged request falls off
            retry_after = max(1, int(q[0] + self._window - now))
            return False, retry_after
        q.append(now)
        return True, 0


class HotkeyRateLimitMiddleware(BaseHTTPMiddleware):
    """Reject writes from a hotkey exceeding the configured rate.

    The hotkey comes from the ``X-Validator-Hotkey`` header. We enforce
    rate limiting BEFORE signature verification because flooding the DB is
    already expensive even if the signature ends up rejected, and a captured
    valid signature within the replay window (see nonce cache) would otherwise
    slip through.
    """

    def __init__(self, app, max_requests: int = RATE_LIMIT_MAX_REQUESTS, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS):
        super().__init__(app)
        self._window = _SlidingWindow(max_requests, window_seconds)
        self._max_requests = max_requests
        self._window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method not in _RATE_LIMITED_METHODS or not any(
            path.startswith(prefix) for prefix in _RATE_LIMITED_PREFIXES
        ):
            return await call_next(request)

        hotkey = request.headers.get("x-validator-hotkey", "").strip()
        if not hotkey:
            # No hotkey means signature verification will reject it anyway —
            # don't count toward the per-hotkey limit.
            return await call_next(request)

        allowed, retry_after = self._window.allow(hotkey)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded: max {self._max_requests} requests per "
                        f"{self._window_seconds}s per hotkey. Retry in {retry_after}s."
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
