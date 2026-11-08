from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deployment_mode: Literal["local", "cloud"] = "local"
    postgres_password: str = ""
    database_url: str = "sqlite:///./research.db"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "research"
    qdrant_url: str = ""
    elasticsearch_url: str = ""
    elasticsearch_api_key: str = ""
    app_secret: str = "development-only-secret"
    demo_email: str = "demo@example.com"
    demo_password: str = "change-me"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_provider: Literal["openai", "ollama"] = "openai"
    ollama_num_ctx: int = Field(default=16384, ge=4096, le=40960)
    agent_max_rounds: int = Field(default=8, ge=1, le=20)
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    github_token: str = ""
    openalex_api_key: str = ""
    semantic_scholar_api_key: str = ""
    citation_references_ttl_hours: int = Field(default=168, ge=1)
    citation_citations_ttl_hours: int = Field(default=24, ge=1)
    metrics_refresh_hours: int = Field(default=24, ge=1)
    code_retry_days: int = Field(default=7, ge=1)
    external_error_retry_hours: int = Field(default=1, ge=1)
    mineru_command: str = "mineru-kit"
    parser_backend: Literal["local", "remote"] = "local"
    parser_vector_enhance: bool = True
    parser_vector_max_pages: int = Field(default=50, ge=0)
    mineru_api_url: str = ""
    mineru_api_key: str = ""
    local_storage: str = "storage"
    debug_console_key: str = ""

    @model_validator(mode="after")
    def validate_cloud(self):
        if self.deployment_mode != "cloud":
            return self
        if self.llm_provider != "openai" or self.parser_backend != "remote":
            raise ValueError("Cloud mode requires LLM_PROVIDER=openai and PARSER_BACKEND=remote")
        for name in ("llm_base_url", "embedding_base_url", "mineru_api_url"):
            value = getattr(self, name)
            parsed = urlparse(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.hostname in ("localhost", "127.0.0.1", "host.docker.internal")
            ):
                raise ValueError(f"Cloud mode requires an external HTTPS {name}")
        if (
            self.app_secret in ("development-only-secret", "change-this-before-exposing-service")
            or self.demo_password == "change-me"
            or self.postgres_password in ("", "change-me")
            or self.s3_secret_key in ("", "change-me")
        ):
            raise ValueError("Cloud mode requires non-default application and storage secrets")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
