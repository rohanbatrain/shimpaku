"""Run the API alone for dashboard development: ``SHIMPAKU_DEMO=1 python -m shimpaku.api``.

The daemon (``shimpakud``) normally builds the app with the real service. Without
``SHIMPAKU_DEMO=1`` this imports ``shimpaku.service``.
"""

from __future__ import annotations

import os

import uvicorn

from shimpaku.api.app import create_app, demo_enabled
from shimpaku.config import settings


def main() -> None:
    if demo_enabled():
        from shimpaku.api import demo as service
    else:
        from shimpaku import service  # type: ignore[no-redef]
    statedb = None if demo_enabled() else settings.ensure_data() / "state.db"
    app = create_app(service, None, statedb)
    port = int(os.environ.get("SHIMPAKU_PORT", settings.port))
    uvicorn.run(app, host=settings.bind, port=port, log_level="info")


if __name__ == "__main__":
    main()
