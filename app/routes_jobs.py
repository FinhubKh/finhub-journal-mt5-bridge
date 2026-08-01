from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import token_ok
from jobqueue.redis_queue import enqueue_job

router = APIRouter()


class SyncJobBody(BaseModel):
    trading_account_id: str = Field(min_length=1)
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)
    server: str = Field(min_length=1)


@router.get("/health")
def health():
    return {"ok": True}


@router.post("/jobs/sync", status_code=202)
def create_sync_job(
    body: SyncJobBody,
    request: Request,
    x_bridge_token: str | None = Header(default=None),
):
    settings = request.app.state.settings
    if not token_ok(x_bridge_token or "", settings.bridge_service_token):
        raise HTTPException(status_code=401, detail="Invalid bridge token")
    job_id = enqueue_job(
        request.app.state.redis,
        settings.redis_queue_key,
        body.model_dump(),
    )
    return {"job_id": job_id}
