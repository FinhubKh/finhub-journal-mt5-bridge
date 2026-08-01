from datetime import datetime, timedelta, timezone

import httpx

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
) -> dict:
    trading_account_id = job["trading_account_id"]
    try:
        ok = mt5.initialize(terminal_path, job["login"], job["password"], job["server"])
        if not ok:
            msg = "Invalid investor credentials — please re-check"
            post_error(http, journal_url, token, trading_account_id, msg)
            return {"ok": False, "error": msg}
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=lookback_days)
        deals = mt5.history_deals(date_from, date_to)
        trades = deals_to_trades(deals)
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
        try:
            mt5.shutdown()
        except Exception:
            pass
