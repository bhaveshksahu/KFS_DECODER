"""Application configuration — all settings come from environment variables."""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Reads configuration from environment variables (or a .env file locally)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Gemini / Vertex AI
    gemini_model: str = "gemini-2.5-flash"
    gemini_backend: str = "developer"   # "developer" | "vertex"
    gemini_api_key: str = ""            # required when gemini_backend = "developer"
    gcp_project: str = ""
    location: str = "us-central1"
    bucket: str = ""

    # Storage
    storage_backend: str = "local"      # "local" | "gcp"
    upload_dir: str = "/tmp/kfs_uploads"

    # API
    cors_origins: str = "*"             # comma-separated; "*" for dev
    demo_mode: bool = False             # force cached responses globally

    # Server
    port: int = int(os.environ.get("PORT", "8080"))
    debug: bool = False

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse the comma-separated CORS_ORIGINS env var."""
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
