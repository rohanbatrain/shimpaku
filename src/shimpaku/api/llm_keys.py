"""Per-lease LiteLLM virtual keys (DEPLOY-DESIGN gap 6: Shim's spend is bounded per lease).

Minted at ``POST {llm_base}/key/generate`` with the admin key (``SHIMPAKU_LLM_ADMIN_KEY``) and
deleted at ``POST {llm_base}/key/delete``. Fields, from docs.litellm.ai/docs/proxy/virtual_keys
(retrieved 2026-10-24): ``max_budget`` (USD), ``duration`` (key expiry), ``rpm_limit``,
``models``, ``key_alias``; the response's ``key`` is the ``sk-...`` value. ``/key/delete`` takes
``keys`` and/or ``key_aliases`` (docs.litellm.ai/docs/proxy/management_cli, same day).

The key value lives only in this process's memory, never on disk and never in a log or an
exception message; after a restart the lease's key is deleted by its alias and a new one minted.
Without an admin key the class is disabled: testers get no assistant, and nothing ever falls back
to the operator's own gateway key.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("shimpaku.llm_keys")


class LLMKeyError(RuntimeError):
    """Gateway refused or was unreachable. The message never contains a key."""


def alias_for(lease_id: str) -> str:
    return f"shimpaku-lease-{lease_id}"


class LiteLLMKeys:
    def __init__(self, base: str, admin_key: str | None, *, budget_usd: float, rpm: int, models: list[str],
                 transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 15.0):
        self.base = base.rstrip("/")
        self._admin = admin_key or None
        self.budget_usd = budget_usd
        self.rpm = rpm
        self.models = list(models)
        self._transport = transport
        self.timeout_s = timeout_s

    def __repr__(self) -> str:
        return f"LiteLLMKeys(base={self.base!r}, enabled={self.enabled})"

    @property
    def enabled(self) -> bool:
        return self._admin is not None

    async def _post(self, path: str, body: dict) -> dict:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self.timeout_s) as c:
                r = await c.post(self.base + path, json=body, headers={"Authorization": f"Bearer {self._admin}"})
        except httpx.HTTPError as e:
            raise LLMKeyError(f"{path}: {type(e).__name__}") from None
        if r.status_code >= 400:
            raise LLMKeyError(f"{path}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            return {}

    async def generate(self, lease_id: str, seconds: float) -> str:
        if not self.enabled:
            raise LLMKeyError("no admin key configured")
        out = await self._post("/key/generate", {
            "key_alias": alias_for(lease_id), "max_budget": self.budget_usd,
            "duration": f"{max(60, int(seconds))}s", "rpm_limit": self.rpm, "models": self.models,
            "metadata": {"shimpaku_lease": lease_id}})
        key = out.get("key")
        if not isinstance(key, str) or not key:
            raise LLMKeyError("/key/generate: no key in the response")
        log.info("minted LLM key for lease %s (alias %s)", lease_id, alias_for(lease_id))
        return key

    async def delete(self, lease_id: str, key: str | None = None) -> None:
        if not self.enabled:
            return
        body: dict = {"keys": [key]} if key else {"key_aliases": [alias_for(lease_id)]}
        await self._post("/key/delete", body)
        log.info("deleted LLM key for lease %s", lease_id)
