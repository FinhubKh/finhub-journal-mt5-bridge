import html
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.auth import token_ok
from jobqueue.redis_lock import lock_held
from jobqueue.redis_queue import (
    enqueue_job,
    get_job_result,
    pending_queue_meta,
    queue_depth,
    set_job_result,
)
from workers.supabase_client import fetch_investor_credentials, set_sync_stage
from workers.terminal_lock import lock_key_for_terminal
from workers.terminal_map import list_terminal_slots

router = APIRouter()


class SyncJobBody(BaseModel):
    trading_account_id: str = Field(min_length=1)
    # Optional legacy fields — ignored for storage; workers load creds from Supabase.
    login: str | None = None
    password: str | None = None
    server: str | None = None


class VerifyJobBody(BaseModel):
    trading_account_id: str = Field(min_length=1)
    login: str | None = None
    password: str | None = None
    server: str | None = None


def _workers_alive(redis_client, heartbeat_key: str) -> bool | None:
    try:
        return bool(redis_client.get(heartbeat_key))
    except Exception:
        return None


def _slot_lock_status(redis_client, settings) -> dict:
    """Report which configured terminal slots currently hold a lock."""
    mt5_slots = list_terminal_slots(
        default_path=settings.mt5_terminal_path or "",
        map_path=settings.mt5_terminal_map_path or None,
    )
    mt4_slots = list_terminal_slots(
        default_path=settings.mt4_terminal_path or "",
        map_path=settings.mt4_terminal_map_path or None,
    )
    mt5 = []
    for path in mt5_slots:
        key = lock_key_for_terminal("mt5", path, fallback_key=settings.mt5_lock_key)
        held = False
        try:
            held = lock_held(redis_client, key)
        except Exception:
            held = False
        mt5.append({"path": path, "lock_key": key, "held": held})
    mt4 = []
    for path in mt4_slots:
        key = lock_key_for_terminal("mt4", path, fallback_key=settings.mt4_lock_key)
        held = False
        try:
            held = lock_held(redis_client, key)
        except Exception:
            held = False
        mt4.append({"path": path, "lock_key": key, "held": held})
    legacy_mt5 = None
    legacy_mt4 = None
    try:
        legacy_mt5 = lock_held(redis_client, settings.mt5_lock_key)
    except Exception:
        legacy_mt5 = None
    try:
        legacy_mt4 = lock_held(redis_client, settings.mt4_lock_key)
    except Exception:
        legacy_mt4 = None
    return {
        "mt5_slots": mt5,
        "mt4_slots": mt4,
        "mt5_any_held": any(s["held"] for s in mt5) or bool(legacy_mt5),
        "mt4_any_held": any(s["held"] for s in mt4) or bool(legacy_mt4),
        "legacy_mt5_lock_held": legacy_mt5,
        "legacy_mt4_lock_held": legacy_mt4,
    }


def _system_status(request: Request) -> dict:
    settings = request.app.state.settings
    redis_client = request.app.state.redis
    redis_ok = True
    redis_error = None
    depth = {"pending_jobs": None, "pending_accounts": None, "processing_jobs": None}
    mt5_locked = None
    workers_alive = None
    terminal_locks = {
        "mt5_slots": [],
        "mt4_slots": [],
        "mt5_any_held": None,
        "mt4_any_held": None,
        "legacy_mt5_lock_held": None,
        "legacy_mt4_lock_held": None,
    }
    try:
        redis_client.ping()
        depth = queue_depth(redis_client, settings.redis_queue_key)
        terminal_locks = _slot_lock_status(redis_client, settings)
        mt5_locked = terminal_locks.get("mt5_any_held")
        workers_alive = _workers_alive(redis_client, settings.worker_heartbeat_key)
    except Exception as exc:
        redis_ok = False
        redis_error = str(exc)
    overall_ok = redis_ok
    return {
        "ok": overall_ok,
        "time": datetime.now(timezone.utc).isoformat(),
        "redis": {"ok": redis_ok, "error": redis_error},
        "queue": depth,
        "mt5_lock_held": mt5_locked,
        "terminal_locks": terminal_locks,
        "workers_alive": workers_alive,
        "worker_pool_size": settings.worker_pool_size,
    }


def _require_bridge_token(request: Request, x_bridge_token: str | None) -> None:
    settings = request.app.state.settings
    if not token_ok(x_bridge_token or "", settings.bridge_service_token):
        raise HTTPException(status_code=401, detail="Invalid bridge token")


def _account_prefers_front_queue(settings, trading_account_id: str) -> bool:
    """Incremental syncs (cashflow backfill already done) jump ahead of first syncs."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return False
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            creds = fetch_investor_credentials(
                client,
                supabase_url=settings.supabase_url,
                service_key=settings.supabase_service_role_key,
                trading_account_id=trading_account_id,
            )
        return bool(creds and creds.get("cashflow_backfill_done_at"))
    except Exception:
        return False


def _mark_job_queued(
    *,
    settings,
    redis_client,
    trading_account_id: str,
    job_id: str,
    job_type: str = "sync",
) -> dict:
    meta = pending_queue_meta(
        redis_client,
        settings.redis_queue_key,
        job_id,
        trading_account_id=trading_account_id,
        job_type=job_type,
    )
    ahead = meta.get("queue_ahead")
    stage = f"queued:{ahead}" if ahead is not None else "queued"
    result = {
        "status": "pending",
        "trading_account_id": trading_account_id,
        **meta,
    }
    set_job_result(redis_client, settings.redis_queue_key, job_id, result)
    if settings.supabase_url and settings.supabase_service_role_key:
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                set_sync_stage(
                    client,
                    supabase_url=settings.supabase_url,
                    service_key=settings.supabase_service_role_key,
                    trading_account_id=trading_account_id,
                    stage=stage,
                )
        except Exception:
            pass
    return result


@router.get("/health")
def health(request: Request, response: Response):
    status = _system_status(request)
    if not status["ok"]:
        response.status_code = 503
    return status


@router.get("/", response_class=HTMLResponse)
def root(
    request: Request,
    x_bridge_token: str | None = Header(default=None),
    token: str | None = None,
):
    settings = request.app.state.settings
    provided = x_bridge_token or token or ""
    if not token_ok(provided, settings.bridge_service_token):
        raise HTTPException(status_code=401, detail="Invalid bridge token")
    return _render_dashboard(_system_status(request))


@router.post("/jobs/sync", status_code=202)
def create_sync_job(
    body: SyncJobBody,
    request: Request,
    x_bridge_token: str | None = Header(default=None),
):
    _require_bridge_token(request, x_bridge_token)
    settings = request.app.state.settings
    # Never persist investor passwords in the Redis queue.
    payload = {
        "trading_account_id": body.trading_account_id,
        "job_type": "sync",
    }
    if _account_prefers_front_queue(settings, body.trading_account_id):
        payload["priority"] = "front"
    job_id = enqueue_job(
        request.app.state.redis,
        settings.redis_queue_key,
        payload,
    )
    pending = _mark_job_queued(
        settings=settings,
        redis_client=request.app.state.redis,
        trading_account_id=payload["trading_account_id"],
        job_id=job_id,
        job_type="sync",
    )
    return {"job_id": job_id, **{k: pending[k] for k in ("queue_ahead", "queue_position") if k in pending}}


@router.post("/jobs/verify", status_code=202)
def create_verify_job(
    body: VerifyJobBody,
    request: Request,
    x_bridge_token: str | None = Header(default=None),
):
    _require_bridge_token(request, x_bridge_token)
    settings = request.app.state.settings
    payload = {
        "trading_account_id": body.trading_account_id,
        "job_type": "verify",
    }
    job_id = enqueue_job(
        request.app.state.redis,
        settings.redis_queue_key,
        payload,
    )
    pending = _mark_job_queued(
        settings=settings,
        redis_client=request.app.state.redis,
        trading_account_id=payload["trading_account_id"],
        job_id=job_id,
        job_type="verify",
    )
    return {"job_id": job_id, **{k: pending[k] for k in ("queue_ahead", "queue_position") if k in pending}}


@router.get("/jobs/{job_id}/result")
def job_result(
    job_id: str,
    request: Request,
    x_bridge_token: str | None = Header(default=None),
):
    _require_bridge_token(request, x_bridge_token)
    settings = request.app.state.settings
    result = get_job_result(request.app.state.redis, settings.redis_queue_key, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    if result.get("status") == "pending":
        account_id = result.get("trading_account_id")
        meta = pending_queue_meta(
            request.app.state.redis,
            settings.redis_queue_key,
            job_id,
            trading_account_id=str(account_id) if account_id else None,
            job_type=str(result.get("job_type") or "sync"),
        )
        if meta:
            result = {**result, **meta}
            set_job_result(request.app.state.redis, settings.redis_queue_key, job_id, result)
            ahead = meta.get("queue_ahead")
            if account_id is not None and ahead is not None and settings.supabase_url:
                try:
                    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                        set_sync_stage(
                            client,
                            supabase_url=settings.supabase_url,
                            service_key=settings.supabase_service_role_key,
                            trading_account_id=str(account_id),
                            stage=f"queued:{ahead}",
                        )
                except Exception:
                    pass
    return result


def _badge(ok: bool | None) -> str:
    if ok is None:
        return '<span class="badge unknown">UNKNOWN</span>'
    return '<span class="badge ok">OK</span>' if ok else '<span class="badge down">DOWN</span>'


def _render_dashboard(status: dict) -> str:
    redis_ok = status["redis"]["ok"]
    redis_error = status["redis"]["error"]
    queue = status["queue"]
    workers_alive = status.get("workers_alive")
    overall_ok = redis_ok and (workers_alive is not False)
    mt5_locked = status["mt5_lock_held"]
    locks = status.get("terminal_locks") or {}
    mt5_held = sum(1 for s in (locks.get("mt5_slots") or []) if s.get("held"))
    mt4_held = sum(1 for s in (locks.get("mt4_slots") or []) if s.get("held"))
    mt5_total = len(locks.get("mt5_slots") or [])
    mt4_total = len(locks.get("mt4_slots") or [])

    error_row = f'<p class="error">{html.escape(redis_error)}</p>' if redis_error else ""
    pending_jobs = queue["pending_jobs"] if queue["pending_jobs"] is not None else "—"
    pending_accounts = queue["pending_accounts"] if queue["pending_accounts"] is not None else "—"
    processing_jobs = queue.get("processing_jobs")
    processing_text = "—" if processing_jobs is None else processing_jobs
    lock_text = "—" if mt5_locked is None else ("Held" if mt5_locked else "Free")
    workers_text = (
        "—" if workers_alive is None else ("Alive" if workers_alive else "No heartbeat")
    )
    slots_text = f"MT5 {mt5_held}/{mt5_total or '—'} · MT4 {mt4_held}/{mt4_total or '—'}"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FinHub MT5 Bridge</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 2.5rem 1.5rem;
    background: #0b0e14;
    color: #e6e9ef;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    display: flex;
    justify-content: center;
  }}
  .wrap {{ width: 100%; max-width: 640px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; }}
  .subtitle {{ color: #8b93a7; margin: 0 0 1.75rem; font-size: 0.9rem; }}
  .overall {{
    display: flex; align-items: center; gap: 0.75rem;
    padding: 1rem 1.25rem; border-radius: 10px; margin-bottom: 1.5rem;
    background: {"#132a1c" if overall_ok else "#2a1414"};
    border: 1px solid {"#1f5c37" if overall_ok else "#6b2323"};
  }}
  .overall .dot {{
    width: 12px; height: 12px; border-radius: 50%;
    background: {"#3fcf6e" if overall_ok else "#ef4444"};
  }}
  .overall strong {{ font-size: 1.05rem; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }}
  .card {{
    background: #131722; border: 1px solid #232837; border-radius: 10px;
    padding: 1rem 1.1rem;
  }}
  .card .label {{ color: #8b93a7; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.4rem; }}
  .card .value {{ font-size: 1.3rem; font-weight: 600; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.55rem; border-radius: 6px; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.03em; }}
  .badge.ok {{ background: #16351f; color: #3fcf6e; }}
  .badge.down {{ background: #3a1414; color: #f87171; }}
  .badge.unknown {{ background: #2a2f3d; color: #8b93a7; }}
  .error {{ color: #f87171; font-size: 0.85rem; margin-top: 1rem; word-break: break-word; }}
  .footer {{ color: #565d6e; font-size: 0.78rem; margin-top: 1.75rem; }}
  a {{ color: #8b93a7; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>FinHub MT5 Bridge</h1>
  <p class="subtitle">Journal sync bridge status</p>

  <div class="overall">
    <span class="dot"></span>
    <strong>{"System Healthy" if overall_ok else "System Degraded"}</strong>
  </div>

  <div class="grid">
    <div class="card">
      <div class="label">Redis</div>
      <div class="value">{_badge(redis_ok)}</div>
    </div>
    <div class="card">
      <div class="label">Workers</div>
      <div class="value">{workers_text}</div>
    </div>
    <div class="card">
      <div class="label">MT5 Lock</div>
      <div class="value">{lock_text}</div>
    </div>
    <div class="card">
      <div class="label">Terminal Slots Held</div>
      <div class="value" style="font-size:1rem;">{slots_text}</div>
    </div>
    <div class="card">
      <div class="label">Pending Jobs</div>
      <div class="value">{pending_jobs}</div>
    </div>
    <div class="card">
      <div class="label">Pending Accounts</div>
      <div class="value">{pending_accounts}</div>
    </div>
    <div class="card">
      <div class="label">Processing</div>
      <div class="value">{processing_text}</div>
    </div>
    <div class="card">
      <div class="label">Worker Pool Size</div>
      <div class="value">{status["worker_pool_size"]}</div>
    </div>
    <div class="card">
      <div class="label">Server Time (UTC)</div>
      <div class="value" style="font-size:0.95rem;">{status["time"][11:19]}</div>
    </div>
  </div>

  {error_row}

  <p class="footer">Auto-refreshes every 10s &middot; <a href="/health">/health</a> for JSON</p>
</div>
</body>
</html>"""
