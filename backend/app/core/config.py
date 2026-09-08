"""Application configuration — one codebase, two profiles (doc 01 §5).

`APP_PROFILE=full|demo` selects the deployment profile. All profile-dependent
limits live here so modules never branch on the environment directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Profile = Literal["full", "demo"]
ENGINE_VERSION = "0.1.0"
IR_SCHEMA_VERSION = "1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    app_profile: Profile = Field(default="full", alias="APP_PROFILE")
    app_name: str = "ThesisGuard"
    debug: bool = False

    # Upload / resource guardrails (doc 02 §6). Demo overrides below.
    max_upload_mb: int = 50
    max_pages: int = 500
    max_words: int = 200_000
    parse_timeout_s: int = 120
    total_timeout_s: int = 1200
    external_queries_per_doc: int = 200

    # Container / archive guardrails (doc 02 §3, format-specific hardening)
    max_zip_members: int = 2_000
    max_uncompressed_mb: int = 500
    max_compression_ratio: int = 100
    max_latex_files: int = 200
    max_latex_include_depth: int = 8
    parse_memory_mb: int = 2_048
    parse_sandboxed: bool = True

    # Retention (doc 02 §4)
    blob_retention_hours: int = 24
    report_retention_days: int = 30

    # Storage (overridden by docker-compose / Space env)
    database_url: str = "postgresql+psycopg://thesis:thesis@localhost:5432/thesisguard"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"

    # External services
    languagetool_url: str = "http://localhost:8010"

    def apply_profile_defaults(self) -> Settings:
        """Apply demo-profile caps (doc 04 §6) when APP_PROFILE=demo."""
        if self.app_profile == "demo":
            self.max_upload_mb = 15
            self.max_pages = 60
            self.max_words = 30_000
            self.total_timeout_s = 300
            self.external_queries_per_doc = 50
            self.blob_retention_hours = 0  # deleted immediately after report
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings().apply_profile_defaults()
