"""Settings from the environment — the only place configuration is read (see CONTRACT.md)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHIMPAKU_", extra="ignore")

    data: Path = Path.home() / ".local/share/shimpaku"
    lock_db: Path = Path("/home/lab/shimpaku/locks.db")
    lab_repo: Path = Path.home() / "juniper-7-day-sprint"
    topologies: str = "day-02-switching.toml,day-06-datacenter.toml"
    device_user: str = "admin"
    device_password: str | None = Field(default=None, repr=False)  # required: SHIMPAKU_DEVICE_PASSWORD
    gnmic: Path = Path.home() / ".local/bin/aiops-gnmic"
    gnmi_port: int = 32767
    bind: str = "127.0.0.1"
    port: int = 8780
    auth_mode: str = "off"  # off | local
    llm_base: str = "http://127.0.0.1:4000"
    llm_key: str | None = Field(default=None, repr=False)
    # Measured 2026-10-24 against the gateway (one tool-calling request each): lightning 10.1 s,
    # ultra 11.5 s; glm, glm-flash, nemotron-super and gpt-oss timed out at 120 s; the Haiku free
    # tier is phased out. Override per deployment with SHIMPAKU_LLM_MODELS.
    llm_models: str = "triage=nvidia-nemotron-lightning,diagnose=nvidia-nemotron-ultra,escalate=nvidia-nemotron-ultra"

    # ---- guest testers (docs/DEPLOY-DESIGN.md build steps 4-5) ----
    public_url: str | None = None          # fixed base for invite links; never taken from a Host header
    invite_ttl_hours: float = 72.0         # time to redeem an invite
    lease_minutes: float = 30.0            # one tester lease
    lease_max_active: int = 1              # concurrent leases; the rest queue (FIFO)
    lease_demo_ops: int = 5                # per lease, each of break and seed
    retention_days: int = 90               # tester identity data kept after lease end
    privacy_contact: str | None = None     # optional; erasure is self-service, this adds a postal/e-mail route to the notices
    # Anonymous visitors get live read-only access in auth_mode=local (role visitor). Off by default
    # so a lab without the edge in front of it stays closed.
    public_view: bool = False
    # Origins (scheme://host) allowed to read GET /api/public/* cross-origin: the landing page's
    # hosts, e.g. "https://shimpaku.example". Comma-separated; empty = no CORS header at all.
    public_cors_origins: str = ""
    # Operator sessions while public view is on (the owner may sign in from someone else's laptop):
    # sliding idle timeout in minutes and absolute lifetime in hours. Off public view: 12 h / 7 d.
    operator_idle_min: float = 30.0
    operator_max_h: float = 4.0
    session_requests_max_pending: int = 200  # open requests stored at once; more are refused (503)
    # LiteLLM admin/master key used only to mint and delete per-lease virtual keys. Unset: Ask Shim
    # is disabled for testers (never falls back to the operator's key).
    llm_admin_key: str | None = Field(default=None, repr=False)
    tester_llm_budget_usd: float = 0.50
    tester_llm_rpm: int = 10
    tester_llm_models: str = ""            # comma list; empty = the models in llm_models
    # per-principal/global requests per minute: category=per_principal/global
    # read (anonymous visitors, per address) and request (session requests, per address) were added
    # with public view; categories missing from an override keep these defaults.
    # lab host health (shimpaku.hostmon): sample period, and the thermal guard (warn + event at
    # >= 97 C for 60 s; never stops VMs). Off by default.
    host_sample_s: float = 15.0
    thermal_guard: bool = False
    rate_limits: str = "write=30/300,demo=6/30,ask=10/60,mcp=60/600,redeem=10/60,read=120/1200,request=3/20,erase=10/100"

    def require_device_password(self) -> str:
        """Device credentials are never defaulted in code (see .env.example)."""
        if not self.device_password:
            raise SystemExit("SHIMPAKU_DEVICE_PASSWORD is not set — see .env.example")
        return self.device_password

    def llm_key_value(self) -> str | None:
        if self.llm_key:
            return self.llm_key
        f = Path.home() / ".config/shimpaku/litellm.key"
        return f.read_text().strip() if f.exists() else None

    def models(self) -> dict[str, str]:
        return dict(kv.split("=", 1) for kv in self.llm_models.split(",") if "=" in kv)

    def tester_models(self) -> list[str]:
        if self.tester_llm_models.strip():
            return [m.strip() for m in self.tester_llm_models.split(",") if m.strip()]
        return sorted(set(self.models().values()))

    def rate_limit_table(self) -> dict[str, tuple[int, int]]:
        out = dict(DEFAULT_RATE_LIMITS)
        for kv in self.rate_limits.split(","):
            if "=" in kv and "/" in kv:
                k, v = kv.split("=", 1)
                a, b = v.split("/", 1)
                out[k.strip()] = (int(a), int(b))
        return out

    def topology_paths(self) -> list[Path]:
        return [self.lab_repo / "lab/topologies" / t.strip() for t in self.topologies.split(",") if t.strip()]

    def ensure_data(self) -> Path:
        self.data.mkdir(parents=True, exist_ok=True)
        return self.data

    @property
    def duckdb_path(self) -> Path:
        return self.data / "aiops.duckdb"

    @property
    def sqlite_path(self) -> Path:
        return self.data / "state.db"


DEFAULT_RATE_LIMITS = {k: (int(v.split("/")[0]), int(v.split("/")[1])) for k, v in
                       (kv.split("=") for kv in Settings.model_fields["rate_limits"].default.split(","))}

settings = Settings()
