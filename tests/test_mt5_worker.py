import httpx
from workers.mt5_worker import run_sync_job


class FakeMt5:
    def __init__(self, deals=None, fail_login=False):
        self.deals = deals or []
        self.fail_login = fail_login
        self.initialized = False

    def initialize(self, path, login, password, server):
        if self.fail_login:
            return False
        self.initialized = True
        return True

    def shutdown(self):
        self.initialized = False

    def history_deals(self, date_from, date_to):
        return list(self.deals)


def test_worker_posts_trades_on_success():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    mt5 = FakeMt5(
        deals=[
            {
                "ticket": 1,
                "order": 10,
                "position_id": 1,
                "entry": "in",
                "type": "buy",
                "symbol": "EURUSD",
                "price": 1.1,
                "volume": 0.1,
                "profit": 0,
                "swap": 0,
                "commission": 0,
                "time": "2026-01-01T10:00:00Z",
            },
            {
                "ticket": 2,
                "order": 11,
                "position_id": 1,
                "entry": "out",
                "type": "sell",
                "symbol": "EURUSD",
                "price": 1.2,
                "volume": 0.1,
                "profit": 10,
                "swap": 0,
                "commission": 0,
                "time": "2026-01-01T11:00:00Z",
            },
        ]
    )
    result = run_sync_job(
        job={"trading_account_id": "a1", "login": "1", "password": "p", "server": "S"},
        mt5=mt5,
        http=client,
        journal_url="http://journal/v1/bridge/sync",
        token="tok",
        terminal_path="C:/mt5/terminal64.exe",
        lookback_days=90,
    )
    assert result["ok"] is True
    assert len(seen["payload"]["trades"]) == 1


def test_worker_posts_error_on_login_fail():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"acknowledged": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_sync_job(
        job={"trading_account_id": "a1", "login": "1", "password": "bad", "server": "S"},
        mt5=FakeMt5(fail_login=True),
        http=client,
        journal_url="http://journal/v1/bridge/sync",
        token="tok",
        terminal_path="C:/mt5/terminal64.exe",
        lookback_days=90,
    )
    assert result["ok"] is False
    assert "credentials" in seen["payload"]["error"].lower() or "login" in seen["payload"]["error"].lower()
