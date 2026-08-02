import httpx

from jobqueue.redis_lock import RedisLock
from jobqueue.redis_queue import get_job_result
from workers.mt5_worker import run_sync_job, run_verify_job


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


class FakeLockRedis:
    def __init__(self, hold=False):
        self.kv = {}
        self.hold = hold

    def set(self, key, value, nx=False, ex=None):
        if self.hold:
            return False
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, key):
        self.kv.pop(key, None)
        return 1


def _supabase_transport(on_trades=None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/trading_accounts"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "a1",
                        "user_id": "u1",
                        "name": "Live",
                        "pnl_denomination": "usd",
                    }
                ],
            )
        if path.endswith("/trades"):
            if on_trades:
                on_trades(request)
            import json

            return httpx.Response(201, json=json.loads(request.content.decode()))
        if path.endswith("/investor_credentials"):
            return httpx.Response(204)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_worker_upserts_trades_on_success():
    seen = {}

    def on_trades(request: httpx.Request):
        import json

        seen["payload"] = json.loads(request.content.decode())

    client = httpx.Client(transport=_supabase_transport(on_trades))
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
        supabase_url="https://example.supabase.co",
        service_key="svc",
        terminal_path="C:/mt5/terminal64.exe",
        lookback_days=90,
        redis_client=FakeLockRedis(),
        lock_wait_seconds=1,
    )
    assert result["ok"] is True
    assert len(seen["payload"]) == 1
    assert seen["payload"][0]["source"] == "investor_bridge"


def test_worker_records_error_on_login_fail():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        if request.url.path.endswith("/investor_credentials"):
            seen["payload"] = json.loads(request.content.decode())
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_sync_job(
        job={"trading_account_id": "a1", "login": "1", "password": "bad", "server": "S"},
        mt5=FakeMt5(fail_login=True),
        http=client,
        supabase_url="https://example.supabase.co",
        service_key="svc",
        terminal_path="C:/mt5/terminal64.exe",
        lookback_days=90,
        redis_client=FakeLockRedis(),
        lock_wait_seconds=1,
    )
    assert result["ok"] is False
    assert "broker server" in seen["payload"]["last_sync_error"].lower()


def test_worker_lock_timeout_without_mt5():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    result = run_sync_job(
        job={"trading_account_id": "a1", "login": "1", "password": "p", "server": "S"},
        mt5=FakeMt5(),
        http=client,
        supabase_url="https://example.supabase.co",
        service_key="svc",
        terminal_path="C:/mt5/terminal64.exe",
        lookback_days=90,
        redis_client=FakeLockRedis(hold=True),
        lock_wait_seconds=0,
        lock_ttl_seconds=1,
    )
    assert result == {"ok": False, "error": "mt5_lock_timeout"}


def test_redis_lock_roundtrip():
    r = FakeLockRedis()
    lock = RedisLock(r, "k", ttl_seconds=10, wait_seconds=1, poll_seconds=0.01)
    assert lock.acquire() is True
    assert r.get("k") == lock.token
    lock.release()
    assert r.get("k") is None


class FakeResultRedis(FakeLockRedis):
    def __init__(self, hold=False):
        super().__init__(hold=hold)
        self.ttls = {}

    def setex(self, key, time, value):
        self.kv[key] = value
        self.ttls[key] = time
        return True


def test_verify_job_success_writes_result():
    redis_client = FakeResultRedis()
    result = run_verify_job(
        job={
            "job_id": "verify-1",
            "job_type": "verify",
            "trading_account_id": "a1",
            "login": "1",
            "password": "p",
            "server": "S",
        },
        mt5=FakeMt5(),
        redis_client=redis_client,
        terminal_path="C:/mt5/terminal64.exe",
        queue_key="q",
        lock_key="lock",
        lock_wait_seconds=1,
    )
    assert result == {"ok": True}
    assert get_job_result(redis_client, "q", "verify-1") == {
        "status": "done",
        "ok": True,
        "trading_account_id": "a1",
    }


def test_verify_job_fail_writes_generic_error():
    redis_client = FakeResultRedis()
    result = run_verify_job(
        job={
            "job_id": "verify-2",
            "job_type": "verify",
            "trading_account_id": "a1",
            "login": "1",
            "password": "bad",
            "server": "S",
        },
        mt5=FakeMt5(fail_login=True),
        redis_client=redis_client,
        terminal_path="C:/mt5/terminal64.exe",
        queue_key="q",
        lock_key="lock",
        lock_wait_seconds=1,
    )
    assert result["ok"] is False
    stored = get_job_result(redis_client, "q", "verify-2")
    assert stored["status"] == "done"
    assert stored["ok"] is False
    assert "broker server" in stored["error"].lower()
