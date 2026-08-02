from fastapi.testclient import TestClient

from app.main import create_app


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.sets = {}
        self.hashes = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def sadd(self, key, value):
        s = self.sets.setdefault(key, set())
        if value in s:
            return 0
        s.add(value)
        return 1

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1


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
    assert client.get("/health").json() == {"ok": True}


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
