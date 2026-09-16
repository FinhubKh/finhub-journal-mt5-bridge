"""Resolve which MetaTrader terminal binary to use for a broker server."""

from __future__ import annotations

import json
from pathlib import Path

from jobqueue.redis_lock import lock_held
from workers.terminal_lock import lock_key_for_terminal

_map_cache: dict[str, tuple[float, dict]] = {}


def _default_map_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "mt5_terminal_map.json"


def _load_map(map_path: str) -> dict:
    path = Path(map_path)
    try:
        mtime = path.stat().st_mtime if path.is_file() else -1.0
    except OSError:
        mtime = -1.0

    cached = _map_cache.get(map_path)
    if cached and cached[0] == mtime:
        return cached[1]

    if not path.is_file():
        data = {"default": "", "prefixes": {}, "slots": []}
        _map_cache[map_path] = (mtime, data)
        return data

    raw = json.loads(path.read_text(encoding="utf-8"))
    prefixes = {
        str(k): str(v)
        for k, v in (raw.get("prefixes") or {}).items()
        if not str(k).startswith("_") and v
    }
    slots = [str(p) for p in (raw.get("slots") or []) if str(p).strip()]
    data = {
        "default": str(raw.get("default") or ""),
        "prefixes": prefixes,
        "slots": slots,
    }
    _map_cache[map_path] = (mtime, data)
    return data


def list_terminal_slots(*, default_path: str, map_path: str | None = None) -> list[str]:
    """Installed terminal paths from the map's slots list (plus default if present)."""
    cfg = _load_map(str(map_path or _default_map_path()))
    seen: set[str] = set()
    out: list[str] = []
    for path in list(cfg.get("slots") or []) + [cfg.get("default") or "", default_path or ""]:
        text = str(path or "").strip()
        if not text or text in seen:
            continue
        if Path(text).is_file():
            seen.add(text)
            out.append(text)
    return out


def resolve_terminal_path(
    server: str,
    *,
    default_path: str,
    map_path: str | None = None,
) -> str:
    """Pick a terminal binary for this server via longest prefix match."""
    server = (server or "").strip()
    cfg = _load_map(str(map_path or _default_map_path()))
    prefixes = cfg.get("prefixes") or {}
    best_prefix = ""
    best_path = ""
    for prefix, path in prefixes.items():
        if server.startswith(prefix) and len(prefix) > len(best_prefix):
            # Prefer an installed terminal; skip missing paths so we fall through.
            if Path(path).is_file():
                best_prefix = prefix
                best_path = path
    if best_path:
        return best_path
    fallback = cfg.get("default") or default_path
    if fallback and Path(fallback).is_file():
        return fallback
    return default_path or fallback


def pick_terminal_path(
    server: str,
    *,
    platform: str,
    default_path: str,
    map_path: str | None = None,
    redis_client=None,
    fallback_lock_key: str = "",
) -> str:
    """Prefer a free slot so concurrent syncs use different portable installs.

    Order: broker-mapped path if free → first free pool slot → broker/default path.
    """
    preferred = resolve_terminal_path(
        server,
        default_path=default_path,
        map_path=map_path,
    )
    slots = list_terminal_slots(default_path=default_path, map_path=map_path)
    candidates: list[str] = []
    for path in [preferred, *slots]:
        text = str(path or "").strip()
        if text and text not in candidates:
            candidates.append(text)
    if not candidates:
        return preferred or default_path or ""

    if redis_client is None or not fallback_lock_key:
        return preferred or candidates[0]

    def _free(path: str) -> bool:
        key = lock_key_for_terminal(platform, path, fallback_key=fallback_lock_key)
        try:
            return not lock_held(redis_client, key)
        except Exception:
            return False

    if preferred and _free(preferred):
        return preferred
    for path in candidates:
        if _free(path):
            return path
    return preferred or candidates[0]
