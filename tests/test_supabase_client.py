import json

import httpx

from workers.supabase_client import (
    account_has_cashflows,
    record_sync_error,
    resolve_pnl_usd,
    trades_to_rows,
    upsert_cashflows,
    upsert_trades,
)


def test_trades_to_rows_maps_cent_pnl():
    account = {"id": "a1", "name": "Cent", "pnl_denomination": "cent", "user_id": "u1"}
    rows = trades_to_rows(
        [
            {
                "ticket": 10,
                "symbol": "EURUSD",
                "direction": "buy",
                "entry_price": 1.1,
                "exit_price": 1.2,
                "lot_size": 0.1,
                "pnl_raw": 250,
                "pnl_usd": 250,
                "r_value": 0,
                "open_time": "2026-01-01T10:00:00Z",
                "close_time": "2026-01-01T11:00:00Z",
            }
        ],
        "u1",
        account,
        "investor_bridge",
    )
    # Cent accounts store MT5 raw amounts 1:1 (display uses ¢, no ÷100).
    assert rows[0]["pnl_usd"] == 250
    assert rows[0]["result"] == "win"
    assert rows[0]["account_id"] == "a1"
    assert rows[0]["source"] == "investor_bridge"


def test_resolve_pnl_usd_fallback():
    account = {"pnl_denomination": "usd"}
    assert resolve_pnl_usd({"pnl_usd": 12}, account) == 12.0


def test_upsert_trades_writes_db_and_status():
    seen = {"urls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["urls"].append((request.method, str(request.url)))
        if request.url.path.endswith("/trading_accounts"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "acct-1",
                        "user_id": "user-1",
                        "name": "Live",
                        "pnl_denomination": "usd",
                    }
                ],
            )
        if request.url.path.endswith("/trades"):
            body = json.loads(request.content.decode())
            seen["trade_rows"] = body
            return httpx.Response(201, json=body)
        if request.url.path.endswith("/investor_credentials"):
            seen["status"] = json.loads(request.content.decode())
            return httpx.Response(204)
        return httpx.Response(404, json={"error": "missing"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = upsert_trades(
        client,
        supabase_url="https://example.supabase.co",
        service_key="svc",
        trading_account_id="acct-1",
        trades=[
            {
                "ticket": 1,
                "symbol": "EURUSD",
                "direction": "buy",
                "entry_price": 1,
                "exit_price": 2,
                "lot_size": 0.1,
                "pnl_usd": 5,
                "pnl_raw": 5,
                "r_value": 0,
                "open_time": "2026-01-01T10:00:00Z",
                "close_time": "2026-01-01T11:00:00Z",
            }
        ],
    )
    assert result["inserted"] == 1
    assert seen["trade_rows"][0]["user_id"] == "user-1"
    assert seen["status"]["last_sync_error"] is None
    trade_posts = [u for m, u in seen["urls"] if m == "POST" and "/trades" in u]
    assert trade_posts and "on_conflict=account_id,ticket" in trade_posts[0]


def test_record_sync_error_patches_credentials():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode())
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    record_sync_error(
        client,
        supabase_url="https://example.supabase.co",
        service_key="svc",
        trading_account_id="acct-1",
        error="Invalid investor credentials",
    )
    assert "Invalid" in seen["payload"]["last_sync_error"]


def test_account_has_cashflows():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/account_cashflows") and request.method == "GET":
            return httpx.Response(200, json=[{"id": "c1"}])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert (
        account_has_cashflows(
            client,
            supabase_url="https://example.supabase.co",
            service_key="svc",
            trading_account_id="acct-1",
        )
        is True
    )


def test_upsert_cashflows_writes_deposits():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/trading_accounts"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "acct-1",
                        "user_id": "user-1",
                        "name": "Live",
                        "pnl_denomination": "usd",
                    }
                ],
            )
        if request.url.path.endswith("/account_cashflows"):
            seen["rows"] = json.loads(request.content.decode())
            return httpx.Response(201, json=seen["rows"])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = upsert_cashflows(
        client,
        supabase_url="https://example.supabase.co",
        service_key="svc",
        trading_account_id="acct-1",
        cashflows=[
            {
                "ticket": 50,
                "op_type": "deposit",
                "amount": 1000,
                "pnl_raw": 1000,
                "comment": "Deposit",
                "open_time": "2026-01-01T09:00:00Z",
                "close_time": "2026-01-01T09:00:00Z",
            }
        ],
    )
    assert result["inserted"] == 1
    assert seen["rows"][0]["op_type"] == "deposit"
    assert seen["rows"][0]["amount"] == 1000
    assert seen["rows"][0]["source"] == "investor_bridge"
