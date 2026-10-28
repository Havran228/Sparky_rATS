"""Environment and role configuration. Model names live only here (ADR-002)."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SPARKY_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Path("/data")
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    mcp_port: int = 8001
    enable_mcp_server: bool = True
    enable_watcher: bool = True

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_timeout_seconds: float = 120.0
    ollama_health_timeout_seconds: float = 2.0

    # ADR-002 candidates. scripts/benchmark_models.sh measures them on the Spark
    # and records the result in docs/benchmarks.md. These stay the defaults until
    # that file shows a run and the defaults are edited on purpose. Env overrides win.
    model_fast: str = "qwen3:30b-a3b"
    model_heavy: str = "gpt-oss:120b"
    model_vision: str = "qwen3-vl:8b"
    model_embed: str = "bge-m3"

    keep_alive_fast: str = "10m"
    keep_alive_heavy: str = "20m"
    keep_alive_vision: str = "5m"
    keep_alive_embed: str = "30m"

    api_token: str | None = None
    mcp_token: str | None = None
    master_key: str | None = None
    typst_bin: str = "typst"
    pdftotext_bin: str = "pdftotext"
    font_dir: str | None = None
    letter_timezone: str = "Europe/Bratislava"
    google_redirect_uri: str = "http://127.0.0.1:3000/settings/google/callback"
    google_lookback_days: int = 90
    chat_tool_rounds: int = 4
    tailscale_proxy_hosts: str = "127.0.0.1,::1"
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"

    general_job_concurrency: int = 4
    chunk_target_tokens: int = 400
    vision_min_chars: int = 40
    retrieval_top_k: int = 8
    rrf_k: int = 60
    score_weight_mandatory: float = 0.6
    score_weight_preferred: float = 0.3
    score_weight_offerings: float = 0.1
    inbox_debounce_seconds: float = 1.0
    inbox_bundle_name: str = "Inbox"
    backup_retention_days: int = 14

    @field_validator("api_token", "mcp_token", "master_key", "font_dir", mode="before")
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sparky_rats.db"

    @property
    def qdrant_path(self) -> Path:
        return self.data_dir / "qdrant"

    @property
    def credentials_dir(self) -> Path:
        return self.data_dir / "credentials"

    @property
    def master_key_path(self) -> Path:
        return self.credentials_dir / "master.key"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def bundles_dir(self) -> Path:
        return self.data_dir / "bundles"

    @property
    def inbox_staging_dir(self) -> Path:
        return self.bundles_dir / "_inbox"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def score_weights(self) -> dict[str, float]:
        return {
            "mandatory": self.score_weight_mandatory,
            "preferred": self.score_weight_preferred,
            "offerings": self.score_weight_offerings,
        }

    def proxy_hosts(self) -> set[str]:
        return {host.strip() for host in self.tailscale_proxy_hosts.split(",") if host.strip()}

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


DATA_SUBDIRS: tuple[str, ...] = (
    "qdrant",
    "ollama",
    "bundles",
    "bundles/_inbox",
    "bundles/_raw",
    "inbox",
    "exports",
    "credentials",
    "backups",
)


def ensure_data_dirs(settings: Settings) -> None:
    for relative in DATA_SUBDIRS:
        (settings.data_dir / relative).mkdir(parents=True, exist_ok=True)
