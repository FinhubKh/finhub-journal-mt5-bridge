from datetime import datetime, timedelta, timezone

import httpx

from jobqueue.redis_lock import RedisLock
from jobqueue.redis_queue import set_job_result
from workers.supabase_client import record_sync_error, upsert_trades
from workers.trade_map import deals_to_trades

LOGIN_FAILED_MSG = "Login failed — check broker server, MT5 login, and investor password"


def _verify_result(job, *, ok: bool, error: str | None = None) -> dict:
    payload = {
        "status": "done",
        "ok": ok,
        "trading_account_id": job["trading_account_id"],
    }
    if error:
        payload["error"] = error
    return payload


def run_verify_job(
    *,
    job,
    mt5,
    redis_client,
    terminal_path: str,
    queue_key: str,
    lock_key: str = "finhubkh:mt5:terminal_lock",
    lock_ttl_seconds: int = 300,
    lock_wait_seconds: int = 120,
) -> dict:
    job_id = job["job_id"]
    lock = None
    login_ok = False
    try:
        lock = RedisLock(
            redis_client,
            lock_key,
            ttl_seconds=lock_ttl_seconds,
            wait_seconds=lock_wait_seconds,
        )
        if not lock.acquire():
            set_job_result(
                redis_client,
                queue_key,
                job_id,
                _verify_result(
                    job,
                    ok=False,
                    error="Could not verify right now — bridge busy or unreachable. Try again.",
                ),
            )
            return {"ok": False, "error": "mt5_lock_timeout"}

        try:
            login_ok = bool(
                mt5.initialize(terminal_path, job["login"], job["password"], job["server"])
            )
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass
            lock.release()
            lock = None

        if login_ok:
            set_job_result(
                redis_client,
                queue_key,
                job_id,
                _verify_result(job, ok=True),
            )
            return {"ok": True}

        set_job_result(
            redis_client,
            queue_key,
            job_id,
            _verify_result(job, ok=False, error=LOGIN_FAILED_MSG),
        )
        return {"ok": False, "error": LOGIN_FAILED_MSG}
    except Exception as exc:
        msg = "Could not verify right now — bridge busy or unreachable. Try again."
        try:
            set_job_result(
                redis_client,
                queue_key,
                job_id,
                _verify_result(job, ok=False, error=msg),
            )
        except Exception:
            pass
        return {"ok": False, "error": f"{msg} ({type(exc).__name__})"}
    finally:
        if lock is not None:
            lock.release()


def run_sync_job(
    *,
    job,
    mt5,
    http: httpx.Client,
    supabase_url: str,
    service_key: str,
    terminal_path: str,
    lookback_days: int,
    redis_client=None,
    lock_key: str = "finhubkh:mt5:terminal_lock",
    lock_ttl_seconds: int = 300,
    lock_wait_seconds: int = 120,
) -> dict:
    trading_account_id = job["trading_account_id"]
    lock = None
    deals = None
    login_ok = False
    try:
        if redis_client is not None:
            lock = RedisLock(
                redis_client,
                lock_key,
                ttl_seconds=lock_ttl_seconds,
                wait_seconds=lock_wait_seconds,
            )
            if not lock.acquire():
                return {"ok": False, "error": "mt5_lock_timeout"}

        # Hold the lock only for MT5 terminal I/O so another worker can write to DB
        # while this process completes HTTP upserts.
        try:
            login_ok = bool(
                mt5.initialize(terminal_path, job["login"], job["password"], job["server"])
            )
            if login_ok:
                date_to = datetime.now(timezone.utc)
                date_from = date_to - timedelta(days=lookback_days)
                deals = mt5.history_deals(date_from, date_to)
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass
            if lock is not None:
                lock.release()
                lock = None

        if not login_ok:
            msg = LOGIN_FAILED_MSG
            record_sync_error(
                http,
                supabase_url=supabase_url,
                service_key=service_key,
                trading_account_id=trading_account_id,
                error=msg,
            )
            return {"ok": False, "error": msg}

        trades = deals_to_trades(deals or [])
        if not trades:
            msg = "No closed trades found in lookback window"
            record_sync_error(
                http,
                supabase_url=supabase_url,
                service_key=service_key,
                trading_account_id=trading_account_id,
                error=msg,
            )
            return {"ok": False, "error": "no_trades"}

        saved = upsert_trades(
            http,
            supabase_url=supabase_url,
            service_key=service_key,
            trading_account_id=trading_account_id,
            trades=trades,
        )
        return {"ok": True, "count": saved.get("inserted", len(trades))}
    except Exception as exc:
        msg = f"Broker server didn't respond, try again ({type(exc).__name__})"
        try:
            record_sync_error(
                http,
                supabase_url=supabase_url,
                service_key=service_key,
                trading_account_id=trading_account_id,
                error=msg,
            )
        except Exception:
            pass
        return {"ok": False, "error": msg}
    finally:
        if lock is not None:
            lock.release()
