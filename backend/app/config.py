"""Application configuration.

All configuration — including every credential — is read from server-side
environment variables (or a local ``.env`` file in development). Nothing in
this module is ever sent to the frontend.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core -------------------------------------------------------------
    app_name: str = "ResearchLab"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://researchlab:researchlab@localhost:5432/researchlab"

    # --- Security ---------------------------------------------------------
    secret_key: str = Field(default="change-me-in-production-please-32+chars")
    access_token_minutes: int = 60 * 12
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    # Single-researcher mode: registration is only possible while no user exists,
    # unless explicitly enabled.
    allow_registration: bool = False
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_minute: int = 240
    login_rate_limit_per_minute: int = 10

    # --- Uploads & storage ------------------------------------------------
    max_upload_mb: int = 40
    storage_backend: Literal["local", "supabase"] = "local"
    local_storage_path: str = "./storage_data"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "researchlab"

    # --- AI inference (provider abstraction) ------------------------------
    # Comma-separated priority list. The first available provider is used and
    # the rest act as fallbacks when a quota or outage error occurs.
    llm_providers: str = "gemini,ollama"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    openai_compat_base_url: str = ""  # e.g. Groq, OpenRouter, LM Studio, vLLM
    openai_compat_api_key: str = ""
    openai_compat_model: str = ""
    llm_timeout_seconds: int = 180
    llm_max_retries: int = 3

    # --- Embeddings ---------------------------------------------------------
    # hashing = dependency-free local lexical embedding (always available)
    # sentence_transformers = local semantic model (optional install)
    # ollama / gemini = API embeddings
    embedding_provider: Literal["hashing", "sentence_transformers", "ollama", "gemini"] = "hashing"
    embedding_dim: int = 768
    sentence_transformer_model: str = "sentence-transformers/all-mpnet-base-v2"
    ollama_embedding_model: str = "nomic-embed-text"
    gemini_embedding_model: str = "gemini-embedding-001"

    # --- Scholarly sources --------------------------------------------------
    scholarly_contact_email: str = ""  # OpenAlex/Crossref "polite pool" (optional)
    openalex_api_key: str = ""
    semantic_scholar_api_key: str = ""
    core_api_key: str = ""
    scholarly_timeout_seconds: int = 30

    # --- Background processing --------------------------------------------
    run_embedded_worker: bool = True
    worker_poll_seconds: float = 2.0
    job_lease_seconds: int = 300
    watch_check_minutes: int = 60

    # --- Analysis sandbox ---------------------------------------------------
    analysis_timeout_seconds: int = 120
    analysis_memory_mb: int = 1024

    @field_validator("secret_key")
    @classmethod
    def _secret_len(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_provider_list(self) -> list[str]:
        return [p.strip() for p in self.llm_providers.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
