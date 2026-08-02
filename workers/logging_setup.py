import logging
import logging.handlers
import os

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


def get_logger(name: str) -> logging.Logger:
    """Logger that writes to logs/<name>.log (rotating) and stdout.

    Give each OS process its own name (e.g. a per-worker-id suffix) — a
    rotating file handler isn't safe to share across multiple processes.
    """
    logger = logging.getLogger(f"bridge.{name}")
    if logger.handlers:
        return logger
    os.makedirs(_LOG_DIR, exist_ok=True)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(_LOG_DIR, f"{name}.log"),
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    logger.propagate = False
    return logger
