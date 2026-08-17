from jobqueue.redis_queue import (
    RESULT_TTL_SECONDS,
    ack_job,
    claim_job,
    enqueue_job,
    get_job_result,
    recover_stale_processing,
    set_job_result,
)


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.sets = {}
        self.hashes = {}
        self.kv = {}
        self.ttls = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def lrange(self, key, start, end):
        items = self.lists.get(key) or []
        if end == -1:
            end = len(items) - 1
        return items[start : end + 1]

    def lrem(self, key, count, value):
        items = self.lists.get(key) or []
        removed = 0
        new_items = []
        for item in items:
            if removed < count and item == value:
                removed += 1
                continue
            new_items.append(item)
        self.lists[key] = new_items
        return removed

    def llen(self, key):
        return len(self.lists.get(key) or [])

    def blpop(self, key, timeout=0):
        items = self.lists.get(key) or []
        if not items:
            return None
        return key, items.pop(0)

    def sadd(self, key, value):
        s = self.sets.setdefault(key, set())
        if value in s:
            return 0
        s.add(value)
        return 1

    def srem(self, key, value):
        s = self.sets.get(key) or set()
        if value not in s:
            return 0
        s.remove(value)
        return 1

    def scard(self, key):
        return len(self.sets.get(key) or set())

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def hget(self, key, field):
        return (self.hashes.get(key) or {}).get(field)

    def hgetall(self, key):
        return dict(self.hashes.get(key) or {})

    def hdel(self, key, field):
        h = self.hashes.get(key) or {}
        if field not in h:
            return 0
        del h[field]
        return 1

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def setex(self, key, time, value):
        self.kv[key] = value
        self.ttls[key] = time
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, key):
        self.kv.pop(key, None)
        self.ttls.pop(key, None)
        return 1


def test_enqueue_and_claim_roundtrip():
    r = FakeRedis()
    job = {"job_id": "j1", "trading_account_id": "a1", "login": "1", "password": "p", "server": "S"}
    enqueue_job(r, "q", job)
    # Password must not sit in the durable latest hash.
    assert "password" not in (r.hashes.get("q:latest") or {}).get("a1:sync", "")
    claimed = claim_job(r, "q", timeout=1)
    assert claimed["job_id"] == "j1"
    assert claimed["login"] == "1"
    assert claimed["password"] == "p"
    assert r.lists.get("q:processing")


def test_coalesce_same_account_keeps_latest_credentials():
    r = FakeRedis()
    enqueue_job(
        r,
        "q",
        {
            "job_id": "j1",
            "trading_account_id": "a1",
            "login": "1",
            "password": "old",
            "server": "S",
        },
    )
    enqueue_job(
        r,
        "q",
        {
            "job_id": "j2",
            "trading_account_id": "a1",
            "login": "1",
            "password": "new",
            "server": "S",
        },
    )
    assert len(r.lists["q"]) == 1
    claimed = claim_job(r, "q", timeout=1)
    assert claimed["password"] == "new"
    assert claimed["job_id"] == "j2"
    assert claim_job(r, "q", timeout=1) is None


def test_different_accounts_both_queued():
    r = FakeRedis()
    enqueue_job(r, "q", {"trading_account_id": "a1", "login": "1", "password": "p", "server": "S"})
    enqueue_job(r, "q", {"trading_account_id": "a2", "login": "2", "password": "p", "server": "S"})
    assert len(r.lists["q"]) == 2
    ids = {claim_job(r, "q")["trading_account_id"], claim_job(r, "q")["trading_account_id"]}
    assert ids == {"a1", "a2"}


def test_verify_and_sync_same_account_both_queued():
    r = FakeRedis()
    enqueue_job(
        r,
        "q",
        {
            "job_id": "sync-1",
            "job_type": "sync",
            "trading_account_id": "a1",
            "login": "1",
            "password": "p",
            "server": "S",
        },
    )
    enqueue_job(
        r,
        "q",
        {
            "job_id": "verify-1",
            "job_type": "verify",
            "trading_account_id": "a1",
            "login": "1",
            "password": "p",
            "server": "S",
        },
    )
    assert len(r.lists["q"]) == 2
    # Verify is lpush'd so it is claimed before the already-queued sync.
    assert claim_job(r, "q")["job_type"] == "verify"
    assert claim_job(r, "q")["job_type"] == "sync"


def test_job_result_roundtrip():
    r = FakeRedis()
    set_job_result(r, "q", "job-1", {"status": "pending"})
    assert get_job_result(r, "q", "job-1") == {"status": "pending"}
    set_job_result(r, "q", "job-1", {"status": "done", "ok": True})
    assert get_job_result(r, "q", "job-1") == {"status": "done", "ok": True}
    assert r.ttls["q:result:job-1"] == RESULT_TTL_SECONDS


def test_ack_removes_processing_entry():
    r = FakeRedis()
    enqueue_job(r, "q", {"job_id": "j1", "trading_account_id": "a1", "password": "p"})
    job = claim_job(r, "q")
    assert r.llen("q:processing") == 1
    ack_job(r, "q", job)
    assert r.llen("q:processing") == 0


def test_recover_stale_processing_requeues():
    r = FakeRedis()
    enqueue_job(r, "q", {"job_id": "j1", "trading_account_id": "a1", "login": "1", "server": "S"})
    job = claim_job(r, "q")
    # Force stale claimed_at
    import json

    meta = json.loads(r.hashes["q:processing_meta"]["j1"])
    meta["claimed_at"] = 1
    r.hashes["q:processing_meta"]["j1"] = json.dumps(meta)
    n = recover_stale_processing(r, "q", max_age_seconds=10)
    assert n == 1
    claimed = claim_job(r, "q")
    assert claimed["trading_account_id"] == "a1"
