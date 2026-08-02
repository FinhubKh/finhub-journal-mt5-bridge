from fastapi.testclient import TestClient

from app.main import create_app


class FakeRedis:
    def __init__(self, *, ping_error: Exception | None = None):
        self.lists = {}
        self.sets = {}
        self.hashes = {}
        self.kv = {}
        self.ping_error = ping_error

    def ping(self):
        if self.ping_error:
            raise self.ping_error
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def llen(self, key):
        return len(self.lists.get(key) or [])

    def sadd(self, key, value):
        s = self.sets.setdefault(key, set())
        if value in s:
            return 0
        s.add(value)
        return 1

    def scard(self, key):
        return len(self.sets.get(key) or set())

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def get(self, key):
        return self.kv.get(key)

    def setex(self, key, time, value):
        self.kv[key] = value
        return True

    def hget(self, key, field):
        return (self.hashes.get(key) or {}).get(field)

    def hdel(self, key, field):
        h = self.hashes.get(key) or {}
        if field not in h:
            return 0
        del h[field]
        return 1

    def srem(self, key, value):
        s = self.sets.get(key) or set()
        if value not in s:
            return 0
        s.remove(value)
        return 1

    def blpop(self, key, timeout=0):
        items = self.lists.get(key) or []
        if not items:
            return None
        return key, items.pop(0)


def test_health():
    app = create_app(
        redis_client=FakeRedis(),
        settings_overrides={
            "bridge_service_token": "tok",
            "journal_bridge_sync_url": "http://journal/v1/bridge/sync",
            "redis_queue_key": "q",
        },
    )
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["redis"] == {"ok": True, "error": None}
    assert body["queue"] == {"pending_jobs": 0, "pending_accounts": 0}
    assert body["mt5_lock_held"] is False


def test_health_reports_down_when_redis_unreachable():
    app = create_app(
        redis_client=FakeRedis(ping_error=ConnectionError("boom")),
        settings_overrides={
            "bridge_service_token": "tok",
            "journal_bridge_sync_url": "http://journal/v1/bridge/sync",
            "redis_queue_key": "q",
        },
    )
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 503
    body = res.json()
    assert body["ok"] is False
    assert body["redis"]["error"] == "boom"


def test_root_renders_html_dashboard():
    app = create_app(
        redis_client=FakeRedis(),
        settings_overrides={
            "bridge_service_token": "tok",
            "journal_bridge_sync_url": "http://journal/v1/bridge/sync",
            "redis_queue_key": "q",
        },
    )
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "FinHub MT5 Bridge" in res.text


def test_jobs_sync_requires_token():
    app = create_app(
        redis_client=FakeRedis(),
        settings_overrides={
            "bridge_service_token": "tok",
            "journal_bridge_sync_url": "http://journal/v1/bridge/sync",
            "redis_queue_key": "q",
        },
    )
    client = TestClient(app)
    res = client.post(
        "/jobs/sync",
        json={"trading_account_id": "a1", "login": "1", "password": "p", "server": "S"},
    )
    assert res.status_code == 401


def test_jobs_sync_enqueues():
    fake = FakeRedis()
    app = create_app(
        redis_client=fake,
        settings_overrides={
            "bridge_service_token": "tok",
            "journal_bridge_sync_url": "http://journal/v1/bridge/sync",
            "redis_queue_key": "q",
        },
    )
    client = TestClient(app)
    res = client.post(
        "/jobs/sync",
        headers={"x-bridge-token": "tok"},
        json={"trading_account_id": "a1", "login": "1", "password": "p", "server": "S"},
    )
    assert res.status_code == 202
    body = res.json()
    assert body["job_id"]
    assert len(fake.lists["q"]) == 1


def test_jobs_verify_enqueues_and_marks_pending():
    fake = FakeRedis()
    app = create_app(
        redis_client=fake,
        settings_overrides={
            "bridge_service_token": "tok",
            "journal_bridge_sync_url": "http://journal/v1/bridge/sync",
            "redis_queue_key": "q",
        },
    )
    client = TestClient(app)
    res = client.post(
        "/jobs/verify",
        headers={"x-bridge-token": "tok"},
        json={"trading_account_id": "a1", "login": "1", "password": "p", "server": "S"},
    )
    assert res.status_code == 202
    body = res.json()
    assert body["job_id"]
    assert len(fake.lists["q"]) == 1
    pending = fake.kv[f"q:result:{body['job_id']}"]
    assert '"pending"' in pending


def test_jobs_result_returns_stored_payload():
    fake = FakeRedis()
    fake.setex("q:result:job-9", 300, '{"status":"done","ok":true}')
    app = create_app(
        redis_client=fake,
        settings_overrides={
            "bridge_service_token": "tok",
            "journal_bridge_sync_url": "http://journal/v1/bridge/sync",
            "redis_queue_key": "q",
        },
    )
    client = TestClient(app)
    res = client.get("/jobs/job-9/result", headers={"x-bridge-token": "tok"})
    assert res.status_code == 200
    assert res.json() == {"status": "done", "ok": True}


def test_jobs_result_404_when_unknown():
    fake = FakeRedis()
    app = create_app(
        redis_client=fake,
        settings_overrides={
            "bridge_service_token": "tok",
            "journal_bridge_sync_url": "http://journal/v1/bridge/sync",
            "redis_queue_key": "q",
        },
    )
    client = TestClient(app)
    res = client.get("/jobs/missing/result", headers={"x-bridge-token": "tok"})
    assert res.status_code == 404
