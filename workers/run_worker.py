import os

import httpx

from app.config import get_settings
from jobqueue.redis_client import make_redis
from jobqueue.redis_queue import claim_job, enqueue_job
from workers.logging_setup import get_logger
from workers.mt5_worker import run_sync_job, run_verify_job


def _http_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


def main() -> None:
    from workers.mt5_adapter import MetaTrader5Adapter
    from workers.supervisor import mark_requeue, should_requeue

    worker_id = os.environ.get("WORKER_ID", "0")
    log = get_logger(f"worker-{worker_id}")

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")

    client = make_redis(settings.redis_url)
    mt5 = MetaTrader5Adapter()
    log.info("Worker %s started (pid=%s)", worker_id, os.getpid())

    with _http_client() as http:
        while True:
            job = claim_job(client, settings.redis_queue_key, timeout=5)
            if not job:
                continue
            job_type = str(job.get("job_type") or "sync")
            account_id = job.get("trading_account_id")
            log.info("Claimed %s job account=%s job_id=%s", job_type, account_id, job.get("job_id"))
            try:
                if job_type == "verify":
                    # Don't leave Connect spinning for up to 2 minutes if MT5 is busy.
                    result = run_verify_job(
                        job=job,
                        mt5=mt5,
                        redis_client=client,
                        terminal_path=settings.mt5_terminal_path,
                        queue_key=settings.redis_queue_key,
                        lock_key=settings.mt5_lock_key,
                        lock_ttl_seconds=settings.mt5_lock_ttl_seconds,
                        lock_wait_seconds=min(20, settings.mt5_lock_wait_seconds),
                        init_timeout_ms=settings.mt5_init_timeout_ms,
                    )
                    if result.get("ok"):
                        # Queue trade pull after login works — don't make Connect wait on it.
                        sync_job = dict(job)
                        sync_job.pop("job_id", None)
                        sync_job["job_type"] = "sync"
                        enqueue_job(client, settings.redis_queue_key, sync_job)
                else:
                    result = run_sync_job(
                        job=job,
                        mt5=mt5,
                        http=http,
                        supabase_url=settings.supabase_url,
                        service_key=settings.supabase_service_role_key,
                        terminal_path=settings.mt5_terminal_path,
                        lookback_days=settings.history_lookback_days,
                        redis_client=client,
                        queue_key=settings.redis_queue_key,
                        lock_key=settings.mt5_lock_key,
                        lock_ttl_seconds=settings.mt5_lock_ttl_seconds,
                        lock_wait_seconds=settings.mt5_lock_wait_seconds,
                        init_timeout_ms=settings.mt5_init_timeout_ms,
                    )
                if result.get("ok"):
                    log.info("%s job succeeded account=%s result=%s", job_type, account_id, result)
                else:
                    log.warning("%s job failed account=%s error=%s", job_type, account_id, result.get("error"))
                if result.get("error") == "mt5_lock_timeout" and job_type != "verify":
                    # Do not burn the retry budget — another worker was using MT5
                    log.warning("Lock timeout, requeueing account=%s", account_id)
                    enqueue_job(client, settings.redis_queue_key, job)
            except Exception:
                log.exception("Unhandled error processing %s job account=%s", job_type, account_id)
                if should_requeue(job):
                    enqueue_job(client, settings.redis_queue_key, mark_requeue(job))


if __name__ == "__main__":
    main()
