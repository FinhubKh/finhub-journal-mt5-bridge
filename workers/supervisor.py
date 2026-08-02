import os
import subprocess
import sys
import time

from app.config import get_settings
from workers.logging_setup import get_logger

log = get_logger("supervisor")


def should_requeue(job: dict) -> bool:
    return int(job.get("attempt") or 0) < 1


def mark_requeue(job: dict) -> dict:
    j = dict(job)
    j["attempt"] = int(j.get("attempt") or 0) + 1
    return j


def _spawn_worker(worker_id: int) -> subprocess.Popen:
    env = dict(os.environ, WORKER_ID=str(worker_id))
    return subprocess.Popen([sys.executable, "-m", "workers.run_worker"], env=env)


def main() -> None:
    settings = get_settings()
    pool_size = int(os.environ.get("WORKER_POOL_SIZE", settings.worker_pool_size))
    workers: list[subprocess.Popen] = []
    log.info("Supervisor starting, pool_size=%d", pool_size)
    try:
        while True:
            while len(workers) < pool_size:
                worker_id = len(workers)
                proc = _spawn_worker(worker_id)
                workers.append(proc)
                log.info("Started worker-%d (pid=%s)", worker_id, proc.pid)
            for i, proc in enumerate(workers):
                if proc.poll() is not None:
                    log.warning(
                        "worker-%d (pid=%s) exited with code %s — restarting",
                        i,
                        proc.pid,
                        proc.returncode,
                    )
                    workers[i] = _spawn_worker(i)
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("Supervisor stopping")
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
