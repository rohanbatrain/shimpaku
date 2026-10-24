"""Run the API alone: ``SHIMPAKU_DEMO=1 python -m shimpaku.api`` for dashboard development, or
``SHIMPAKU_REPLAY=snapshot.json python -m shimpaku.api`` for the public read-only replay.

The daemon (``shimpakud``) normally builds the app with the real service. Without
``SHIMPAKU_DEMO=1`` this imports ``shimpaku.service``.
"""

from __future__ import annotations

import ipaddress
import os

import uvicorn

from shimpaku.api.app import create_app, demo_enabled
from shimpaku.config import settings


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def main() -> None:
    # auth_mode=off serves every caller as the operator: loopback only, as the MCP endpoint
    if settings.auth_mode.lower() == "off" and not _loopback(settings.bind):
        raise RuntimeError(f"SHIMPAKU_AUTH_MODE=off refuses a non-loopback bind ({settings.bind}); "
                           "use 127.0.0.1 or set SHIMPAKU_AUTH_MODE=local")
    if os.environ.get("SHIMPAKU_REPLAY"):
        from shimpaku.api import replay as service
        service.load()  # fail at start, not on the first request
    elif demo_enabled():
        from shimpaku.api import demo as service
    else:
        from shimpaku import service  # type: ignore[no-redef]
    statedb = None if demo_enabled() or os.environ.get("SHIMPAKU_REPLAY") else settings.ensure_data() / "state.db"
    app = create_app(service, None, statedb)
    port = int(os.environ.get("SHIMPAKU_PORT", settings.port))
    # behind the reverse proxy: trust its X-Forwarded-Proto so cookies get the Secure/__Host- form
    uvicorn.run(app, host=settings.bind, port=port, log_level="info",
                proxy_headers=True, forwarded_allow_ips=os.environ.get("SHIMPAKU_TRUSTED_PROXIES", "127.0.0.1"))


if __name__ == "__main__":
    main()
