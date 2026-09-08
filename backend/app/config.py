"""Application configuration.

DATA_DIR is resolved exactly once, here, against the repository root so that the
API process, the worker task and any CLI script agree on a single location.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    data_dir: str = "./runtime-data"

    extraction_provider: str = "rules"
    allow_rules_fallback: bool = False

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    max_upload_mb: int = 10
    max_pdf_pages: int = 10
    max_queue_depth: int = 10
    ocr_timeout_seconds: int = 30
    llm_timeout_seconds: int = 60
    ocr_dpi: int = 220
    ocr_max_megapixels: float = 40.0

    port: int = 8000

    demo_username: str = ""
    demo_password: str = ""

    # Comma-separated list of origins permitted to perform browser mutations.
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Mutations per minute per client address. A brake on runaway scripts, not a
    # production quota: a reviewer working through a batch, or the scenario
    # script, comfortably exceeds a tighter limit. Set to 0 to disable.
    rate_limit_per_minute: int = 60

    policy_version: str = "v1"
    fixture_reference_date: str = "2026-09-01"

    static_dir: str = ""

    @field_validator("extraction_provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        allowed = {"rules", "ollama", "openai_compatible"}
        if value not in allowed:
            raise ValueError(f"EXTRACTION_PROVIDER must be one of {sorted(allowed)}")
        return value

    @property
    def resolved_data_dir(self) -> Path:
        raw = Path(os.path.expanduser(self.data_dir))
        return raw if raw.is_absolute() else (PROJECT_ROOT / raw).resolve()

    @property
    def uploads_dir(self) -> Path:
        return self.resolved_data_dir / "uploads"

    @property
    def tmp_dir(self) -> Path:
        return self.resolved_data_dir / "tmp"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{(self.resolved_data_dir / 'clearledger.db').as_posix()}"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def auth_enabled(self) -> bool:
        return bool(self.demo_username and self.demo_password)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def origin_allowlist(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def resolved_static_dir(self) -> Path | None:
        if not self.static_dir:
            return None
        raw = Path(self.static_dir)
        resolved = raw if raw.is_absolute() else (PROJECT_ROOT / raw).resolve()
        return resolved if resolved.is_dir() else None

    def ensure_directories(self) -> None:
        for path in (self.resolved_data_dir, self.uploads_dir, self.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)
        probe = self.resolved_data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
