import json
import uuid


def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:16]}"


def _pending_key(queue_key: str) -> str:
    return f"{queue_key}:pending"


def _latest_key(queue_key: str) -> str:
    return f"{queue_key}:latest"


def enqueue_job(redis_client, queue_key: str, job: dict) -> str:
    """Enqueue a sync job, coalescing duplicates for the same trading_account_id.

    If an account already has a pending marker on the queue, only the latest
    credentials/payload are kept in a hash — no second list item is pushed.
    """
    payload = dict(job)
    if not payload.get("job_id"):
        payload["job_id"] = new_job_id()
    account_id = str(payload["trading_account_id"])
    redis_client.hset(_latest_key(queue_key), account_id, json.dumps(payload))
    added = redis_client.sadd(_pending_key(queue_key), account_id)
    if added:
        marker = {
            "job_id": payload["job_id"],
            "trading_account_id": account_id,
        }
        redis_client.rpush(queue_key, json.dumps(marker))
    return payload["job_id"]


def queue_depth(redis_client, queue_key: str) -> dict:
    return {
        "pending_jobs": redis_client.llen(queue_key),
        "pending_accounts": redis_client.scard(_pending_key(queue_key)),
    }


def claim_job(redis_client, queue_key: str, timeout: int = 5):
    """Claim the next job marker and resolve the latest coalesced payload."""
    item = redis_client.blpop(queue_key, timeout=timeout)
    if not item:
        return None
    _key, raw = item
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    marker = json.loads(raw)
    account_id = str(marker["trading_account_id"])
    latest_raw = redis_client.hget(_latest_key(queue_key), account_id)
    redis_client.srem(_pending_key(queue_key), account_id)
    redis_client.hdel(_latest_key(queue_key), account_id)
    if not latest_raw:
        return marker
    if isinstance(latest_raw, bytes):
        latest_raw = latest_raw.decode("utf-8")
    return json.loads(latest_raw)
