"""The stall watchdog notices a blocked event loop and names the blocking frame."""

import asyncio
import logging
import time

from shimpaku.loopwatch import LoopWatch


def test_a_blocked_loop_is_logged_with_its_stack(caplog):
    w = LoopWatch(stall_s=0.2, tick_s=0.05, log_every_s=0)

    def block_the_loop_for_a_while():
        time.sleep(0.6)  # synchronous: the loop cannot tick

    async def main():
        hb = w.start()
        await asyncio.sleep(0.2)
        block_the_loop_for_a_while()
        await asyncio.sleep(0.3)
        w.stop()
        await hb

    with caplog.at_level(logging.WARNING, logger="shimpaku.loopwatch"):
        asyncio.run(main())
    assert w.stalls == 1 and w.worst_s >= 0.4
    text = caplog.text
    assert "event loop stalled" in text and "block_the_loop_for_a_while" in text
    assert "stall ended after" in text


def test_a_healthy_loop_is_quiet(caplog):
    w = LoopWatch(stall_s=0.2, tick_s=0.05, log_every_s=0)

    async def main():
        hb = w.start()
        await asyncio.sleep(0.5)
        w.stop()
        await hb

    with caplog.at_level(logging.WARNING, logger="shimpaku.loopwatch"):
        asyncio.run(main())
    assert w.stalls == 0 and "stalled" not in caplog.text
