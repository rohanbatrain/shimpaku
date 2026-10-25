"""FastAPI app: REST ``/api/*``, SSE ``/api/stream/*``, auth flag, and the built dashboard at ``/``.

Every route calls the CONTRACT service layer (``shimpaku.service`` or a stand-in with the same
function names). Rules enforced here, not in the browser:

- ``approve`` needs a session user (cookie). A bearer token never approves, whatever its scope.
- Bearer scopes: ``read`` = GETs and the assistant; ``propose`` = also propose and cancel.
- Every non-GET that rides on cookies passes an Origin / Sec-Fetch-Site check and a double-submit
  CSRF token (cookie value echoed in ``X-CSRF-Token``). Bearer requests carry no cookies and are exempt.
- Tester passes (``shimpaku.api.lease``): an invite redeemed at ``POST /api/invites/redeem``
  gives a tester session bound to one lease; a tester writes only while that lease is active, and
  the engine limits what it may write. Invites, leases and tokens are operator-only surfaces.
- Writes, demo operations and Ask Shim are rate-limited per principal and globally (429 with
  ``Retry-After``; ``shimpaku.api.ratelimit``).
- Public view (``SHIMPAKU_PUBLIC_VIEW=1``, auth mode local): a caller with no session is a
  ``visitor`` principal: GET routes and SSE only, rate-limited per client address, no Ask Shim, no
  writes, no tokens. Strangers ask for a live session at ``POST /api/session-requests``; the
  operator approves a slot (``shimpaku.api.requests``) and the requester gets a slot-bound invite.
- The session cookie is ``__Host-shimpaku_session`` (Secure) only over https; over plain http it is
  ``shimpaku_session`` without Secure, and ``/api/settings`` reports that so the UI can warn.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import time
import logging
import os
import secrets
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from shimpaku.api import view
from shimpaku.api import lease as lease_mod
from shimpaku.api import requests as req_mod
from shimpaku.api.auth import ADMIN_USER, OFF_MODE_USER, SCOPES, AuthStore, Principal, load_admin_hash, verify_password
from shimpaku.api.llm_keys import LLMKeyError
from shimpaku.api.ratelimit import RateLimiter
from shimpaku.api.totp import TotpStore, totp_file
from shimpaku.config import settings

log = logging.getLogger("shimpaku.api")

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
REPLAY_READ_ONLY = "read-only replay: the write path runs only against the private lab"
ERROR_STATUS = {"not_found": 404, "unknown_device": 404, "bad_state": 409, "forbidden": 403,
                "bad_argument": 422, "propose_failed": 422, "busy": 409, "device_error": 502, "device_unreachable": 502, "provider_failed": 502}

SLE_TICK_S = 10.0
ACTIONS_TICK_S = 2.0
EVENTS_TICK_S = 5.0


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    username: str = ADMIN_USER
    code: str | None = Field(default=None, max_length=16)           # 6-digit TOTP code
    recovery_code: str | None = Field(default=None, max_length=64)  # one-time recovery code


TOTP_SETUP_NEEDED = ("Two-factor sign-in is required on the public site and is not set up. On the lab host run "
                     "`shimpaku admin totp enable`, add the code to your authenticator app, then sign in again.")


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 ._-]+$")
    scope: str = "read"


class ProposeBody(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=4000)


class InviteBody(BaseModel):
    note: str = Field(default="", max_length=500)
    ttl_hours: float | None = Field(default=None, gt=0, le=24 * 30)


class EraseBody(BaseModel):
    token: str = ""  # any bad token (empty, long, unknown) is the same 404, never a 422


class RedeemBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    consent: bool = False
    display_name: str | None = Field(default=None, max_length=60)


def invite_link(token: str) -> str | None:
    """Built only from the configured SHIMPAKU_PUBLIC_URL, never from a request's Host header."""
    base = (settings.public_url or "").rstrip("/")
    return f"{base}/start?i={token}" if base else None


NO_REFERRER_PATHS = ("/start", "/api/invites/", "/request/status", "/api/session-requests/status")


class SessionRequestBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    contact: str = Field(min_length=3, max_length=200)
    message: str = Field(default="", max_length=500)
    slots: list[AwareDatetime] = Field(min_length=1, max_length=req_mod.MAX_SLOTS)
    consent: bool = False


class ApproveBody(BaseModel):
    slot_start: AwareDatetime


class DeclineBody(BaseModel):
    note: str = Field(default="", max_length=500)


VISITOR_ASK = "Request a live session to use Shim"


class AskTurn(BaseModel):
    q: str = Field(max_length=2000)
    a: str = Field(max_length=2000)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    context: dict[str, Any] | None = None
    # the browser's last few turns of this conversation, so Shim can follow up (never stored here)
    history: list[AskTurn] = Field(default_factory=list, max_length=8)


def _cookie_names(secure: bool) -> tuple[str, str]:
    return ("__Host-shimpaku_session", "__Host-shimpaku_csrf") if secure else ("shimpaku_session", "shimpaku_csrf")


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https"


MAX_API_BODY = 64 * 1024  # no REST body needs more; the MCP gate has its own 1 MiB cap


class BodyLimit:
    """ASGI: refuse a request body over ``limit`` bytes with 413 before any route parses it
    (declared Content-Length or, for chunked bodies, as it streams in)."""

    def __init__(self, app, limit: int = MAX_API_BODY):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def too_large():
            body = b'{"detail":"request body too large"}'
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > self.limit:
                        return await too_large()
                except ValueError:
                    return await too_large()
        chunks, size = [], 0
        while True:
            msg = await receive()
            if msg["type"] != "http.request":
                chunks.append(msg)
                break
            size += len(msg.get("body", b""))
            if size > self.limit:
                return await too_large()
            chunks.append(msg)
            if not msg.get("more_body"):
                break

        async def replay():
            return chunks.pop(0) if chunks else await receive()
        return await self.app(scope, replay, send)


def create_app(
    service: Any,
    store: Any = None,
    statedb: Path | str | None = None,
    *,
    assistant: Callable[..., AsyncIterator[dict]] | None = None,
    auth_mode: str | None = None,
    web_dist: Path | None = None,
    demo: bool | None = None,
    leases: "lease_mod.LeaseManager | None" = None,
    limiter: RateLimiter | None = None,
    public_view: bool | None = None,
    totp: TotpStore | None = None,
) -> FastAPI:
    """Build the app.

    ``service``: object or module exposing the CONTRACT async functions. ``store``: telemetry store
    (unused by the routes today; kept for the daemon's wiring). ``statedb``: SQLite path for the
    ``session`` and ``token`` tables (``None`` = in memory, for tests). ``assistant``: async-generator
    function ``(question, principal, context) -> events``; defaults to ``service.assistant_ask``.
    """
    mode = (auth_mode or settings.auth_mode).lower()
    if mode not in ("off", "local"):
        raise ValueError(f"SHIMPAKU_AUTH_MODE={mode!r}: supported modes are off and local")
    is_demo = bool(getattr(service, "DEMO", False)) if demo is None else demo
    # Replay (public host): every visitor is an anonymous reader; nothing signs in or writes.
    is_replay = bool(getattr(service, "REPLAY", False))
    replay_info = service.info() if is_replay else None
    auth = leases.auth if leases is not None else AuthStore(statedb)
    ask_fn = assistant or getattr(service, "assistant_ask", None)
    if leases is None:
        real = getattr(service, "__name__", "") == "shimpaku.service"
        leases = lease_mod.from_settings(auth, service, action_store=(lambda: service._engine().store) if real else None)
    limiter = limiter or RateLimiter(settings.rate_limit_table())
    # anonymous read-only visitors: only in local mode (off mode already makes everyone the operator)
    public = (settings.public_view if public_view is None else public_view) and mode == "local" and not is_replay
    requests_ = leases.requests
    dist = web_dist or WEB_DIST
    totp = totp or TotpStore(totp_file(settings.data))
    # operator sessions on the public site are short (shared computers); unchanged otherwise
    op_idle_s = int(settings.operator_idle_min * 60) if public else None
    op_abs_s = int(settings.operator_max_h * 3600) if public else None

    app = FastAPI(title="Shimpaku", version="0.0.1", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(BodyLimit)
    app.state.auth = auth
    app.state.service = service
    app.state.store = store
    app.state.auth_mode = mode
    app.state.leases = leases
    app.state.limiter = limiter
    app.state.public_view = public
    app.state.totp = totp

    async def call(name: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(service, name, None)
        if fn is None:
            raise HTTPException(501, f"service.{name} is not available yet")
        try:
            res = fn(*args, **kwargs)
            if inspect.isawaitable(res):
                res = await res
        except KeyError as e:
            raise HTTPException(404, f"not found: {e.args[0] if e.args else ''}") from None
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        # The service returns errors as {"error": {"code", "message"}} instead of raising.
        if isinstance(res, dict) and isinstance(res.get("error"), dict):
            code = res["error"].get("code", "")
            status = ERROR_STATUS.get(code, 502)
            raise HTTPException(status, res["error"].get("message") or code)
        return res

    # ---- security headers ------------------------------------------------------
    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        # invite pages carry the token in the URL: send no Referer anywhere from them (OWASP)
        no_ref = request.url.path in ("/start", "/request/status") or request.url.path.startswith(NO_REFERRER_PATHS[1:])
        resp.headers["Referrer-Policy"] = "no-referrer" if no_ref else resp.headers.get("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
        return resp

    # ---- principals ------------------------------------------------------------
    VISITOR = Principal("anonymous", "visitor", "read", role="visitor")

    def principal_or_none(request: Request) -> Principal | None:
        if is_replay:
            return Principal("anonymous", "visitor", "read")
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer "):
            p = auth.token_principal(authz[7:].strip())
            # a tester's token works only while its lease is active
            if p is not None and p.effective_role == "tester" and not leases.is_active(p.lease_id):
                p = None
            if p is None:
                raise HTTPException(401, "invalid, expired or revoked token", headers={"WWW-Authenticate": "Bearer"})
            return p
        sname, _ = _cookie_names(_is_https(request))
        rec = auth.session_record(request.cookies.get(sname))
        if rec and rec.get("role") == "tester":
            if not leases.is_live(rec.get("lease_id")):
                if mode == "off":
                    return Principal("session", OFF_MODE_USER, "admin")
                return VISITOR if public else None
            return Principal("session", rec["user"], "tester", role="tester", lease_id=rec["lease_id"])
        if mode == "off":
            return Principal("session", OFF_MODE_USER, "admin", presenting=bool(getattr(app.state, "presenting_off", False)))
        if rec:
            return Principal("session", rec["user"], "admin", presenting=bool(rec.get("presenting")))
        return VISITOR if public else None

    def check_csrf(request: Request, p: Principal | None) -> None:
        if request.method in SAFE_METHODS or (p is not None and p.kind == "token"):
            return
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if origin is not None and origin != expected:
            raise HTTPException(403, "cross-origin request refused")
        if origin is None and request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
            raise HTTPException(403, "cross-site request refused")
        _, cname = _cookie_names(_is_https(request))
        cookie = request.cookies.get(cname, "")
        header = request.headers.get("x-csrf-token", "")
        if not cookie or not hmac.compare_digest(cookie, header):
            raise HTTPException(403, "missing or wrong CSRF token")

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    def reader(request: Request) -> Principal:
        p = principal_or_none(request)
        if p is None:
            raise HTTPException(401, "sign in required")
        check_csrf(request, p)
        if p is VISITOR:
            # anonymous reads: no audit rows (too noisy); limited per address, logged without it
            rate("read", f"ip:{client_ip(request)}")
            log.info("visitor %s %s", request.method, request.url.path)
        return p

    def rate(category: str, p: Principal | str) -> None:
        key = p if isinstance(p, str) else (p.token_id or f"{p.name}:{p.lease_id or ''}")
        wait = limiter.check(category, key)
        if wait is not None:
            raise HTTPException(429, f"rate limit ({category}); retry in {wait} s", headers={"Retry-After": str(wait)})

    def require_lease(p: Principal) -> None:
        if p.effective_role == "tester" and not leases.is_active(p.lease_id):
            info = leases.lease(p.lease_id) or {}
            pos = info.get("queue_position")
            raise HTTPException(403, f"your lease has not started (queue position {pos})" if pos
                                else "your lease is not active")

    def proposer(p: Principal = Depends(reader)) -> Principal:
        if is_replay:
            raise HTTPException(403, REPLAY_READ_ONLY)
        if not p.can_propose:
            raise HTTPException(403, f"{p.name} has scope {p.scope}; proposing needs scope propose")
        require_lease(p)
        rate("write", p)
        return p

    def session_user(p: Principal = Depends(reader)) -> Principal:
        if is_replay:
            raise HTTPException(403, REPLAY_READ_ONLY)
        if not p.can_approve:
            raise HTTPException(403, "this needs a signed-in dashboard user; tokens cannot do it")
        require_lease(p)
        return p

    def writer(p: Principal = Depends(session_user)) -> Principal:
        rate("write", p)
        return p

    def operator(p: Principal = Depends(reader)) -> Principal:
        if is_replay:
            raise HTTPException(403, REPLAY_READ_ONLY)
        if not p.is_operator:
            raise HTTPException(403, "operator only")
        return p

    def set_csrf(request: Request, response: Response, rotate: bool = False) -> None:
        secure = _is_https(request)
        _, cname = _cookie_names(secure)
        if rotate or not request.cookies.get(cname):
            response.set_cookie(cname, secrets.token_urlsafe(24), httponly=False, secure=secure, samesite="lax", path="/")

    # ---- session ---------------------------------------------------------------
    @app.get("/api/whoami")
    async def whoami(request: Request, response: Response) -> dict:
        set_csrf(request, response)
        try:
            p = principal_or_none(request)
        except HTTPException:
            p = None
        return {
            "mode": mode, "demo": is_demo, "replay": replay_info, "authenticated": p is not None,
            "user": p.name if p else None, "kind": p.kind if p else None, "scope": p.scope if p else None,
            "can_approve": bool(p and p.can_approve), "can_propose": bool(p and p.can_propose),
            "role": p.effective_role if p else None, "lease_id": p.lease_id if p else None,
            "public_view": public, "presenting": bool(p and p.can_present and p.presenting),
            # sign-in form hints: whether a code is asked for, and the operator idle timeout
            "totp_required": mode == "local" and (public or totp.enabled),
            "operator_idle_min": settings.operator_idle_min if public else None,
        }

    @app.get("/api/settings")
    async def get_settings(request: Request, p: Principal = Depends(reader)) -> dict:
        secure = _is_https(request)
        sname, cname = _cookie_names(secure)
        return {
            "auth_mode": mode, "demo": is_demo, "replay": replay_info, "https": secure, "cookie_secure": secure,
            "session_cookie": sname, "csrf_cookie": cname,
            "cookie_warning": None if secure or mode == "off" else
            "Served over plain http: the session cookie is not Secure and has no __Host- prefix. "
            "Use the ssh tunnel to 127.0.0.1 or serve over https.",
            "bind": settings.bind, "port": settings.port,
            "mcp_url": f"{request.url.scheme}://{request.url.netloc}/mcp",
            "mcp_loopback_only": mode == "off",
            "models": settings.models(), "token_scopes": list(SCOPES),
            "admin_configured": load_admin_hash(settings.data) is not None,
            "public_view": public, "totp_enabled": totp.enabled,
            "operator_session": ({"idle_min": settings.operator_idle_min, "max_h": settings.operator_max_h,
                                  "persistent_cookie": False} if public else
                                 {"idle_min": 12 * 60, "max_h": 7 * 24, "persistent_cookie": False}),
        }

    def audit_login(status: str, args: dict, ok: bool) -> None:
        # never the password, the code or the recovery code: only which factor and why it failed
        caller = ({"kind": "dashboard", "name": ADMIN_USER, "role": "operator", "transport": "rest"} if ok else
                  {"kind": "dashboard", "name": "anonymous", "role": "visitor", "transport": "rest"})
        lease_mod._audit(caller, "login", args, status=status)

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request, response: Response) -> dict:
        check_csrf(request, None)
        if mode != "local":
            raise HTTPException(400, f"auth mode is {mode}; there is nothing to sign in to")
        client = client_ip(request)  # the real client: uvicorn trusts only SHIMPAKU_TRUSTED_PROXIES
        if not auth.login_allowed(client):
            audit_login("rate_limited", {"reason": "rate_limited"}, ok=False)
            raise HTTPException(429, "too many sign-in attempts; wait a minute")
        two_factor = totp.enabled
        pw_ok = body.username == ADMIN_USER and verify_password(load_admin_hash(settings.data), body.password)
        if not pw_ok:
            audit_login("denied", {"reason": "bad_credentials"}, ok=False)
            log.info("login failed (password)")
            raise HTTPException(401, "wrong password or code" if two_factor or public else "wrong user name or password")
        if public and not two_factor:
            audit_login("denied", {"reason": "totp_not_enabled"}, ok=False)
            raise HTTPException(403, TOTP_SETUP_NEEDED)
        factor = "password"
        if two_factor:
            if body.recovery_code:
                ok, factor = totp.use_recovery(body.recovery_code), "recovery_code"
            elif body.code:
                ok, factor = totp.verify_code(body.code), "totp"
            else:
                audit_login("denied", {"reason": "code_missing"}, ok=False)
                raise HTTPException(401, "enter the 6-digit code from your authenticator app")
            if not ok:
                audit_login("denied", {"reason": f"bad_{factor}"}, ok=False)
                log.info("login failed (%s)", factor)
                raise HTTPException(401, "wrong password or code")
        secure = _is_https(request)
        sname, _ = _cookie_names(secure)
        auth.drop_session(request.cookies.get(sname))  # rotate at login
        sid = auth.create_session(ADMIN_USER, idle_s=op_idle_s, absolute_s=op_abs_s)
        # no max_age / expires: a browser-session cookie, gone when the browser closes
        response.set_cookie(sname, sid, httponly=True, secure=secure, samesite="lax", path="/")
        set_csrf(request, response, rotate=True)
        audit_login("ok", {"factor": factor}, ok=True)
        log.info("login ok user=%s factor=%s", ADMIN_USER, factor)
        out = {"ok": True, "user": ADMIN_USER}
        if factor == "recovery_code":
            out["recovery_codes_left"] = totp.status().get("recovery_codes_left")
        return out

    @app.post("/api/logout")
    async def logout(request: Request, response: Response) -> dict:
        check_csrf(request, None)
        secure = _is_https(request)
        sname, _ = _cookie_names(secure)
        auth.drop_session(request.cookies.get(sname))
        response.delete_cookie(sname, path="/", secure=secure, httponly=True, samesite="lax")
        return {"ok": True}

    @app.post("/api/logout-all")
    async def logout_all(request: Request, response: Response, p: Principal = Depends(reader)) -> dict:
        if not p.is_operator:
            raise HTTPException(403, "operator only")
        n = auth.drop_operator_sessions()
        secure = _is_https(request)
        sname, _ = _cookie_names(secure)
        response.delete_cookie(sname, path="/", secure=secure, httponly=True, samesite="lax")
        lease_mod._audit({"kind": "dashboard", "name": p.name, "role": "operator", "transport": "rest"},
                         "logout_all", {"sessions": n})
        return {"ok": True, "sessions": n}

    # ---- tokens (Connected agents) -----------------------------------------------
    @app.get("/api/tokens")
    async def list_tokens(p: Principal = Depends(operator)) -> dict:
        return {"tokens": auth.list_tokens(), "scopes": list(SCOPES)}

    @app.post("/api/tokens", status_code=201)
    async def create_token(body: TokenBody, p: Principal = Depends(operator)) -> dict:
        if body.scope not in SCOPES:
            raise HTTPException(422, f"scope must be one of {list(SCOPES)}; approve is never a token scope")
        return auth.create_token(body.name, body.scope, p.name)

    @app.delete("/api/tokens/{token_id}")
    async def revoke_token(token_id: str, p: Principal = Depends(operator)) -> dict:
        if not auth.revoke_token(token_id):
            raise HTTPException(404, "no such active token")
        return {"ok": True}

    # ---- reads -----------------------------------------------------------------
    @app.get("/api/inventory")
    async def inventory(site: str | None = None, p: Principal = Depends(reader)):
        out = view.inventory(await call("inventory_list", site=site))
        if p.effective_role in ("tester", "visitor") and isinstance(out, dict) and isinstance(out.get("devices"), list):
            out = {**out, "devices": [{k: v for k, v in d.items() if k != "mgmt_ip"} for d in out["devices"]]}  # D4: addresses withheld
        return out

    @app.get("/api/sle")
    async def sle(site: str = "campus", window: int = 60, p: Principal = Depends(reader)):
        return view.sle_summary(await call("sle_get_summary", site, window_min=max(5, min(window, 7 * 24 * 60))))

    @app.get("/api/incidents")
    async def incidents(status: str | None = None, p: Principal = Depends(reader)):
        return view.incident_list(await call("incident_list", status=status))

    @app.get("/api/incidents/{incident_id}")
    async def incident(incident_id: str, p: Principal = Depends(reader)):
        return view.incident(await call("incident_get", incident_id))

    @app.get("/api/incidents/{incident_id}/blast-radius")
    async def blast(incident_id: str, p: Principal = Depends(reader)):
        return view.blast_radius(await call("sle_get_blast_radius", incident_id))

    @app.get("/api/actions")
    async def actions(status: str | None = None, p: Principal = Depends(reader)):
        return view.action_list(await call("action_list", status=status))

    @app.get("/api/actions/{action_id}")
    async def action(action_id: str, p: Principal = Depends(reader)):
        return view.action(await call("action_get", action_id))

    @app.get("/api/audit")
    async def audit(limit: int = 100, tool: str | None = None, action_id: str | None = None,
                    status: str | None = None, p: Principal = Depends(reader)):
        return view.audit(await call("audit_query", tool=tool, action_id=action_id, status=status,
                                     limit=max(1, min(limit, 1000))))

    @app.get("/api/audit/verify")
    async def audit_verify(p: Principal = Depends(reader)):
        """Recompute the hash chain over every audit row; reports the first broken row, if any."""
        return await call("audit_verify")

    @app.get("/api/topology")
    async def topology(device: str | None = None, p: Principal = Depends(reader)):
        return view.topology(await call("topo_get_neighbors", device=device))

    @app.get("/api/bench/results")
    async def bench(p: Principal = Depends(reader)):
        return view.bench(await call("bench_results"))

    # ---- writes ----------------------------------------------------------------
    @app.post("/api/actions", status_code=201)
    async def propose(body: ProposeBody, p: Principal = Depends(proposer)):
        return await call("action_propose", body.kind, body.args, body.reason, p.caller())

    @app.post("/api/actions/{action_id}/approve")
    async def approve(action_id: str, p: Principal = Depends(writer)):
        return await call("action_approve", action_id, p.caller())

    @app.post("/api/actions/{action_id}/cancel")
    async def cancel(action_id: str, p: Principal = Depends(proposer)):
        return await call("action_cancel", action_id, p.caller())

    @app.post("/api/actions/{action_id}/reject")
    async def reject(action_id: str, p: Principal = Depends(writer)):
        return await call("action_reject", action_id, p.caller())

    tester_asks: dict[str, int] = {}  # lease id -> Shim questions asked on the shared key
    public_asks: dict[str, list[float]] = {}  # client IP -> times of guide-mode questions (10 min)
    public_day: dict[str, int] = {}  # "YYYY-MM-DD" -> guide-mode questions that day

    @app.options("/api/assistant/public")
    async def ask_public_preflight(request: Request):
        from shimpaku.api.routes_public import cors_origins
        origin = request.headers.get("origin", "")
        h = {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST",
             "Access-Control-Allow-Headers": "content-type", "Access-Control-Max-Age": "600"} if origin in cors_origins() else {}
        return Response(status_code=204, headers=h)

    @app.post("/api/assistant/public")
    async def ask_public(request: Request):
        """Shim's guide mode for anyone, signed in or not, including the static landing page (CORS
        for SHIMPAKU_PUBLIC_CORS_ORIGINS). It reads only Shimpaku's documents: no device, incident or
        action tool is offered, nothing is proposed. No cookie is used, so there is no CSRF surface;
        the body may be sent as text/plain to stay a simple (preflight-free) request."""
        from shimpaku.api.routes_public import cors_origins
        origin = request.headers.get("origin", "")
        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin in cors_origins() else {}

        def fail(code: int, msg: str) -> JSONResponse:  # errors carry CORS too, so the landing page can show them
            return JSONResponse({"detail": msg}, status_code=code, headers=cors)

        if origin and not cors and origin != f"{request.url.scheme}://{request.url.netloc}":
            return fail(403, "cross-origin request refused")
        if not settings.public_shim or ask_fn is None or "guide" not in inspect.signature(ask_fn).parameters:
            return fail(503, "Shim's public guide is not enabled on this server")
        raw = await request.body()
        if len(raw) > 32_000:
            return fail(413, "request too large")
        try:
            body = AskBody.model_validate_json(raw)
        except Exception:  # noqa: BLE001
            return fail(422, "send {question, history?}")
        if len(body.question) > 600:
            return fail(422, "Questions are limited to 600 characters here")
        ip, now = client_ip(request), time.time()
        recent = [t for t in public_asks.get(ip, []) if now - t < 600]
        day = time.strftime("%Y-%m-%d")
        if len(recent) >= settings.public_shim_per_ip:
            return fail(429, "That's a lot of questions! Shim's public guide takes a short break; try again in a few minutes, or request a live session for the full assistant.")
        if public_day.get(day, 0) >= settings.public_shim_daily:
            return fail(429, "Shim's public guide has answered all it can today. Request a live session for the full assistant.")
        public_asks[ip] = recent + [now]
        if day not in public_day:
            public_day.clear()
        public_day[day] = public_day.get(day, 0) + 1
        hist = [t.model_dump() for t in body.history][-4:]

        async def gen():
            try:
                async for ev in ask_fn(body.question, "public", {"page": "public"}, guide=True, history=hist):
                    if await request.is_disconnected():
                        break
                    yield {"event": ev.get("type", "message"), "data": json.dumps(ev)}
            except Exception:  # noqa: BLE001
                log.exception("public guide failed")
                yield {"event": "error", "data": json.dumps({"type": "error", "message": "Shim could not answer"})}

        return EventSourceResponse(gen(), ping=15, headers=cors)

    @app.post("/api/assistant/ask")
    async def ask(body: AskBody, request: Request, p: Principal = Depends(reader)):
        if p.effective_role == "visitor" and p.kind == "anonymous":
            raise HTTPException(403, VISITOR_ASK)  # LLM spend: only operators and live sessions ask
        if ask_fn is None:
            raise HTTPException(503, "the assistant is not wired into this daemon")
        extra: dict = {}
        if p.effective_role == "tester":
            require_lease(p)
            # a tester's questions spend only the lease's own LiteLLM key, never the operator's
            if "llm_key" not in inspect.signature(ask_fn).parameters:
                raise HTTPException(503, "Ask Shim is not available to testers on this server")
            try:
                key = await leases.llm_key(p.lease_id or "")
            except LLMKeyError:
                raise HTTPException(503, "Ask Shim is unavailable: the LLM gateway refused a key for this lease") from None
            if key is not None:
                extra["llm_key"] = key
            elif settings.tester_shim_shared_key:
                # opt-in: no per-lease key, so the operator's key with a per-lease question cap
                used = tester_asks.get(p.lease_id or "", 0)
                if used >= settings.tester_shim_questions:
                    raise HTTPException(429, f"This session's {settings.tester_shim_questions} Shim questions are used up")
                tester_asks[p.lease_id or ""] = used + 1
            else:
                raise HTTPException(503, "Ask Shim is not enabled for testers on this server "
                                         "(no LLM admin key is configured)")
        rate("ask", p)
        params = inspect.signature(ask_fn).parameters
        if "history" in params and body.history:
            extra["history"] = [t.model_dump() for t in body.history]
        if "role" in params:
            # Shim proposes as the asker: a principal that may not propose (a `read` token) gets a
            # visitor's Shim, which the engine refuses to propose or cancel for
            extra["role"] = p.effective_role if p.can_propose else "visitor"
            extra["lease_id"] = p.lease_id

        async def gen():
            try:
                async for ev in ask_fn(body.question, p.name, body.context, **extra):
                    if await request.is_disconnected():
                        break
                    yield {"event": ev.get("type", "message"), "data": json.dumps(ev)}
            except Exception as e:  # stream the failure; never the traceback
                log.exception("assistant failed")
                yield {"event": "error", "data": json.dumps({"type": "error", "message": type(e).__name__})}

        return EventSourceResponse(gen(), ping=15)

    # ---- tester passes: invites, leases, demo fixture ----------------------------------
    @app.get("/api/invites/status")
    async def invite_status(i: str = "") -> dict:
        """Does NOT consume the invite (link previews fetch the link too). Notice fields included."""
        st = leases.invite_status(i) if i and not is_replay else {"valid": False, "reason": "invalid"}
        return {**st, "retention_days": settings.retention_days, "lease_minutes": int(leases.lease_s // 60),
                "contact": settings.privacy_contact}

    @app.post("/api/invites/redeem")
    async def invite_redeem(body: RedeemBody, request: Request, response: Response) -> dict:
        check_csrf(request, None)
        if is_replay:
            raise HTTPException(403, REPLAY_READ_ONLY)
        rate("redeem", f"ip:{request.client.host if request.client else '?'}")
        try:
            info = leases.redeem(body.token, consent=body.consent, display_name=body.display_name)
        except lease_mod.InviteError as e:
            status = {"consent": 422, "invalid": 404, "early": 425}.get(e.code, 410)
            raise HTTPException(status, str(e)) from None
        secure = _is_https(request)
        sname, _ = _cookie_names(secure)
        auth.drop_session(request.cookies.get(sname))
        sid = auth.create_session(lease_mod.tester_name(info["invite_id"]), role="tester", lease_id=info["id"])
        response.set_cookie(sname, sid, httponly=True, secure=secure, samesite="lax", path="/")
        set_csrf(request, response, rotate=True)
        return {"ok": True, "lease": _public_lease(info)}

    def _public_lease(info: dict | None) -> dict | None:
        if info is None:
            return None
        return {k: v for k, v in info.items() if k != "display_name"}

    @app.get("/api/lease")
    async def get_lease(p: Principal = Depends(reader)) -> dict:
        role = p.effective_role
        assistant_ok = bool(ask_fn is not None and "llm_key" in inspect.signature(ask_fn).parameters
                            and ((leases.keys and leases.keys.enabled) or settings.tester_shim_shared_key))
        if role == "tester":
            return {"role": role, "lease": _public_lease(leases.lease(p.lease_id)), "assistant": assistant_ok,
                    "demo_ops": leases.demo_ops}
        if p.is_operator:
            live = [x for x in leases.leases() if x["state"] in ("queued", "active", "ending")]
            return {"role": role, "lease": None, "leases": [_public_lease(x) for x in live]}
        return {"role": role, "lease": None}

    @app.post("/api/lease/end")
    async def end_lease(request: Request, response: Response, p: Principal = Depends(reader)) -> dict:
        if p.effective_role != "tester" or p.kind != "session":
            raise HTTPException(403, "only a tester ends their own lease here; operators use /api/admin/leases")
        rate("write", p)
        info = await leases.end(p.lease_id or "", "tester")
        secure = _is_https(request)
        sname, _ = _cookie_names(secure)
        response.delete_cookie(sname, path="/", secure=secure, httponly=True, samesite="lax")
        return {"ok": True, "lease": _public_lease(info)}

    @app.post("/api/lease/erase")
    async def erase_lease(request: Request, response: Response, p: Principal = Depends(reader)) -> dict:
        """The tester erases their own data: invite note, display name (and the request behind the
        invite); the lease ends through the normal cleanup."""
        if p.effective_role != "tester" or p.kind != "session":
            raise HTTPException(403, "only a tester erases their own pass here")
        rate("erase", f"ip:{client_ip(request)}")
        info = leases.lease(p.lease_id) or {}
        if not info.get("invite_id") or not leases.erase_tester(info["invite_id"]):
            raise HTTPException(404, "no such pass")
        await leases.tick()
        secure = _is_https(request)
        sname, _ = _cookie_names(secure)
        response.delete_cookie(sname, path="/", secure=secure, httponly=True, samesite="lax")
        return {"ok": True, "erased": True}

    @app.post("/api/invites/erase")
    async def erase_invite(body: EraseBody, request: Request) -> dict:
        """Erase via the original invite link, in any state (used, expired, revoked). Any unknown
        token is the same 404."""
        check_csrf(request, None)
        if is_replay:
            raise HTTPException(403, REPLAY_READ_ONLY)
        rate("erase", f"ip:{client_ip(request)}")
        if leases.erase_by_token(body.token) is None:
            raise HTTPException(404, "no such invite")
        await leases.tick()
        return {"ok": True, "erased": True}

    @app.post("/api/lease/token", status_code=201)
    async def lease_token(request: Request, p: Principal = Depends(reader)) -> dict:
        if p.effective_role != "tester" or p.kind != "session":
            raise HTTPException(403, "a lease token is issued only to a tester's dashboard session")
        require_lease(p)
        rate("write", p)
        try:
            tok = leases.issue_token(p.lease_id or "")
        except lease_mod.LeaseError as e:
            raise HTTPException(409 if e.code == "exists" else 403, str(e)) from None
        base = (settings.public_url or f"{request.url.scheme}://{request.url.netloc}").rstrip("/")
        return {**tok, "mcp_url": f"{base}/mcp"}

    async def _demo(op: str, p: Principal) -> dict:
        if is_replay:
            raise HTTPException(403, REPLAY_READ_ONLY)
        role = p.effective_role
        if not (p.is_operator or (role == "tester" and p.kind == "session")):
            raise HTTPException(403, "demo operations need an operator or a tester session")
        require_lease(p)
        rate("demo", p)
        if role == "tester" and not leases.use_demo_op(p.lease_id or "", op):
            info = leases.lease(p.lease_id) or {}
            wait = str(max(1, info.get("remaining_s") or 1))
            raise HTTPException(429, f"this lease has used its {leases.demo_ops} {op} operations",
                                headers={"Retry-After": wait})
        return await call(f"demo_{op}", p.caller())

    @app.post("/api/demo/break")
    async def demo_break(p: Principal = Depends(reader)):
        return await _demo("break", p)

    @app.post("/api/demo/seed")
    async def demo_seed(p: Principal = Depends(reader)):
        return await _demo("seed", p)

    # ---- operator: invites and leases ---------------------------------------------------
    @app.post("/api/admin/invites", status_code=201)
    async def admin_invite(body: InviteBody, p: Principal = Depends(operator)) -> dict:
        inv = leases.create_invite(body.note, p.name, ttl_s=body.ttl_hours * 3600 if body.ttl_hours else None)
        return {**inv, "link": invite_link(inv["token"]),
                "link_warning": None if settings.public_url else "SHIMPAKU_PUBLIC_URL is not set; no link built"}

    @app.get("/api/admin/invites")
    async def admin_invites(p: Principal = Depends(operator)) -> dict:
        return {"invites": leases.list_invites()}

    @app.delete("/api/admin/invites/{invite_id}")
    async def admin_invite_revoke(invite_id: str, p: Principal = Depends(operator)) -> dict:
        if not leases.revoke_invite(invite_id, p.name):
            raise HTTPException(404, "no such open invite")
        await leases.tick()
        return {"ok": True}

    @app.get("/api/admin/leases")
    async def admin_leases(p: Principal = Depends(operator)) -> dict:
        return {"leases": leases.leases()}

    @app.post("/api/admin/leases/{lease_id}/revoke")
    async def admin_lease_revoke(lease_id: str, p: Principal = Depends(operator)) -> dict:
        if not leases.request_end(lease_id, "operator"):
            raise HTTPException(404, "no such live lease")
        await leases.tick()
        return {"ok": True, "lease": leases.lease(lease_id)}

    @app.post("/api/admin/purge")
    async def admin_purge(p: Principal = Depends(operator)) -> dict:
        return leases.purge()

    # ---- session requests ------------------------------------------------------------------
    def _req_error(e: req_mod.RequestError) -> HTTPException:
        return HTTPException({"consent": 422, "bad_argument": 422, "full": 503, "not_found": 404,
                              "bad_state": 409}.get(e.code, 400), str(e))

    def _req_links(view_: dict, token: str | None) -> dict:
        return {**view_, "link": invite_link(token) if token else None,
                "ics_url": f"/api/invites/ics?i={token}" if token else None}

    @app.post("/api/session-requests", status_code=201)
    async def session_request_create(body: SessionRequestBody, request: Request) -> dict:
        check_csrf(request, None)
        if is_replay or mode != "local":
            raise HTTPException(403, "session requests are taken only by a lab in auth mode local")
        rate("request", f"ip:{client_ip(request)}")
        try:
            out = requests_.create(name=body.name, contact=body.contact, message=body.message,
                                   slots=[d.timestamp() for d in body.slots], consent=body.consent,
                                   max_pending=settings.session_requests_max_pending)
        except req_mod.RequestError as e:
            raise _req_error(e) from None
        return {"ok": True, "status_token": out["status_token"], "status_path": f"/request/status?s={out['status_token']}"}

    @app.get("/api/session-requests/status")
    async def session_request_status(request: Request, s: str = "") -> dict:
        """The status token is the capability; any unknown token is the same 404."""
        rate("read", f"ip:{client_ip(request)}")
        st = requests_.status(s) if not is_replay else None
        if st is None:
            raise HTTPException(404, "no such request")
        tok = st.pop("invite_token")
        return {**_req_links(st, tok), "retention_days": settings.retention_days}

    @app.post("/api/session-requests/erase")
    async def session_request_erase(body: EraseBody, request: Request) -> dict:
        """The requester erases their request with the status token (DPDP s.12 without a mailbox).
        Any unknown token is the same 404 as the status page."""
        check_csrf(request, None)
        if is_replay:
            raise HTTPException(403, REPLAY_READ_ONLY)
        rate("erase", f"ip:{client_ip(request)}")
        st = requests_.erase(body.token)
        if st is None:
            raise HTTPException(404, "no such request")
        await leases.tick()  # an ended lease: cancel its actions and re-seed now
        return {**_req_links(st, None), "retention_days": settings.retention_days}

    @app.get("/api/session-requests/notice")
    async def session_request_notice() -> dict:
        return {"retention_days": settings.retention_days, "contact": settings.privacy_contact,
                "lease_minutes": int(leases.lease_s // 60), "max_slots": req_mod.MAX_SLOTS,
                "open": mode == "local" and not is_replay}

    @app.get("/api/invites/ics")
    async def invite_ics(request: Request, i: str = "") -> Response:
        """Calendar file for a slot-bound invite. Never consumes the invite."""
        rate("read", f"ip:{client_ip(request)}")
        r = requests_.request_for_invite(i) if i and not is_replay else None
        if r is None or not r["slot_start"]:
            raise HTTPException(404, "no calendar entry for this link")
        return _ics_response(r["request_id"], requests_.ics_for_request(r["request_id"], invite_link(i)))

    def _ics_response(rid: str, body: str) -> Response:
        return Response(body, media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="shimpaku-{rid}.ics"',
                                 "Cache-Control": "no-store"})

    @app.get("/api/admin/session-requests")
    async def admin_requests(status: str | None = None, p: Principal = Depends(operator)) -> dict:
        return {"requests": requests_.list(status), "pending": requests_.pending_count(),
                "link_warning": None if settings.public_url else "SHIMPAKU_PUBLIC_URL is not set; no link built"}

    @app.get("/api/admin/session-requests/{rid}.ics")
    async def admin_request_ics(rid: str, p: Principal = Depends(operator)) -> Response:
        try:
            r = requests_.get(rid)
            tok = requests_._row(rid)["invite_token"]  # type: ignore[index]
            return _ics_response(rid, requests_.ics_for_request(rid, invite_link(tok) if tok else None))
        except req_mod.RequestError as e:
            raise _req_error(e) from None

    @app.get("/api/admin/session-requests/{rid}")
    async def admin_request(rid: str, p: Principal = Depends(operator)) -> dict:
        try:
            r = requests_.get(rid)
        except req_mod.RequestError as e:
            raise _req_error(e) from None
        tok = (requests_._row(rid) or {}).get("invite_token")
        return _req_links(r, tok)

    @app.post("/api/admin/session-requests/{rid}/approve")
    async def admin_request_approve(rid: str, body: ApproveBody, p: Principal = Depends(operator)) -> dict:
        rate("write", p)
        try:
            out = requests_.approve(rid, body.slot_start.timestamp(), p.name)
        except req_mod.RequestError as e:
            raise _req_error(e) from None
        tok = out.pop("invite_token")
        return {**_req_links(out, tok), "ics_url": f"/api/admin/session-requests/{rid}.ics",
                "link_warning": None if settings.public_url else "SHIMPAKU_PUBLIC_URL is not set; no link built"}

    @app.post("/api/admin/session-requests/{rid}/decline")
    async def admin_request_decline(rid: str, body: DeclineBody, p: Principal = Depends(operator)) -> dict:
        rate("write", p)
        try:
            out = requests_.decline(rid, body.note, p.name)
        except req_mod.RequestError as e:
            raise _req_error(e) from None
        await leases.tick()
        return out

    # ---- SSE -------------------------------------------------------------------
    async def _viewed(fn: Callable[[Any], Any], pending: Any) -> Any:
        return fn(await pending)

    def poller(request: Request, event: str, tick: float, fetch: Callable[[], Any]):
        async def gen():
            last = None
            while not await request.is_disconnected():
                try:
                    data = await fetch()
                except HTTPException as e:
                    yield {"event": "error", "data": json.dumps({"status": e.status_code, "detail": e.detail})}
                    data = None
                if data is not None:
                    blob = json.dumps(data, default=str)
                    if blob != last:
                        last = blob
                        yield {"event": event, "data": blob}
                await asyncio.sleep(tick)

        return EventSourceResponse(gen(), ping=15)

    @app.get("/api/stream/sle")
    async def stream_sle(request: Request, site: str = "campus", window: int = 60, p: Principal = Depends(reader)):
        return poller(request, "sle", SLE_TICK_S, lambda: _viewed(view.sle_summary, call("sle_get_summary", site, window_min=window)))

    @app.get("/api/stream/actions")
    async def stream_actions(request: Request, p: Principal = Depends(reader)):
        return poller(request, "actions", ACTIONS_TICK_S, lambda: _viewed(view.action_list, call("action_list")))

    @app.get("/api/stream/events")
    async def stream_events(request: Request, p: Principal = Depends(reader)):
        return poller(request, "incidents", EVENTS_TICK_S, lambda: _viewed(view.incident_list, call("incident_list")))

    from shimpaku.api import routes_viz  # /api/viz/*: Overview, Topology, Device pages (read-only)
    app.include_router(viz := routes_viz.router(call, reader))
    from shimpaku.api import routes_hardware; app.include_router(routes_hardware.router(call, reader, service, topology=viz.topology, hostmon=lambda: getattr(app.state, "hostmon", None)))  # noqa: E702  /api/hardware
    from shimpaku.api import routes_console; app.include_router(routes_console.router(call, reader, rate))  # noqa: E702
    from shimpaku.api import routes_ops; app.include_router(routes_ops.router(operator, app, is_demo))  # noqa: E702  /api/ops/*: operator-only
    from shimpaku.api import routes_public  # /api/probes/heatmap, /api/public/summary (landing)
    app.include_router(routes_public.router(call, reader))
    from shimpaku.api import routes_live  # /api/live/*: link traffic, event stream, time travel (read-only)
    app.include_router(routes_live.router(call, reader, service))
    from shimpaku.api import routes_explain; app.include_router(routes_explain.router(call, reader))  # noqa: E702
    from shimpaku.api import routes_theatre; app.include_router(routes_theatre.router(call, reader))  # noqa: E702  /api/theatre*
    from shimpaku.api import routes_virga; app.include_router(routes_virga.router(call, reader, rate))  # noqa: E702  /api/virga/*

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
    async def api_404(rest: str):
        raise HTTPException(404, "no such API route")

    # ---- dashboard -------------------------------------------------------------
    if (dist / "index.html").exists():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = (dist / path).resolve()
            if path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        async def no_build():
            return JSONResponse({"detail": "dashboard not built: run `npm run build` in web/"}, status_code=503)

    return app


def demo_enabled() -> bool:
    return os.environ.get("SHIMPAKU_DEMO", "") in ("1", "true", "yes")
