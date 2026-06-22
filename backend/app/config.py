"""Runtime settings (env-overridable with the RACKIQ_ prefix)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RACKIQ_")

    db_path: str | None = None
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
