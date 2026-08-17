"""Resolve which MetaTrader 5 terminal binary to use for a broker server."""

from __future__ import annotations

import json
from pathlib import Path

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
        data = {"default": "", "prefixes": {}}
        _map_cache[map_path] = (mtime, data)
        return data

    raw = json.loads(path.read_text(encoding="utf-8"))
    prefixes = {
        str(k): str(v)
        for k, v in (raw.get("prefixes") or {}).items()
        if not str(k).startswith("_") and v
    }
    data = {
        "default": str(raw.get("default") or ""),
        "prefixes": prefixes,
    }
    _map_cache[map_path] = (mtime, data)
    return data


def resolve_terminal_path(
    server: str,
    *,
    default_path: str,
    map_path: str | None = None,
) -> str:
    """Pick a terminal64.exe for this server via longest prefix match."""
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
