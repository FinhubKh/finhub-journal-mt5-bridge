import json
import uuid

def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:16]}"

def enqueue_job(redis_client, queue_key: str, job: dict) -> str:
    payload = dict(job)
    if not payload.get("job_id"):
        payload["job_id"] = new_job_id()
    redis_client.rpush(queue_key, json.dumps(payload))
    return payload["job_id"]

def claim_job(redis_client, queue_key: str, timeout: int = 5):
    item = redis_client.blpop(queue_key, timeout=timeout)
    if not item:
        return None
    _key, raw = item
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)
