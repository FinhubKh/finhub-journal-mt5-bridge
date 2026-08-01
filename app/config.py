from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bridge_service_token: str = ""
    journal_bridge_sync_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_queue_key: str = "finhubkh:mt5:sync_jobs"
    mt5_terminal_path: str = ""
    worker_pool_size: int = 3
    history_lookback_days: int = 90
    bridge_api_host: str = "0.0.0.0"
    bridge_api_port: int = 8788


def get_settings() -> Settings:
    return Settings()
