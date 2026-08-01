import os
import subprocess
import sys
import time

from app.config import get_settings


def should_requeue(job: dict) -> bool:
    return int(job.get("attempt") or 0) < 1


def mark_requeue(job: dict) -> dict:
    j = dict(job)
    j["attempt"] = int(j.get("attempt") or 0) + 1
    return j


def _spawn_worker() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "workers.run_worker"])


def main() -> None:
    settings = get_settings()
    pool_size = int(os.environ.get("WORKER_POOL_SIZE", settings.worker_pool_size))
    workers: list[subprocess.Popen] = []
    try:
        while True:
            while len(workers) < pool_size:
                workers.append(_spawn_worker())
            for i, proc in enumerate(workers):
                if proc.poll() is not None:
                    workers[i] = _spawn_worker()
            time.sleep(0.5)
    except KeyboardInterrupt:
        for proc in workers:
            if proc.poll() is None:
                proc.terminate()
        for proc in workers:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
