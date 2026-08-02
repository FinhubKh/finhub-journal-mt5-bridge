from datetime import datetime, timedelta, timezone

import httpx

from jobqueue.redis_lock import RedisLock
from jobqueue.redis_queue import set_job_result
from workers.supabase_client import fetch_investor_credentials, record_sync_error, upsert_trades
from workers.trade_map import deals_to_trades

LOGIN_FAILED_MSG = "Login failed — check broker server, MT5 login, and investor password"

# Re-pull a little before the last successful sync in case a deal settled
# late or a prior sync was cut short, without re-walking the full history.
INCREMENTAL_SYNC_OVERLAP_HOURS = 24


def _resolve_sync_window(
    http: httpx.Client,
    *,
    supabase_url: str,
    service_key: str,
    trading_account_id: str,
    lookback_days: int,
) -> tuple[datetime, datetime]:
    date_to = datetime.now(timezone.utc)
    full_history_from = date_to - timedelta(days=lookback_days)
    try:
        creds = fetch_investor_credentials(
            http,
            supabase_url=supabase_url,
            service_key=service_key,
            trading_account_id=trading_account_id,
        )
        last_synced_at = (creds or {}).get("last_synced_at")
        if last_synced_at:
            since = datetime.fromisoformat(str(last_synced_at).replace("Z", "+00:00"))
            date_from = since - timedelta(hours=INCREMENTAL_SYNC_OVERLAP_HOURS)
            return max(date_from, full_history_from), date_to
    except Exception:
        pass
    # First sync (or credentials lookup failed) — fall back to the full window.
    return full_history_from, date_to


def _mt5_error_detail(mt5) -> str:
    """Best-effort MT5 error reason (e.g. 'Invalid account', 'No connection') for diagnostics."""
    last_error = getattr(mt5, "last_error", None)
    if not callable(last_error):
        return ""
    try:
        code, desc = last_error()
    except Exception:
        return ""
    if not desc or code == 1:
        return ""
    return f" ({desc})"


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
    init_timeout_ms: int = 15000,
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
                mt5.initialize(
                    terminal_path,
                    job["login"],
                    job["password"],
                    job["server"],
                    timeout_ms=init_timeout_ms,
                )
            )
            error_detail = "" if login_ok else _mt5_error_detail(mt5)
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

        msg = LOGIN_FAILED_MSG + error_detail
        set_job_result(
            redis_client,
            queue_key,
            job_id,
            _verify_result(job, ok=False, error=msg),
        )
        return {"ok": False, "error": msg}
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
    init_timeout_ms: int = 15000,
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
                mt5.initialize(
                    terminal_path,
                    job["login"],
                    job["password"],
                    job["server"],
                    timeout_ms=init_timeout_ms,
                )
            )
            error_detail = "" if login_ok else _mt5_error_detail(mt5)
            if login_ok:
                date_from, date_to = _resolve_sync_window(
                    http,
                    supabase_url=supabase_url,
                    service_key=service_key,
                    trading_account_id=trading_account_id,
                    lookback_days=lookback_days,
                )
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
            msg = LOGIN_FAILED_MSG + error_detail
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
