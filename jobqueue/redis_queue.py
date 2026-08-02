import json
import uuid

RESULT_TTL_SECONDS = 300


def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:16]}"


def _pending_key(queue_key: str) -> str:
    return f"{queue_key}:pending"


def _latest_key(queue_key: str) -> str:
    return f"{queue_key}:latest"


def _result_key(queue_key: str, job_id: str) -> str:
    return f"{queue_key}:result:{job_id}"


def _coalesce_id(job: dict) -> str:
    account_id = str(job["trading_account_id"])
    job_type = str(job.get("job_type") or "sync")
    return f"{account_id}:{job_type}"


def set_job_result(redis_client, queue_key: str, job_id: str, result: dict, *, ttl_seconds: int = RESULT_TTL_SECONDS) -> None:
    redis_client.setex(_result_key(queue_key, job_id), ttl_seconds, json.dumps(result))


def get_job_result(redis_client, queue_key: str, job_id: str) -> dict | None:
    raw = redis_client.get(_result_key(queue_key, job_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def enqueue_job(redis_client, queue_key: str, job: dict) -> str:
    """Enqueue a job, coalescing duplicates for the same account+job_type.

    If an account already has a pending marker for that job type, only the latest
    credentials/payload are kept in a hash — no second list item is pushed.
    """
    payload = dict(job)
    if not payload.get("job_id"):
        payload["job_id"] = new_job_id()
    payload["job_type"] = str(payload.get("job_type") or "sync")
    account_id = str(payload["trading_account_id"])
    coalesce_id = _coalesce_id(payload)
    redis_client.hset(_latest_key(queue_key), coalesce_id, json.dumps(payload))
    added = redis_client.sadd(_pending_key(queue_key), coalesce_id)
    if added:
        marker = {
            "job_id": payload["job_id"],
            "trading_account_id": account_id,
            "job_type": payload["job_type"],
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
    coalesce_id = _coalesce_id(marker)
    latest_raw = redis_client.hget(_latest_key(queue_key), coalesce_id)
    redis_client.srem(_pending_key(queue_key), coalesce_id)
    redis_client.hdel(_latest_key(queue_key), coalesce_id)
    if not latest_raw:
        return marker
    if isinstance(latest_raw, bytes):
        latest_raw = latest_raw.decode("utf-8")
    return json.loads(latest_raw)
