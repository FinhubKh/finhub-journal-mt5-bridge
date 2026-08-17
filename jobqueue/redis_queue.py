import json
import time
import uuid

RESULT_TTL_SECONDS = 3600
SECRET_TTL_SECONDS = 900
STALE_PROCESSING_SECONDS = 900


def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:16]}"


def _pending_key(queue_key: str) -> str:
    return f"{queue_key}:pending"


def _latest_key(queue_key: str) -> str:
    return f"{queue_key}:latest"


def _result_key(queue_key: str, job_id: str) -> str:
    return f"{queue_key}:result:{job_id}"


def _secret_key(queue_key: str, job_id: str) -> str:
    return f"{queue_key}:secret:{job_id}"


def _processing_key(queue_key: str) -> str:
    return f"{queue_key}:processing"


def _processing_meta_key(queue_key: str) -> str:
    return f"{queue_key}:processing_meta"


def _coalesce_id(job: dict) -> str:
    account_id = str(job["trading_account_id"])
    job_type = str(job.get("job_type") or "sync")
    return f"{account_id}:{job_type}"


def _public_payload(job: dict) -> dict:
    """Queue payload without secrets — passwords live in a short-TTL side key."""
    payload = dict(job)
    payload.pop("password", None)
    return payload


def set_job_result(
    redis_client,
    queue_key: str,
    job_id: str,
    result: dict,
    *,
    ttl_seconds: int = RESULT_TTL_SECONDS,
) -> None:
    redis_client.setex(_result_key(queue_key, job_id), ttl_seconds, json.dumps(result))


def get_job_result(redis_client, queue_key: str, job_id: str) -> dict | None:
    raw = redis_client.get(_result_key(queue_key, job_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def _store_secret(redis_client, queue_key: str, job_id: str, password: str | None) -> None:
    if not password:
        return
    redis_client.setex(_secret_key(queue_key, job_id), SECRET_TTL_SECONDS, password)


def _take_secret(redis_client, queue_key: str, job_id: str) -> str | None:
    key = _secret_key(queue_key, job_id)
    raw = redis_client.get(key)
    try:
        redis_client.delete(key)
    except Exception:
        pass
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)


def enqueue_job(redis_client, queue_key: str, job: dict) -> str:
    """Enqueue a job, coalescing duplicates for the same account+job_type.

    Passwords are never stored in the durable latest-hash — only in a short-TTL
    secret key keyed by job_id (optional; workers can also load from Supabase).
    """
    payload = dict(job)
    if not payload.get("job_id"):
        payload["job_id"] = new_job_id()
    payload["job_type"] = str(payload.get("job_type") or "sync")
    password = payload.pop("password", None)
    account_id = str(payload["trading_account_id"])
    coalesce_id = _coalesce_id(payload)

    # Drop any prior secret for this coalesce slot when credentials refresh.
    prior_raw = redis_client.hget(_latest_key(queue_key), coalesce_id)
    if prior_raw:
        if isinstance(prior_raw, bytes):
            prior_raw = prior_raw.decode("utf-8")
        try:
            prior = json.loads(prior_raw)
            prior_id = prior.get("job_id")
            if prior_id and prior_id != payload["job_id"]:
                redis_client.delete(_secret_key(queue_key, prior_id))
        except Exception:
            pass

    redis_client.hset(_latest_key(queue_key), coalesce_id, json.dumps(_public_payload(payload)))
    _store_secret(redis_client, queue_key, payload["job_id"], password)

    added = redis_client.sadd(_pending_key(queue_key), coalesce_id)
    if added:
        marker = {
            "job_id": payload["job_id"],
            "trading_account_id": account_id,
            "job_type": payload["job_type"],
        }
        raw_marker = json.dumps(marker)
        # Login checks jump the queue so Connect doesn't wait behind a full sync.
        if payload["job_type"] == "verify":
            redis_client.lpush(queue_key, raw_marker)
        else:
            redis_client.rpush(queue_key, raw_marker)
    return payload["job_id"]


def queue_depth(redis_client, queue_key: str) -> dict:
    return {
        "pending_jobs": redis_client.llen(queue_key),
        "pending_accounts": redis_client.scard(_pending_key(queue_key)),
        "processing_jobs": redis_client.llen(_processing_key(queue_key)),
    }


def claim_job(redis_client, queue_key: str, timeout: int = 5):
    """Claim the next job into a processing list (crash-recoverable) and resolve payload.

    Pops from the LEFT (same as blpop) so verify lpush priority and sync rpush
    FIFO order stay correct. Never use brpoplpush here — that pops the RIGHT.
    """
    processing = _processing_key(queue_key)
    item = None
    blmove = getattr(redis_client, "blmove", None)
    if callable(blmove):
        # Atomic LEFT→RIGHT move into the processing list.
        item = blmove(queue_key, processing, float(timeout), "LEFT", "RIGHT")
    else:
        popped = redis_client.blpop(queue_key, timeout=timeout)
        if not popped:
            return None
        _key, item = popped
        redis_client.rpush(processing, item)

    if not item:
        return None
    if isinstance(item, bytes):
        item = item.decode("utf-8")
    marker = json.loads(item)
    coalesce_id = _coalesce_id(marker)
    latest_raw = redis_client.hget(_latest_key(queue_key), coalesce_id)
    redis_client.srem(_pending_key(queue_key), coalesce_id)
    redis_client.hdel(_latest_key(queue_key), coalesce_id)

    if latest_raw:
        if isinstance(latest_raw, bytes):
            latest_raw = latest_raw.decode("utf-8")
        job = json.loads(latest_raw)
    else:
        job = dict(marker)

    job_id = str(job.get("job_id") or marker.get("job_id") or "")
    password = _take_secret(redis_client, queue_key, job_id) if job_id else None
    if password:
        job["password"] = password

    if job_id:
        redis_client.hset(
            _processing_meta_key(queue_key),
            job_id,
            json.dumps({"claimed_at": time.time(), "marker": marker, "job": _public_payload(job)}),
        )
    return job


def ack_job(redis_client, queue_key: str, job: dict) -> None:
    """Remove a finished job from the processing list."""
    job_id = str(job.get("job_id") or "")
    marker = {
        "job_id": job_id,
        "trading_account_id": job.get("trading_account_id"),
        "job_type": str(job.get("job_type") or "sync"),
    }
    raw_marker = json.dumps(marker)
    processing = _processing_key(queue_key)
    try:
        # Remove one matching marker (exact JSON from claim).
        if hasattr(redis_client, "lrem"):
            removed = redis_client.lrem(processing, 1, raw_marker)
            if not removed:
                # Marker job_id may differ from coalesced latest — scan.
                items = redis_client.lrange(processing, 0, -1) if hasattr(redis_client, "lrange") else []
                for raw in items:
                    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    try:
                        m = json.loads(text)
                    except Exception:
                        continue
                    if str(m.get("job_id")) == job_id or (
                        str(m.get("trading_account_id")) == str(job.get("trading_account_id"))
                        and str(m.get("job_type") or "sync") == marker["job_type"]
                    ):
                        redis_client.lrem(processing, 1, raw)
                        break
        if job_id:
            redis_client.hdel(_processing_meta_key(queue_key), job_id)
    except Exception:
        pass


def recover_stale_processing(
    redis_client,
    queue_key: str,
    *,
    max_age_seconds: int = STALE_PROCESSING_SECONDS,
) -> int:
    """Re-queue jobs stuck in processing (worker crash). Returns requeued count."""
    meta_key = _processing_meta_key(queue_key)
    processing = _processing_key(queue_key)
    now = time.time()
    requeued = 0
    try:
        all_meta = redis_client.hgetall(meta_key) if hasattr(redis_client, "hgetall") else {}
    except Exception:
        return 0

    for field, raw in (all_meta or {}).items():
        job_id = field.decode("utf-8") if isinstance(field, bytes) else str(field)
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            meta = json.loads(text)
        except Exception:
            continue
        claimed_at = float(meta.get("claimed_at") or 0)
        if claimed_at and (now - claimed_at) < max_age_seconds:
            continue
        job = meta.get("job") or {}
        job["job_id"] = job.get("job_id") or job_id
        # Drop processing entries then re-enqueue.
        try:
            marker = meta.get("marker") or {
                "job_id": job_id,
                "trading_account_id": job.get("trading_account_id"),
                "job_type": job.get("job_type") or "sync",
            }
            raw_marker = json.dumps(marker)
            if hasattr(redis_client, "lrem"):
                redis_client.lrem(processing, 1, raw_marker)
            redis_client.hdel(meta_key, job_id)
        except Exception:
            pass
        job.pop("password", None)
        enqueue_job(redis_client, queue_key, job)
        requeued += 1
    return requeued
