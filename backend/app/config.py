from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    supabase_url: str
    supabase_service_key: str
    supabase_db_url: str  # asyncpg DSN for direct pgmq access

    # HS256 secret from Supabase: Project Settings → API → JWT Secret
    jwt_secret: str

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5vl:7b"

    queue_name: str = "invoice_extraction"
    worker_poll_interval_seconds: float = 2.0
    worker_visibility_timeout_seconds: int = 120


settings = Settings()
