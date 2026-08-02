import html
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.auth import token_ok
from jobqueue.redis_lock import lock_held
from jobqueue.redis_queue import enqueue_job, get_job_result, queue_depth, set_job_result

router = APIRouter()


class SyncJobBody(BaseModel):
    trading_account_id: str = Field(min_length=1)
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)
    server: str = Field(min_length=1)


class VerifyJobBody(BaseModel):
    trading_account_id: str = Field(min_length=1)
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)
    server: str = Field(min_length=1)


def _system_status(request: Request) -> dict:
    settings = request.app.state.settings
    redis_client = request.app.state.redis
    redis_ok = True
    redis_error = None
    depth = {"pending_jobs": None, "pending_accounts": None}
    mt5_locked = None
    try:
        redis_client.ping()
        depth = queue_depth(redis_client, settings.redis_queue_key)
        mt5_locked = lock_held(redis_client, settings.mt5_lock_key)
    except Exception as exc:
        redis_ok = False
        redis_error = str(exc)
    return {
        "ok": redis_ok,
        "time": datetime.now(timezone.utc).isoformat(),
        "redis": {"ok": redis_ok, "error": redis_error},
        "queue": depth,
        "mt5_lock_held": mt5_locked,
        "worker_pool_size": settings.worker_pool_size,
    }


@router.get("/health")
def health(request: Request, response: Response):
    status = _system_status(request)
    if not status["ok"]:
        response.status_code = 503
    return status


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    return _render_dashboard(_system_status(request))


@router.post("/jobs/sync", status_code=202)
def create_sync_job(
    body: SyncJobBody,
    request: Request,
    x_bridge_token: str | None = Header(default=None),
):
    settings = request.app.state.settings
    if not token_ok(x_bridge_token or "", settings.bridge_service_token):
        raise HTTPException(status_code=401, detail="Invalid bridge token")
    payload = body.model_dump()
    payload["job_type"] = "sync"
    job_id = enqueue_job(
        request.app.state.redis,
        settings.redis_queue_key,
        payload,
    )
    return {"job_id": job_id}


@router.post("/jobs/verify", status_code=202)
def create_verify_job(
    body: VerifyJobBody,
    request: Request,
    x_bridge_token: str | None = Header(default=None),
):
    settings = request.app.state.settings
    if not token_ok(x_bridge_token or "", settings.bridge_service_token):
        raise HTTPException(status_code=401, detail="Invalid bridge token")
    payload = body.model_dump()
    payload["job_type"] = "verify"
    job_id = enqueue_job(
        request.app.state.redis,
        settings.redis_queue_key,
        payload,
    )
    set_job_result(
        request.app.state.redis,
        settings.redis_queue_key,
        job_id,
        {"status": "pending", "trading_account_id": payload["trading_account_id"]},
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}/result")
def job_result(
    job_id: str,
    request: Request,
    x_bridge_token: str | None = Header(default=None),
):
    settings = request.app.state.settings
    if not token_ok(x_bridge_token or "", settings.bridge_service_token):
        raise HTTPException(status_code=401, detail="Invalid bridge token")
    result = get_job_result(request.app.state.redis, settings.redis_queue_key, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return result


def _badge(ok: bool | None) -> str:
    if ok is None:
        return '<span class="badge unknown">UNKNOWN</span>'
    return '<span class="badge ok">OK</span>' if ok else '<span class="badge down">DOWN</span>'


def _render_dashboard(status: dict) -> str:
    redis_ok = status["redis"]["ok"]
    redis_error = status["redis"]["error"]
    queue = status["queue"]
    overall_ok = status["ok"]
    mt5_locked = status["mt5_lock_held"]

    error_row = f'<p class="error">{html.escape(redis_error)}</p>' if redis_error else ""
    pending_jobs = queue["pending_jobs"] if queue["pending_jobs"] is not None else "—"
    pending_accounts = queue["pending_accounts"] if queue["pending_accounts"] is not None else "—"
    lock_text = "—" if mt5_locked is None else ("Held" if mt5_locked else "Free")

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
      <div class="label">MT5 Lock</div>
      <div class="value">{lock_text}</div>
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
