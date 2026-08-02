from datetime import datetime, timezone


def _iso(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def deals_to_trades(deals: list) -> list:
    # position_id -> first in / first out (matches previous next() semantics)
    by_pos: dict = {}
    for d in deals:
        bucket = by_pos.get(d["position_id"])
        if bucket is None:
            bucket = {"in": None, "out": None}
            by_pos[d["position_id"]] = bucket
        kind = d.get("entry")
        if kind == "in":
            if bucket["in"] is None:
                bucket["in"] = d
        elif kind == "out":
            if bucket["out"] is None:
                bucket["out"] = d

    out = []
    for group in by_pos.values():
        entry = group["in"]
        exit_ = group["out"]
        if not exit_:
            continue
        exit_type = (exit_.get("type") or "").lower()
        direction = "buy" if exit_type == "sell" else "sell"
        pnl_raw = (
            float(exit_.get("profit") or 0)
            + float(exit_.get("swap") or 0)
            + float(exit_.get("commission") or 0)
        )
        if entry:
            ticket = int(entry.get("order") or entry.get("ticket") or exit_.get("ticket"))
        else:
            ticket = int(exit_.get("order") or exit_.get("ticket"))
        out.append(
            {
                "ticket": ticket,
                "symbol": exit_.get("symbol") or (entry or {}).get("symbol"),
                "direction": direction,
                "entry_price": float((entry or {}).get("price") or 0),
                "exit_price": float(exit_.get("price") or 0),
                "lot_size": float(exit_.get("volume") or (entry or {}).get("volume") or 0),
                "pnl_raw": pnl_raw,
                "pnl_usd": pnl_raw,
                "r_value": 0,
                "open_time": _iso((entry or exit_).get("time")),
                "close_time": _iso(exit_.get("time")),
            }
        )
    return out
