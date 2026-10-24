"""In-process token buckets: one per (category, principal) and one global per category.

Categories (settings ``SHIMPAKU_RATE_LIMITS``, ``category=per_principal/global`` per minute):
``write`` (REST propose/approve/reject/cancel, token and lease writes), ``demo`` (break/seed),
``ask`` (Ask Shim), ``mcp`` (``tools/call`` on ``/mcp``), ``redeem`` (invite redemption, keyed
by client address). A refused request is answered 429 with ``Retry-After`` by the caller.
The state is per process and resets on restart; the edge adds per-IP limits (build step 8).
"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable


class RateLimiter:
    def __init__(self, table: dict[str, tuple[int, int]], clock: Callable[[], float] = time.monotonic):
        self.table = dict(table)
        self.clock = clock
        self._b: dict[tuple[str, str], tuple[float, float]] = {}  # key -> (tokens, last)
        self._mu = threading.Lock()

    def _take(self, key: tuple[str, str], per_min: int, now: float) -> float | None:
        if per_min <= 0:
            return None  # 0 = unlimited
        rate = per_min / 60.0
        tokens, last = self._b.get(key, (float(per_min), now))
        tokens = min(float(per_min), tokens + (now - last) * rate)
        if tokens < 1.0:
            self._b[key] = (tokens, now)
            return (1.0 - tokens) / rate
        self._b[key] = (tokens - 1.0, now)
        return None

    def check(self, category: str, principal: str) -> int | None:
        """Spend one request. None = allowed; else seconds to wait (>= 1). The global bucket is
        charged only when the principal's own bucket allowed the request."""
        limits = self.table.get(category)
        if not limits:
            return None
        per, glob = limits
        now = self.clock()
        with self._mu:
            wait = self._take((category, principal), per, now)
            if wait is None:
                wait = self._take((category, "*global*"), glob, now)
                if wait is not None:  # give the principal's token back
                    t, last = self._b[(category, principal)]
                    self._b[(category, principal)] = (t + 1.0, last)
        return None if wait is None else max(1, math.ceil(wait))
