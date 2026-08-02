from jobqueue.redis_queue import enqueue_job, claim_job


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.sets = {}
        self.hashes = {}
        self.kv = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

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

    def hset(self, key, field, value):
        h = self.hashes.setdefault(key, {})
        h[field] = value
        return 1

    def hget(self, key, field):
        return (self.hashes.get(key) or {}).get(field)

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
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, key):
        self.kv.pop(key, None)
        return 1


def test_enqueue_and_claim_roundtrip():
    r = FakeRedis()
    job = {"job_id": "j1", "trading_account_id": "a1", "login": "1", "password": "p", "server": "S"}
    enqueue_job(r, "q", job)
    claimed = claim_job(r, "q", timeout=1)
    assert claimed["job_id"] == "j1"
    assert claimed["login"] == "1"


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
