import redis as redis_lib
from fastapi import FastAPI

from app.config import get_settings
from app.routes_jobs import router


def create_app(redis_client=None, settings_overrides: dict | None = None) -> FastAPI:
    settings = get_settings()
    if settings_overrides:
        settings = settings.model_copy(update=settings_overrides)
    app = FastAPI(title="finhubkh-mt5-bridge")
    app.state.settings = settings
    app.state.redis = redis_client or redis_lib.Redis.from_url(
        settings.redis_url, decode_responses=True
    )
    app.include_router(router)
    return app


app = create_app()
