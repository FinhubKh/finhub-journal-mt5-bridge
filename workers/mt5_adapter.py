"""Real MetaTrader5 adapter — import only on Windows VPS."""


class MetaTrader5Adapter:
    def __init__(self):
        import MetaTrader5 as mt5

        self._mt5 = mt5

    def initialize(self, path, login, password, server) -> bool:
        return bool(
            self._mt5.initialize(
                path=path,
                login=int(login),
                password=password,
                server=server,
            )
        )

    def shutdown(self):
        self._mt5.shutdown()

    def history_deals(self, date_from, date_to) -> list[dict]:
        deals = self._mt5.history_deals_get(date_from, date_to) or []
        mapped = []
        for d in deals:
            entry = "in" if d.entry == 0 else "out" if d.entry == 1 else "other"
            typ = "buy" if d.type == 0 else "sell" if d.type == 1 else str(d.type)
            mapped.append(
                {
                    "ticket": int(d.ticket),
                    "order": int(d.order),
                    "position_id": int(d.position_id),
                    "entry": entry,
                    "type": typ,
                    "symbol": d.symbol,
                    "price": float(d.price),
                    "volume": float(d.volume),
                    "profit": float(d.profit),
                    "swap": float(d.swap),
                    "commission": float(d.commission),
                    "time": d.time,
                }
            )
        return [m for m in mapped if m["entry"] in ("in", "out")]
