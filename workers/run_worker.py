import httpx

from app.config import get_settings
from jobqueue.redis_client import make_redis
from jobqueue.redis_queue import claim_job, enqueue_job
from workers.mt5_worker import run_sync_job


def _http_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


def main() -> None:
    from workers.mt5_adapter import MetaTrader5Adapter
    from workers.supervisor import mark_requeue, should_requeue

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")

    client = make_redis(settings.redis_url)
    mt5 = MetaTrader5Adapter()

    with _http_client() as http:
        while True:
            job = claim_job(client, settings.redis_queue_key, timeout=5)
            if not job:
                continue
            try:
                result = run_sync_job(
                    job=job,
                    mt5=mt5,
                    http=http,
                    supabase_url=settings.supabase_url,
                    service_key=settings.supabase_service_role_key,
                    terminal_path=settings.mt5_terminal_path,
                    lookback_days=settings.history_lookback_days,
                    redis_client=client,
                    lock_key=settings.mt5_lock_key,
                    lock_ttl_seconds=settings.mt5_lock_ttl_seconds,
                    lock_wait_seconds=settings.mt5_lock_wait_seconds,
                )
                if result.get("error") == "mt5_lock_timeout":
                    # Do not burn the retry budget — another worker was using MT5
                    enqueue_job(client, settings.redis_queue_key, job)
            except Exception:
                if should_requeue(job):
                    enqueue_job(client, settings.redis_queue_key, mark_requeue(job))


if __name__ == "__main__":
    main()
