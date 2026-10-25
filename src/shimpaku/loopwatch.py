"""Event-loop stall watchdog: when the daemon's asyncio loop stops ticking, log what it is stuck in.

Everything in ``shimpakud`` shares one loop, so one synchronous call (a database read waiting for the
shared connection, a device read, a heavy computation) stalls every request at once, including the
edge's health check (``/api/whoami``, 3 s): the public site then falls back to the replay. This
thread never blocks the loop itself: a coroutine on the loop stamps a heartbeat every ``TICK_S``;
a plain thread checks the stamp, and when it is older than ``STALL_S`` it logs the loop thread's
current stack once per stall (at most one log line every ``LOG_EVERY_S``), then the stall's length
when the loop comes back.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

log = logging.getLogger("shimpaku.loopwatch")

TICK_S = 0.25
STALL_S = 1.0
LOG_EVERY_S = 30.0


class LoopWatch:
    def __init__(self, stall_s: float = STALL_S, tick_s: float = TICK_S, log_every_s: float = LOG_EVERY_S):
        self.stall_s, self.tick_s, self.log_every_s = stall_s, tick_s, log_every_s
        self.beat = time.monotonic()
        self.loop_thread: int | None = None
        self.stalls = 0          # how many stalls were seen (each counted once)
        self.worst_s = 0.0
        self._stop = threading.Event()
        self._last_log = 0.0

    async def heartbeat(self) -> None:
        self.loop_thread = threading.get_ident()
        while not self._stop.is_set():
            self.beat = time.monotonic()
            await asyncio.sleep(self.tick_s)

    def _stack(self) -> str:
        frame = sys._current_frames().get(self.loop_thread or -1)
        if frame is None:
            return "(no frame)"
        # the innermost frames say what it is waiting in; keep the stack short for the log
        return "".join(traceback.format_stack(frame)[-12:])

    def watch(self) -> None:
        in_stall, start = False, 0.0
        while not self._stop.wait(self.tick_s):
            lag = time.monotonic() - self.beat
            if lag > self.stall_s and not in_stall:
                in_stall, start = True, self.beat
                self.stalls += 1
                now = time.monotonic()
                if now - self._last_log >= self.log_every_s:
                    self._last_log = now
                    log.warning("event loop stalled %.1f s so far; it is in:\n%s", lag, self._stack())
            elif lag <= self.stall_s and in_stall:
                in_stall = False
                took = self.beat - start
                self.worst_s = max(self.worst_s, took)
                if took >= 2 * self.stall_s:
                    log.warning("event loop stall ended after %.1f s (%d stalls so far, worst %.1f s)",
                                took, self.stalls, self.worst_s)

    def start(self) -> asyncio.Task:
        threading.Thread(target=self.watch, name="loopwatch", daemon=True).start()
        return asyncio.create_task(self.heartbeat(), name="loopwatch")

    def stop(self) -> None:
        self._stop.set()
