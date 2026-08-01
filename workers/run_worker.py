import httpx
import redis

from app.config import get_settings
from jobqueue.redis_queue import claim_job, enqueue_job
from workers.mt5_worker import run_sync_job


def main() -> None:
    from workers.mt5_adapter import MetaTrader5Adapter
    from workers.supervisor import mark_requeue, should_requeue

    settings = get_settings()
    client = redis.from_url(settings.redis_url)
    mt5 = MetaTrader5Adapter()

    with httpx.Client() as http:
        while True:
            job = claim_job(client, settings.redis_queue_key, timeout=5)
            if not job:
                continue
            try:
                run_sync_job(
                    job=job,
                    mt5=mt5,
                    http=http,
                    journal_url=settings.journal_bridge_sync_url,
                    token=settings.bridge_service_token,
                    terminal_path=settings.mt5_terminal_path,
                    lookback_days=settings.history_lookback_days,
                )
            except Exception:
                if should_requeue(job):
                    enqueue_job(client, settings.redis_queue_key, mark_requeue(job))


if __name__ == "__main__":
    main()
