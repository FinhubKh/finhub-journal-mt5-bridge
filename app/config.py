from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bridge_service_token: str = ""
    # Same key as the journal (INVESTOR_CRED_ENCRYPTION_KEY) so workers can
    # load encrypted investor passwords from Supabase instead of the Redis queue.
    investor_cred_encryption_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "INVESTOR_CRED_ENCRYPTION_KEY",
            "INVESTOR_ENCRYPTION_KEY",
        ),
    )
    supabase_url: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_URL", "VITE_SUPABASE_URL"),
    )
    supabase_service_role_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_SERVICE_ROLE_KEY"),
    )
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_queue_key: str = "finhubkh:mt5:sync_jobs"
    mt5_terminal_path: str = ""
    # Optional JSON map (server prefix → terminal64.exe). See config/mt5_terminal_map.json.
    mt5_terminal_map_path: str = ""
    # Bound how long a single login attempt can hold the MT5 lock. MT5's own
    # default is 60s, which lets one bad/unreachable broker starve every other
    # queued verify/sync job behind the lock.
    mt5_init_timeout_ms: int = 15000
    # Default 2: one worker can write DB while another holds the MT5 lock
    worker_pool_size: int = 2
    history_lookback_days: int = 90
    bridge_api_host: str = "0.0.0.0"
    bridge_api_port: int = 8788
    mt5_lock_key: str = "finhubkh:mt5:terminal_lock"
    mt5_lock_ttl_seconds: int = 300
    mt5_lock_wait_seconds: int = 120
    worker_heartbeat_key: str = "finhubkh:mt5:worker_heartbeat"
    worker_heartbeat_ttl_seconds: int = 60
    processing_stale_seconds: int = 900


def get_settings() -> Settings:
    settings = Settings()
    if settings.supabase_url:
        object.__setattr__(settings, "supabase_url", settings.supabase_url.rstrip("/"))
    return settings
