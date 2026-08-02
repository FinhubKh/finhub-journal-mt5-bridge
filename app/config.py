from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bridge_service_token: str = ""
    journal_bridge_sync_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_queue_key: str = "finhubkh:mt5:sync_jobs"
    mt5_terminal_path: str = ""
    # Default 2: one worker can post to journal while another holds the MT5 lock
    worker_pool_size: int = 2
    history_lookback_days: int = 90
    bridge_api_host: str = "0.0.0.0"
    bridge_api_port: int = 8788
    mt5_lock_key: str = "finhubkh:mt5:terminal_lock"
    mt5_lock_ttl_seconds: int = 300
    mt5_lock_wait_seconds: int = 120


def get_settings() -> Settings:
    return Settings()
