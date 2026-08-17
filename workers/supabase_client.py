from datetime import datetime, timezone

import httpx


def supabase_headers(service_key: str, extra: dict | None = None) -> dict:
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def resolve_pnl_usd(trade: dict, matched_account: dict | None = None) -> float:
    """Store broker/MT5 amounts 1:1. Cent vs USD is a display label only."""
    raw = trade.get("pnl_raw")
    fallback = float(trade.get("pnl_usd") or 0)
    if raw is not None:
        return float(raw)
    return fallback


def trades_to_rows(trades: list, user_id: str, matched_account: dict, source: str) -> list[dict]:
    account_label = matched_account["name"]
    rows = []
    for t in trades:
        pnl = resolve_pnl_usd(t, matched_account)
        close_time = t.get("close_time") or t.get("open_time") or datetime.now(timezone.utc).isoformat()
        open_time = t.get("open_time") or close_time
        row = {
            "user_id": user_id,
            "source": source,
            "ticket": t["ticket"],
            "symbol": t.get("symbol"),
            "direction": t.get("direction"),
            "entry_price": t.get("entry_price"),
            "exit_price": t.get("exit_price"),
            "lot_size": t.get("lot_size"),
            "pnl_usd": pnl,
            "result": "win" if pnl > 0 else "loss" if pnl < 0 else "be",
            "open_time": open_time,
            "close_time": close_time,
            "date": str(close_time)[:10],
            "account": account_label,
            "account_id": matched_account["id"],
        }
        r_value = float(t.get("r_value") or 0)
        if abs(r_value) > 0.01:
            row["r_value"] = r_value
        rows.append(row)
    return rows


def cashflows_to_rows(cashflows: list, user_id: str, matched_account: dict, source: str) -> list[dict]:
    rows = []
    for c in cashflows:
        amount = resolve_pnl_usd(
            {"pnl_raw": c.get("pnl_raw", c.get("amount")), "pnl_usd": c.get("amount") or c.get("pnl_usd")},
            matched_account,
        )
        if amount == 0:
            continue
        occurred = c.get("occurred_at") or c.get("close_time") or c.get("open_time") or datetime.now(timezone.utc).isoformat()
        rows.append(
            {
                "user_id": user_id,
                "account_id": matched_account["id"],
                "ticket": c["ticket"],
                "op_type": c.get("op_type") or ("deposit" if amount >= 0 else "withdrawal"),
                "amount": amount,
                "comment": c.get("comment") or None,
                "occurred_at": occurred,
                "date": str(occurred)[:10],
                "source": source,
            }
        )
    return rows


def fetch_investor_credentials(
    client: httpx.Client, *, supabase_url: str, service_key: str, trading_account_id: str
) -> dict | None:
    url = (
        f"{supabase_url.rstrip('/')}/rest/v1/investor_credentials"
        f"?select=last_synced_at,broker_server,login,encrypted_password"
        f"&trading_account_id=eq.{trading_account_id}&limit=1"
    )
    res = client.get(url, headers=supabase_headers(service_key), timeout=30.0)
    res.raise_for_status()
    rows = res.json()
    return rows[0] if rows else None


def fetch_trading_account(
    client: httpx.Client, *, supabase_url: str, service_key: str, trading_account_id: str
) -> dict | None:
    url = (
        f"{supabase_url.rstrip('/')}/rest/v1/trading_accounts"
        f"?select=id,user_id,name,pnl_denomination"
        f"&id=eq.{trading_account_id}&limit=1"
    )
    res = client.get(url, headers=supabase_headers(service_key), timeout=30.0)
    res.raise_for_status()
    rows = res.json()
    return rows[0] if rows else None


def upsert_trades(
    client: httpx.Client,
    *,
    supabase_url: str,
    service_key: str,
    trading_account_id: str,
    trades: list,
) -> dict:
    account = fetch_trading_account(
        client,
        supabase_url=supabase_url,
        service_key=service_key,
        trading_account_id=trading_account_id,
    )
    if not account:
        raise LookupError("Trading account not found")

    rows = trades_to_rows(trades, account["user_id"], account, "investor_bridge")
    url = f"{supabase_url.rstrip('/')}/rest/v1/trades?on_conflict=account_id,ticket"
    without_r = [{k: v for k, v in row.items() if k != "r_value"} for row in rows]
    with_r = [row for row in rows if "r_value" in row]
    res = client.post(
        url,
        headers=supabase_headers(
            service_key,
            {"Prefer": "resolution=merge-duplicates,return=representation"},
        ),
        json=without_r,
        timeout=60.0,
    )
    if res.status_code >= 400:
        raise RuntimeError(res.text or f"Failed to save trades ({res.status_code})")
    if with_r:
        r_res = client.post(
            url,
            headers=supabase_headers(
                service_key,
                {"Prefer": "resolution=merge-duplicates,return=representation"},
            ),
            json=with_r,
            timeout=60.0,
        )
        if r_res.status_code >= 400:
            raise RuntimeError(r_res.text or f"Failed to save trade R ({r_res.status_code})")

    synced_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _patch_investor_credentials(
        client,
        supabase_url=supabase_url,
        service_key=service_key,
        trading_account_id=trading_account_id,
        payload={"last_synced_at": synced_at, "last_sync_error": None, "sync_stage": None},
    )
    return {"inserted": len(res.json()), "last_synced_at": synced_at}


def upsert_cashflows(
    client: httpx.Client,
    *,
    supabase_url: str,
    service_key: str,
    trading_account_id: str,
    cashflows: list,
) -> dict:
    if not cashflows:
        return {"inserted": 0}
    account = fetch_trading_account(
        client,
        supabase_url=supabase_url,
        service_key=service_key,
        trading_account_id=trading_account_id,
    )
    if not account:
        raise LookupError("Trading account not found")

    rows = cashflows_to_rows(cashflows, account["user_id"], account, "investor_bridge")
    if not rows:
        return {"inserted": 0}
    url = f"{supabase_url.rstrip('/')}/rest/v1/account_cashflows?on_conflict=account_id,ticket"
    res = client.post(
        url,
        headers=supabase_headers(
            service_key,
            {"Prefer": "resolution=merge-duplicates,return=representation"},
        ),
        json=rows,
        timeout=60.0,
    )
    if res.status_code >= 400:
        raise RuntimeError(res.text or f"Failed to save cashflows ({res.status_code})")
    return {"inserted": len(res.json())}


def set_sync_stage(
    client: httpx.Client,
    *,
    supabase_url: str,
    service_key: str,
    trading_account_id: str,
    stage: str,
) -> None:
    _patch_investor_credentials(
        client,
        supabase_url=supabase_url,
        service_key=service_key,
        trading_account_id=trading_account_id,
        payload={"sync_stage": stage, "last_sync_error": None},
    )


def record_sync_success(
    client: httpx.Client,
    *,
    supabase_url: str,
    service_key: str,
    trading_account_id: str,
) -> str:
    """Mark a sync that found zero new trades as successful (advance watermark)."""
    synced_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _patch_investor_credentials(
        client,
        supabase_url=supabase_url,
        service_key=service_key,
        trading_account_id=trading_account_id,
        payload={"last_synced_at": synced_at, "last_sync_error": None, "sync_stage": None},
    )
    return synced_at


def record_sync_error(
    client: httpx.Client,
    *,
    supabase_url: str,
    service_key: str,
    trading_account_id: str,
    error: str,
) -> None:
    _patch_investor_credentials(
        client,
        supabase_url=supabase_url,
        service_key=service_key,
        trading_account_id=trading_account_id,
        payload={
            "last_sync_error": str(error)[:500],
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )


def _patch_investor_credentials(
    client: httpx.Client,
    *,
    supabase_url: str,
    service_key: str,
    trading_account_id: str,
    payload: dict,
) -> None:
    body = dict(payload)
    if "updated_at" not in body:
        body["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    url = (
        f"{supabase_url.rstrip('/')}/rest/v1/investor_credentials"
        f"?trading_account_id=eq.{trading_account_id}"
    )
    res = client.patch(
        url,
        headers=supabase_headers(service_key),
        json=body,
        timeout=30.0,
    )
    # Credentials row may not exist yet in some edge cases — don't crash workers.
    if res.status_code >= 400 and res.status_code != 404:
        raise RuntimeError(res.text or f"Failed to update sync status ({res.status_code})")
