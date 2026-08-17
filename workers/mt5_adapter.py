"""Real MetaTrader5 adapter — import only on Windows VPS."""

from datetime import timedelta

# MT5 can drop or truncate a multi-year history_deals_get. Walk in slices.
HISTORY_CHUNK_DAYS = 90
CASH_DEAL_TYPES = (2, 3, 4, 5, 6, 12)


def _deal_type_int(value) -> int:
    if value is None:
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def keep_history_deal(entry, deal_type) -> bool:
    """Keep IN/OUT trades and balance/credit deals (any entry)."""
    return entry in (0, 1) or deal_type in CASH_DEAL_TYPES


def map_history_deal(d) -> dict:
    deal_type = _deal_type_int(getattr(d, "type", None))
    return {
        "ticket": int(d.ticket),
        "order": int(d.order),
        "position_id": int(d.position_id),
        "entry": "in" if d.entry == 0 else "out",
        "type": "buy" if deal_type == 0 else "sell" if deal_type == 1 else str(deal_type),
        "symbol": d.symbol,
        "price": float(d.price),
        "volume": float(d.volume),
        "profit": float(d.profit),
        "swap": float(d.swap),
        "commission": float(d.commission),
        "sl": float(getattr(d, "sl", 0) or 0),
        "comment": str(getattr(d, "comment", "") or ""),
        "time": d.time,
    }


class MetaTrader5Adapter:
    def __init__(self, mt5=None):
        if mt5 is None:
            import MetaTrader5 as mt5

        self._mt5 = mt5

    def initialize(self, path, login, password, server, timeout_ms=15000) -> bool:
        # Forward slashes + portable mode avoid common IPC timeouts on Windows
        # Server / Hyper-V VPS installs under Program Files.
        normalized = (path or "").replace("\\", "/")
        kwargs = {
            "login": int(login),
            "password": password,
            "server": server,
            "timeout": timeout_ms,
            "portable": True,
        }
        if normalized:
            kwargs["path"] = normalized
        return bool(self._mt5.initialize(**kwargs))

    def last_error(self):
        return self._mt5.last_error()

    def shutdown(self):
        self._mt5.shutdown()

    def history_deals(self, date_from, date_to) -> list[dict]:
        raw = []
        cursor = date_from
        chunk = timedelta(days=HISTORY_CHUNK_DAYS)
        while cursor < date_to:
            nxt = min(cursor + chunk, date_to)
            batch = self._mt5.history_deals_get(cursor, nxt) or []
            raw.extend(batch)
            cursor = nxt

        mapped = []
        seen = set()
        for d in raw:
            deal_type = _deal_type_int(getattr(d, "type", None))
            if not keep_history_deal(getattr(d, "entry", None), deal_type):
                continue
            row = map_history_deal(d)
            ticket = row["ticket"]
            if not ticket or ticket in seen:
                continue
            seen.add(ticket)
            mapped.append(row)
        return mapped
