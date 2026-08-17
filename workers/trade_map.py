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


def _deal_fees(deal: dict) -> float:
    return (
        float(deal.get("profit") or 0)
        + float(deal.get("swap") or 0)
        + float(deal.get("commission") or 0)
    )


def _sl_price(ins: list, exit_: dict) -> float:
    for d in (exit_, *(ins or [])):
        sl = float(d.get("sl") or 0)
        if sl > 0:
            return sl
    return 0.0


def _r_multiple(entry_price: float, exit_price: float, sl_price: float, pnl: float) -> float:
    if sl_price <= 0 or entry_price <= 0:
        return 0.0
    risk = abs(entry_price - sl_price)
    if risk <= 0:
        return 0.0
    rr = abs(exit_price - entry_price) / risk
    signed = rr if pnl >= 0 else -rr
    return round(signed, 2)


def _vwap_price(deals: list) -> float:
    total_vol = sum(float(d.get("volume") or 0) for d in deals)
    if total_vol <= 0:
        return float(deals[0].get("price") or 0)
    return sum(float(d.get("price") or 0) * float(d.get("volume") or 0) for d in deals) / total_vol


def deals_to_trades(deals: list) -> list:
    """Map MT5 deals to closed journal trades.

    Partial closes: one journal row per OUT deal (volume/PnL from that exit).
    Full single-exit closes keep the previous ticket = entry order id.
    """
    by_pos: dict = {}
    for d in deals:
        pos_id = d["position_id"]
        bucket = by_pos.get(pos_id)
        if bucket is None:
            bucket = {"ins": [], "outs": []}
            by_pos[pos_id] = bucket
        kind = d.get("entry")
        if kind == "in":
            bucket["ins"].append(d)
        elif kind == "out":
            bucket["outs"].append(d)

    out = []
    for group in by_pos.values():
        ins = group["ins"]
        outs = group["outs"]
        if not outs:
            continue

        entry_fees = sum(_deal_fees(d) for d in ins)
        if ins:
            entry_price = _vwap_price(ins)
            entry_type = (ins[0].get("type") or "").lower()
            direction = "buy" if entry_type == "buy" else "sell"
            open_time = _iso(min((d.get("time") for d in ins), default=outs[0].get("time")))
            first_entry = ins[0]
        else:
            # Exit-only history: infer direction from the closing deal.
            exit_type = (outs[0].get("type") or "").lower()
            direction = "buy" if exit_type == "sell" else "sell"
            entry_price = 0.0
            open_time = _iso(outs[0].get("time"))
            first_entry = None

        for idx, exit_ in enumerate(outs):
            pnl_raw = _deal_fees(exit_)
            if idx == 0:
                pnl_raw += entry_fees

            if len(outs) == 1 and first_entry is not None:
                ticket = int(
                    first_entry.get("order")
                    or first_entry.get("ticket")
                    or exit_.get("order")
                    or exit_.get("ticket")
                )
            else:
                ticket = int(exit_.get("order") or exit_.get("ticket"))

            sl_price = _sl_price(ins, exit_)
            exit_price = float(exit_.get("price") or 0)
            out.append(
                {
                    "ticket": ticket,
                    "symbol": exit_.get("symbol") or (first_entry or {}).get("symbol"),
                    "direction": direction,
                    "entry_price": float(entry_price),
                    "exit_price": exit_price,
                    "lot_size": float(exit_.get("volume") or 0),
                    "pnl_raw": pnl_raw,
                    "pnl_usd": pnl_raw,
                    "r_value": _r_multiple(float(entry_price), exit_price, sl_price, pnl_raw),
                    "sl_price": sl_price,
                    "open_time": open_time,
                    "close_time": _iso(exit_.get("time")),
                }
            )
    return out
