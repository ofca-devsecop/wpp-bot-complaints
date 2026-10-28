from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+psycopg://sicar:sicar@localhost:5432/sicar"
    neonize_session_db: str = "./data/neonize.db"
    whatsapp_max_media_bytes: int = 10 * 1024 * 1024
    merge_window_seconds: float = 3.0
    evidence_dir: str = "./data/evidence"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_vision_model: str = "gemini-3.5-flash"
    gemini_vision_timeout_s: float = 20.0

    sicar_api_token: str = ""
    sicar_allowed_ips: str = ""

    retention_days: int = 0
    timezone: str = "America/Argentina/Buenos_Aires"


@lru_cache
def get_settings() -> Settings:
    return Settings()
