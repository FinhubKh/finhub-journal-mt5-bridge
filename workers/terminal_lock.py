"""Per-terminal Redis lock keys so parallel portable installs can run together."""

from __future__ import annotations

import hashlib
import os


def normalize_terminal_path(path: str) -> str:
    text = (path or "").strip()
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.abspath(text))
    except Exception:
        return os.path.normcase(text)


def lock_key_for_terminal(platform: str, terminal_path: str, *, fallback_key: str) -> str:
    """Derive a lock key from the terminal binary path.

    Different portable installs get different locks (true parallelism).
    Empty/missing path falls back to the legacy global key.
    """
    normalized = normalize_terminal_path(terminal_path)
    if not normalized:
        return fallback_key
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    prefix = "mt4" if str(platform or "").lower() == "mt4" else "mt5"
    return f"finhubkh:{prefix}:lock:{digest}"
