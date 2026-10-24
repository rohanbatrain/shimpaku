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
    device_password: str = Field(default="admin@123", repr=False)
    gnmic: Path = Path.home() / ".local/bin/aiops-gnmic"
    gnmi_port: int = 32767
    bind: str = "127.0.0.1"
    port: int = 8780
    auth_mode: str = "off"  # off | local
    llm_base: str = "http://127.0.0.1:4000"
    llm_key: str | None = Field(default=None, repr=False)
    llm_models: str = "triage=nvidia-glm-flash,diagnose=tokenharbor-claude-haiku-5.5-free,escalate=nvidia-glm"

    def llm_key_value(self) -> str | None:
        if self.llm_key:
            return self.llm_key
        f = Path.home() / ".config/shimpaku/litellm.key"
        return f.read_text().strip() if f.exists() else None

    def models(self) -> dict[str, str]:
        return dict(kv.split("=", 1) for kv in self.llm_models.split(",") if "=" in kv)

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


settings = Settings()
