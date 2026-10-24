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
- The session cookie is ``__Host-shimpaku_session`` (Secure) only over https; over plain http it is
  ``shimpaku_session`` without Secure, and ``/api/settings`` reports that so the UI can warn.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from shimpaku.api import view
from shimpaku.api import lease as lease_mod
from shimpaku.api.auth import ADMIN_USER, OFF_MODE_USER, SCOPES, AuthStore, Principal, load_admin_hash, verify_password
from shimpaku.api.llm_keys import LLMKeyError
from shimpaku.api.ratelimit import RateLimiter
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


class RedeemBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    consent: bool = False
    display_name: str | None = Field(default=None, max_length=60)


def invite_link(token: str) -> str | None:
    """Built only from the configured SHIMPAKU_PUBLIC_URL, never from a request's Host header."""
    base = (settings.public_url or "").rstrip("/")
    return f"{base}/start?i={token}" if base else None


NO_REFERRER_PATHS = ("/start", "/api/invites/")


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    context: dict[str, Any] | None = None


def _cookie_names(secure: bool) -> tuple[str, str]:
    return ("__Host-shimpaku_session", "__Host-shimpaku_csrf") if secure else ("shimpaku_session", "shimpaku_csrf")


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https"


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
    dist = web_dist or WEB_DIST

    app = FastAPI(title="Shimpaku", version="0.0.1", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.auth = auth
    app.state.service = service
    app.state.store = store
    app.state.auth_mode = mode
    app.state.leases = leases
    app.state.limiter = limiter

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
        no_ref = request.url.path == "/start" or request.url.path.startswith(NO_REFERRER_PATHS[1])
        resp.headers["Referrer-Policy"] = "no-referrer" if no_ref else resp.headers.get("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
        return resp

    # ---- principals ------------------------------------------------------------
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
                return None if mode != "off" else Principal("session", OFF_MODE_USER, "admin")
            return Principal("session", rec["user"], "tester", role="tester", lease_id=rec["lease_id"])
        if mode == "off":
            return Principal("session", OFF_MODE_USER, "admin")
        return Principal("session", rec["user"], "admin") if rec else None

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

    def reader(request: Request) -> Principal:
        p = principal_or_none(request)
        if p is None:
            raise HTTPException(401, "sign in required")
        check_csrf(request, p)
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
        }

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request, response: Response) -> dict:
        check_csrf(request, None)
        if mode != "local":
            raise HTTPException(400, f"auth mode is {mode}; there is nothing to sign in to")
        client = request.client.host if request.client else "?"
        if not auth.login_allowed(client):
            raise HTTPException(429, "too many sign-in attempts; wait a minute")
        if body.username != ADMIN_USER or not verify_password(load_admin_hash(settings.data), body.password):
            raise HTTPException(401, "wrong user name or password")
        secure = _is_https(request)
        sname, _ = _cookie_names(secure)
        auth.drop_session(request.cookies.get(sname))  # rotate at login
        sid = auth.create_session(ADMIN_USER)
        response.set_cookie(sname, sid, httponly=True, secure=secure, samesite="lax", path="/")
        set_csrf(request, response, rotate=True)
        log.info("login ok user=%s", ADMIN_USER)
        return {"ok": True, "user": ADMIN_USER}

    @app.post("/api/logout")
    async def logout(request: Request, response: Response) -> dict:
        check_csrf(request, None)
        secure = _is_https(request)
        sname, _ = _cookie_names(secure)
        auth.drop_session(request.cookies.get(sname))
        response.delete_cookie(sname, path="/", secure=secure, httponly=True, samesite="lax")
        return {"ok": True}

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
        return view.inventory(await call("inventory_list", site=site))

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

    @app.post("/api/assistant/ask")
    async def ask(body: AskBody, request: Request, p: Principal = Depends(reader)):
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
            if key is None:
                raise HTTPException(503, "Ask Shim is not enabled for testers on this server "
                                         "(no LLM admin key is configured)")
            extra["llm_key"] = key
        rate("ask", p)
        params = inspect.signature(ask_fn).parameters
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
            status = {"consent": 422, "invalid": 404}.get(e.code, 410)
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
        assistant_ok = bool(leases.keys and leases.keys.enabled and ask_fn is not None
                            and "llm_key" in inspect.signature(ask_fn).parameters)
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
