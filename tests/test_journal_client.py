import httpx
from workers.journal_client import post_trades, post_error


def test_post_trades_sends_token_and_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers.get("x-bridge-token")
        seen["body"] = httpx.Response(200).json  # placeholder
        import json

        seen["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    post_trades(
        client,
        "http://journal/v1/bridge/sync",
        "tok",
        "acct-1",
        [
            {
                "ticket": 1,
                "symbol": "EURUSD",
                "direction": "buy",
                "entry_price": 1,
                "exit_price": 2,
                "lot_size": 0.1,
                "pnl_usd": 1,
                "r_value": 0,
                "open_time": "t",
                "close_time": "t",
            }
        ],
    )
    assert seen["token"] == "tok"
    assert seen["payload"]["trading_account_id"] == "acct-1"
    assert len(seen["payload"]["trades"]) == 1


def test_post_error_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"acknowledged": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    post_error(client, "http://journal/v1/bridge/sync", "tok", "acct-1", "Invalid investor credentials")
    assert seen["payload"]["error"]
