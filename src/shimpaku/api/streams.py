"""Caps on the dashboard's polling SSE streams (SLE, actions, live traffic and events, theatre).

Every open stream is a loop that reads the service every 1.5 to 5 s on the lab host, so what costs is
open connections, not requests: the per-minute rate limits never see a stream that stays open. These
streams share one budget, per client address (``PER_IP``) and in all (``TOTAL``). The operator's own
session is not counted: presenting from one laptop must never be crowded out. Past a cap a stream is
429 with ``Retry-After`` and the page polls the same GET instead (``useApi`` in ``web/src/api.ts``).

Virga's stream keeps its own, smaller caps (``routes_virga``); the assistant's answer streams end with
the answer and are bounded by their own rate limits.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from fastapi import HTTPException, Request
from sse_starlette.sse import EventSourceResponse

PER_IP = 12   # a dashboard tab holds one to four streams; this leaves room for a few tabs
TOTAL = 200


class CountedStream(EventSourceResponse):
    """An SSE response that gives its slot back however it ends (also when it never started)."""

    def __init__(self, *a: Any, release: Callable[[], None], **kw: Any):
        super().__init__(*a, **kw)
        self._release = release

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


class StreamCaps:
    def __init__(self, per_ip: int | None = None, total: int | None = None):
        self.per_ip, self.total = per_ip, total  # None: the module values, read at each check
        self.open: dict[str, int] = {}
        self._mu = threading.Lock()

    def acquire(self, ip: str, counted: bool = True) -> Callable[[], None]:
        """A release function for one stream slot; raises 429 past either cap."""
        if not counted:
            return lambda: None
        per_ip = PER_IP if self.per_ip is None else self.per_ip
        total = TOTAL if self.total is None else self.total
        with self._mu:
            if self.open.get(ip, 0) >= per_ip or sum(self.open.values()) >= total:
                raise HTTPException(429, "too many live views from here; the page refreshes on its own instead",
                                    headers={"Retry-After": "60"})
            self.open[ip] = self.open.get(ip, 0) + 1
        done = False

        def release() -> None:
            nonlocal done
            with self._mu:
                if done:
                    return
                done = True
                self.open[ip] -= 1
                if self.open[ip] <= 0:
                    del self.open[ip]

        return release


def caps_for(app: Any) -> StreamCaps:
    caps = getattr(app.state, "stream_caps", None)
    if caps is None:
        caps = app.state.stream_caps = StreamCaps()
    return caps


def capped(request: Request, p: Any, gen: Any, **kw: Any) -> CountedStream:
    """``EventSourceResponse(gen, **kw)`` holding one slot of the shared budget while it is open.
    Acquire before the generator runs, so a refused stream never starts its loop."""
    ip = request.client.host if request.client else "?"
    release = caps_for(request.app).acquire(ip, counted=not getattr(p, "can_present", False))
    return CountedStream(gen, release=release, **kw)
