import os
import time

import httpx

from app.config import get_settings
from jobqueue.redis_client import make_redis
from jobqueue.redis_queue import ack_job, claim_job, enqueue_job, recover_stale_processing
from workers.credentials import CredentialsError, resolve_job_credentials
from workers.logging_setup import get_logger
from workers.mt5_worker import run_sync_job, run_verify_job
from workers.supabase_client import record_sync_error
from workers.terminal_map import resolve_terminal_path


def _http_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


def _touch_heartbeat(redis_client, key: str, ttl: int, worker_id: str) -> None:
    try:
        redis_client.setex(key, ttl, f"{worker_id}:{int(time.time())}")
    except Exception:
        pass


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

    try:
        n = recover_stale_processing(
            client,
            settings.redis_queue_key,
            max_age_seconds=0,
        )
        if n:
            log.warning("Requeued %s leftover processing job(s) on startup", n)
    except Exception:
        log.exception("Failed recovering leftover processing jobs on startup")

    last_recover = 0.0
    with _http_client() as http:
        while True:
            _touch_heartbeat(
                client,
                settings.worker_heartbeat_key,
                settings.worker_heartbeat_ttl_seconds,
                worker_id,
            )
            now = time.monotonic()
            if now - last_recover > 30:
                try:
                    n = recover_stale_processing(
                        client,
                        settings.redis_queue_key,
                        max_age_seconds=settings.processing_stale_seconds,
                    )
                    if n:
                        log.warning("Requeued %s stale processing job(s)", n)
                except Exception:
                    log.exception("Failed recovering stale processing jobs")
                last_recover = now

            try:
                job = claim_job(client, settings.redis_queue_key, timeout=5)
            except Exception:
                log.exception("Failed claiming next job")
                time.sleep(1)
                continue
            if not job:
                continue
            job_type = str(job.get("job_type") or "sync")
            account_id = job.get("trading_account_id")
            try:
                job = resolve_job_credentials(
                    http,
                    job=job,
                    supabase_url=settings.supabase_url,
                    service_key=settings.supabase_service_role_key,
                    encryption_key=settings.investor_cred_encryption_key,
                )
            except CredentialsError as exc:
                log.warning("Credentials resolve failed account=%s error=%s", account_id, exc)
                from jobqueue.redis_queue import set_job_result

                if job.get("job_id"):
                    set_job_result(
                        client,
                        settings.redis_queue_key,
                        job["job_id"],
                        {
                            "status": "done",
                            "ok": False,
                            "trading_account_id": account_id,
                            "error": str(exc),
                        },
                    )
                if account_id and job_type != "verify":
                    try:
                        record_sync_error(
                            http,
                            supabase_url=settings.supabase_url,
                            service_key=settings.supabase_service_role_key,
                            trading_account_id=str(account_id),
                            error=str(exc),
                        )
                    except Exception:
                        log.exception("Failed recording credential error account=%s", account_id)
                ack_job(client, settings.redis_queue_key, job)
                continue

            terminal_path = resolve_terminal_path(
                str(job.get("server") or ""),
                default_path=settings.mt5_terminal_path,
                map_path=settings.mt5_terminal_map_path or None,
            )
            log.info(
                "Claimed %s job account=%s job_id=%s server=%s terminal=%s",
                job_type,
                account_id,
                job.get("job_id"),
                job.get("server"),
                terminal_path,
            )
            try:
                if job_type == "verify":
                    # Don't leave Connect spinning for up to 2 minutes if MT5 is busy.
                    result = run_verify_job(
                        job=job,
                        mt5=mt5,
                        redis_client=client,
                        terminal_path=terminal_path,
                        queue_key=settings.redis_queue_key,
                        lock_key=settings.mt5_lock_key,
                        lock_ttl_seconds=settings.mt5_lock_ttl_seconds,
                        lock_wait_seconds=min(20, settings.mt5_lock_wait_seconds),
                        init_timeout_ms=settings.mt5_init_timeout_ms,
                    )
                    if result.get("ok"):
                        # Queue trade pull after login works — don't make Connect wait on it.
                        sync_job = {
                            "trading_account_id": job["trading_account_id"],
                            "job_type": "sync",
                        }
                        enqueue_job(client, settings.redis_queue_key, sync_job)
                else:
                    result = run_sync_job(
                        job=job,
                        mt5=mt5,
                        http=http,
                        supabase_url=settings.supabase_url,
                        service_key=settings.supabase_service_role_key,
                        terminal_path=terminal_path,
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
                    log.warning(
                        "%s job failed account=%s error=%s",
                        job_type,
                        account_id,
                        result.get("error"),
                    )
                if result.get("error") == "mt5_lock_timeout" and job_type != "verify":
                    # Do not burn the retry budget — another worker was using MT5
                    log.warning("Lock timeout, requeueing account=%s", account_id)
                    safe = {
                        "trading_account_id": job["trading_account_id"],
                        "job_type": job_type,
                        "job_id": job.get("job_id"),
                        "attempt": job.get("attempt"),
                    }
                    enqueue_job(client, settings.redis_queue_key, safe)
            except Exception:
                log.exception("Unhandled error processing %s job account=%s", job_type, account_id)
                if should_requeue(job):
                    safe = mark_requeue(
                        {
                            "trading_account_id": job["trading_account_id"],
                            "job_type": job_type,
                            "attempt": job.get("attempt"),
                        }
                    )
                    enqueue_job(client, settings.redis_queue_key, safe)
            finally:
                ack_job(client, settings.redis_queue_key, job)


if __name__ == "__main__":
    main()
