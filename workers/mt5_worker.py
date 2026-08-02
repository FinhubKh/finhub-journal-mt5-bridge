from datetime import datetime, timedelta, timezone

import httpx

from jobqueue.redis_lock import RedisLock
from workers.journal_client import post_error, post_trades
from workers.trade_map import deals_to_trades


def run_sync_job(
    *,
    job,
    mt5,
    http: httpx.Client,
    journal_url: str,
    token: str,
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

        # Hold the lock only for MT5 terminal I/O so another worker can drive MT5
        # while this process posts results to the journal.
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
            msg = "Invalid investor credentials — please re-check"
            post_error(http, journal_url, token, trading_account_id, msg)
            return {"ok": False, "error": msg}

        trades = deals_to_trades(deals or [])
        if not trades:
            post_error(
                http,
                journal_url,
                token,
                trading_account_id,
                "No closed trades found in lookback window",
            )
            return {"ok": False, "error": "no_trades"}
        res = post_trades(http, journal_url, token, trading_account_id, trades)
        if res.status_code >= 400:
            return {"ok": False, "error": f"journal_callback_{res.status_code}"}
        return {"ok": True, "count": len(trades)}
    except Exception as exc:
        msg = f"Broker server didn't respond, try again ({type(exc).__name__})"
        try:
            post_error(http, journal_url, token, trading_account_id, msg)
        except Exception:
            pass
        return {"ok": False, "error": msg}
    finally:
        if lock is not None:
            lock.release()
