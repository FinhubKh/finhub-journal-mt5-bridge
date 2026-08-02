"""Real MetaTrader5 adapter — import only on Windows VPS."""

import os
import time


class MetaTrader5Adapter:
    def __init__(self):
        import MetaTrader5 as mt5

        self._mt5 = mt5

    def initialize(self, path, login, password, server, timeout_ms=15000) -> bool:
        ok = bool(
            self._mt5.initialize(
                path=path,
                login=int(login),
                password=password,
                server=server,
                timeout=timeout_ms,
            )
        )
        if ok:
            self._hide_terminal_window(path)
        return ok

    def _hide_terminal_window(self, path: str) -> None:
        """Best-effort: hide the MT5 terminal window so it never pops up on screen.

        MT5 has no official headless/silent-login flag, so this finds the
        terminal's window(s) by process name and hides them via the Windows
        API. Never allowed to affect login/sync — any failure here is swallowed.
        """
        try:
            import psutil
            import win32con
            import win32gui
            import win32process
        except ImportError:
            return

        try:
            exe_name = os.path.basename(path).lower()
            pids = {
                p.info["pid"]
                for p in psutil.process_iter(["pid", "name"])
                if (p.info.get("name") or "").lower() == exe_name
            }
            if not pids:
                return

            def _hide_visible(hwnd, hidden_any):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid in pids and win32gui.IsWindowVisible(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                    hidden_any.append(True)
                return True

            # The window can take a moment to appear after initialize() returns,
            # so poll briefly and stop once nothing is left visible to hide.
            for attempt in range(6):
                hidden_any: list = []
                win32gui.EnumWindows(_hide_visible, hidden_any)
                if not hidden_any and attempt > 0:
                    break
                time.sleep(0.25)
        except Exception:
            pass

    def last_error(self):
        return self._mt5.last_error()

    def shutdown(self):
        self._mt5.shutdown()

    def history_deals(self, date_from, date_to) -> list[dict]:
        deals = self._mt5.history_deals_get(date_from, date_to) or []
        mapped = []
        for d in deals:
            # 0 = in, 1 = out — skip balance/credit/other in one pass
            if d.entry not in (0, 1):
                continue
            mapped.append(
                {
                    "ticket": int(d.ticket),
                    "order": int(d.order),
                    "position_id": int(d.position_id),
                    "entry": "in" if d.entry == 0 else "out",
                    "type": "buy" if d.type == 0 else "sell" if d.type == 1 else str(d.type),
                    "symbol": d.symbol,
                    "price": float(d.price),
                    "volume": float(d.volume),
                    "profit": float(d.profit),
                    "swap": float(d.swap),
                    "commission": float(d.commission),
                    "time": d.time,
                }
            )
        return mapped
