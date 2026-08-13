"""Probe whether MT5 server names are cached (auth error) vs missing (IPC timeout)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import MetaTrader5 as mt5

PROBE_SERVERS = [
    "Exness-MT5Real36",
    "Exness-MT5Real1",
    "LirunexLimited-Live-MT5",
    "XMGlobal-MT5",
    "XMGlobal-MT5 2",
    "Pepperstone-MT5-Live01",
    "FBS-Real",
    "RoboForex-ECN",
    "TickmillUK-Live",
    "Alpari-MT5",
    "ForexTime-Live01",
    "ICMarketsSC-MT5",
    "FxPro-MT5",
    "FusionMarkets-Live",
    "LiteFinance-MT5-Live",
    "STMarket-Live",
]


def resolve(server: str, cfg: dict, default: str) -> str:
    best, path = "", ""
    for pfx, p in (cfg.get("prefixes") or {}).items():
        if str(pfx).startswith("_"):
            continue
        if server.startswith(pfx) and len(pfx) > len(best) and Path(p).is_file():
            best, path = pfx, p
    if path:
        return path
    return default if Path(default).is_file() else default


def main() -> int:
    map_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/mt5_terminal_map.json")
    default = (
        sys.argv[2]
        if len(sys.argv) > 2
        else r"C:\finhubkh\mt5-portable\terminal64.exe"
    )
    cfg = (
        json.loads(map_path.read_text(encoding="utf-8-sig"))
        if map_path.is_file()
        else {"default": default, "prefixes": {}}
    )
    default = cfg.get("default") or default

    results: list[str] = []
    for server in PROBE_SERVERS:
        path = resolve(server, cfg, default)
        ok = mt5.initialize(
            path=path,
            login=1,
            password="cache-probe",
            server=server,
            timeout=45000,
        )
        err = mt5.last_error()
        try:
            mt5.shutdown()
        except Exception:
            pass
        code = err[0] if isinstance(err, tuple) else err
        desc = err[1] if isinstance(err, tuple) and len(err) > 1 else str(err)
        if ok:
            status = "LOGIN_UNEXPECTED_OK"
        elif "IPC" in str(desc):
            status = "MISSING"
        else:
            status = "CACHED"
        print(f"{status}\t{server}\t{code}\t{desc}\t{path}")
        results.append(status)
        time.sleep(1)

    print("---")
    print(
        "CACHED",
        results.count("CACHED"),
        "MISSING",
        results.count("MISSING"),
        "OTHER",
        len(results) - results.count("CACHED") - results.count("MISSING"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
