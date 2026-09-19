from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "LLM Serving Backend"
    backend_api_key: str = "change-me-backend-key"
    admin_api_key: str = "change-me-admin-key"

    database_url: str = "postgresql+asyncpg://llm:llm@db:5432/llm"

    llama_base_url: str = "http://host.docker.internal:8080/v1"
    llama_api_key: str = "sk-no-key-required"
    model_alias: str = "bonsai-2-27b"
    upstream_model: str = ""
    request_timeout_seconds: float = 600.0

    default_user_id: str = "dad"
    allowed_user_ids: str = ""
    allowed_roles: str = "user,admin"

    system_prompt_path: Path = Path("config/system_prompt.txt")
    memory_top_k: int = 4
    rag_top_k: int = 4
    recent_message_limit: int = 10
    summary_trigger_messages: int = 20
    summary_keep_recent: int = 8
    max_context_item_chars: int = 1600

    embeddings_enabled: bool = True
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_device: str = "cpu"

    allowed_tool_names: str = ""

    log_level: str = "INFO"

    @property
    def allowed_user_id_set(self) -> set[str]:
        return {item.strip() for item in self.allowed_user_ids.split(",") if item.strip()}

    @property
    def allowed_role_set(self) -> set[str]:
        return {item.strip().lower() for item in self.allowed_roles.split(",") if item.strip()}

    @property
    def allowed_tool_name_set(self) -> set[str]:
        return {item.strip() for item in self.allowed_tool_names.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
