"""Presenter mode and the Junos CLI console (``/api/console/*``).

Presenter mode is the operator's own session showing the clean, visitor-style dashboard while
keeping operator authority server-side for presenter features. It is a flag on the operator's
session row (``auth.AuthStore.set_presenting``), never a separate login, never on a token.

Who may use the console: ``Principal.can_present`` — an operator dashboard session (password +
TOTP on the public site). Visitors, testers, every bearer token (whatever its scope or role) and
anonymous callers get 401/403. The ``presenting`` flag is *not* required (the operator can always
use the console); it only labels the audit row ``role: presenter``.

What runs: one operational-mode command, checked by ``shimpaku.console_policy`` (allow-list,
Junos prefix resolution, pipes), sent over the existing PyEZ/NETCONF read pool as
``dev.cli(cmd, warning=False)`` — never a shell. 20 s timeout, 64 KiB output cap, per-principal
rate limit (category ``console``, default 20/min, 60/min globally), at most two commands running
at once. Every attempt — allowed, refused, failed — is one hash-chained audit row with the device,
the command, the outcome, the duration and the output *length*; the output itself is never stored.

``app.py`` mounts this with one line: ``app.include_router(routes_console.router(call, reader, rate))``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shimpaku import console_policy as policy
from shimpaku.api.auth import Principal

log = logging.getLogger("shimpaku.api.console")

CONSOLE_RATE = (20, 60)  # per principal / global, per minute (overridable: SHIMPAKU_RATE_LIMITS console=a/b)
MAX_CONCURRENT = 2
NOT_PRESENTER = "the console is for the operator's own signed-in session"


class ExecBody(BaseModel):
    device: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9-]{0,31}$")
    command: str = Field(min_length=1, max_length=1024)  # the policy refuses > 256; 1024 so it says why


class PresentBody(BaseModel):
    on: bool


class PyEZRunner:
    """The lab: the daemon's pooled NETCONF read sessions (``shimpaku.devices``)."""

    def devices(self) -> list[str]:
        from shimpaku import devices
        return sorted(devices.inventory().nodes)

    def run(self, device: str, command: str, timeout: float) -> str:
        from shimpaku import devices

        def f(dev):
            old = getattr(dev, "timeout", None)
            dev.timeout = int(timeout)
            try:
                return dev.cli(command, warning=False, format="text")
            finally:
                if old is not None:
                    dev.timeout = old
        out = devices.pool.run(device, f)
        return out if isinstance(out, str) else str(out or "")


class DemoRunner:
    def devices(self) -> list[str]:
        from shimpaku.api import demo_console
        return list(demo_console.DEVICES)

    def run(self, device: str, command: str, timeout: float) -> str:
        from shimpaku.api import demo_console
        return demo_console.run(device, command)


def runner_for(app: Any) -> Any:
    """``app.state.console_runner`` (tests set a fake), else demo data, else the lab."""
    r = getattr(app.state, "console_runner", None)
    if r is not None:
        return r
    return DemoRunner() if getattr(app.state.service, "DEMO", False) else PyEZRunner()


def audit_console(p: Principal, args: dict, status: str) -> None:
    """One hash-chained row. The caller dict is written as-is (``role`` presenter or operator):
    it never reaches the action engine, which grants a ``presenter`` role nothing."""
    from shimpaku import service
    if service._audit is None:
        return
    caller = {"kind": "dashboard", "name": p.name, "role": "presenter" if p.presenting else "operator",
              "transport": "rest", "lease_id": None}
    try:
        service._audit.append(caller=caller, transport="rest", method="console_exec", tool="console_exec",
                              args=args, result_status=status[:40], latency_ms=args.get("duration_ms"),
                              source="rest")
    except Exception:  # noqa: BLE001
        log.exception("console audit append failed")


def router(call: Callable[..., Any], reader: Callable[..., Principal], rate: Callable[[str, Any], None]) -> APIRouter:
    r = APIRouter()
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    def presenter(request: Request, p: Principal = Depends(reader)) -> Principal:
        if not p.can_present or getattr(request.app.state.service, "REPLAY", False):
            raise HTTPException(403, NOT_PRESENTER)
        return p

    @r.post("/api/console/presenting")
    async def set_presenting(body: PresentBody, request: Request, p: Principal = Depends(presenter)) -> dict:
        from shimpaku.api.app import _cookie_names, _is_https
        app = request.app
        if app.state.auth_mode == "off":
            app.state.presenting_off = body.on  # one loopback operator, no session row
        else:
            sname, _ = _cookie_names(_is_https(request))
            if not app.state.auth.set_presenting(request.cookies.get(sname), body.on):
                raise HTTPException(409, "no operator session to put in presenter mode")
        from shimpaku.api import lease as lease_mod
        lease_mod._audit({"kind": "dashboard", "name": p.name, "role": "operator", "transport": "rest"},
                         "presenter_mode", {"on": body.on})
        return {"ok": True, "presenting": body.on}

    @r.get("/api/console/devices")
    async def console_devices(request: Request, p: Principal = Depends(presenter)) -> dict:
        try:
            names = runner_for(request.app).devices()
        except Exception:  # noqa: BLE001
            names = []
        return {"devices": names, "max_output": policy.MAX_OUTPUT, "timeout_s": policy.TIMEOUT_S,
                "quick": ["show interfaces terse", "show ospf neighbor", "show bfd session", "show vlans",
                          "show system alarms"]}

    @r.post("/api/console/exec")
    async def console_exec(body: ExecBody, request: Request, p: Principal = Depends(presenter)) -> dict:
        request.app.state.limiter.table.setdefault("console", CONSOLE_RATE)
        rate("console", p)
        run = runner_for(request.app)
        base = {"device": body.device, "command": body.command[:policy.MAX_LEN + 1]}
        t0 = time.monotonic()

        def done(status: str, **extra: Any) -> None:
            audit_console(p, {**base, **extra, "outcome": status,
                              "duration_ms": round((time.monotonic() - t0) * 1000)}, status)

        try:
            cmd = policy.check(body.command)
        except policy.Refused as e:
            done("refused", reason=e.reason)
            raise HTTPException(422, f"refused: {e.reason}") from None
        try:
            known = body.device in run.devices()
        except Exception:  # noqa: BLE001
            known = False
        if not known:
            done("refused", reason="unknown device")
            raise HTTPException(404, f"unknown device {body.device!r}")
        base["sent"] = cmd.text
        async with sem:
            try:
                raw = await asyncio.wait_for(asyncio.to_thread(run.run, body.device, cmd.text, policy.TIMEOUT_S),
                                             timeout=policy.TIMEOUT_S)
            except asyncio.TimeoutError:
                done("error:timeout")
                raise HTTPException(504, f"no answer from {body.device} within {policy.TIMEOUT_S:.0f} s") from None
            except Exception as e:  # noqa: BLE001  (never the traceback or the device address)
                done(f"error:{type(e).__name__}")
                raise HTTPException(502, f"{body.device}: {type(e).__name__}") from None
        out, cut = policy.cap_output(raw)
        ms = round((time.monotonic() - t0) * 1000)
        done("ok", output_len=len(raw.encode("utf-8", "replace")), truncated=cut)
        return {"device": body.device, "command": cmd.text, "output": out, "truncated": cut,
                "duration_ms": ms, "max_output": policy.MAX_OUTPUT}

    return r
