import json
from jobqueue.redis_queue import enqueue_job, claim_job

class FakeRedis:
    def __init__(self):
        self.lists = {}
    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])
    def blpop(self, key, timeout=0):
        items = self.lists.get(key) or []
        if not items:
            return None
        return key, items.pop(0)

def test_enqueue_and_claim_roundtrip():
    r = FakeRedis()
    job = {"job_id": "j1", "trading_account_id": "a1", "login": "1", "password": "p", "server": "S"}
    enqueue_job(r, "q", job)
    claimed = claim_job(r, "q", timeout=1)
    assert claimed["job_id"] == "j1"
    assert claimed["login"] == "1"
