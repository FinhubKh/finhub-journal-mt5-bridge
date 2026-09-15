"""Unit tests for MT4 file-IPC adapter (no real terminal)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from workers.mt4_adapter import MetaTrader4Adapter


class _FakeProc:
    def poll(self):
        return None

    def terminate(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None


def _start_ea_simulator(files: Path) -> None:
    """Mimic the companion EA: answer each request file with a response."""

    def loop():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            req = files / "finhub_bridge_request.json"
            if req.is_file():
                try:
                    payload = json.loads(req.read_text(encoding="utf-8"))
                    req.unlink(missing_ok=True)
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.01)
                    continue
                if payload.get("action") == "verify":
                    body = {"ok": True, "login": payload.get("login"), "server": "Demo"}
                else:
                    body = {
                        "ok": True,
                        "deals": [
                            {
                                "ticket": 1,
                                "order": 1,
                                "position_id": 1,
                                "entry": "in",
                                "type": "buy",
                                "symbol": "EURUSD",
                                "price": 1.1,
                                "volume": 0.1,
                                "profit": 0,
                                "swap": 0,
                                "commission": 0,
                                "sl": 0,
                                "comment": "",
                                "time": 1700000000,
                            }
                        ],
                    }
                (files / "finhub_bridge_response.json").write_text(
                    json.dumps(body), encoding="utf-8"
                )
            time.sleep(0.01)

    threading.Thread(target=loop, daemon=True).start()


def test_initialize_reads_verify_response(tmp_path: Path):
    terminal = tmp_path / "terminal.exe"
    terminal.write_text("fake", encoding="utf-8")
    files = tmp_path / "MQL4" / "Files"
    files.mkdir(parents=True)
    _start_ea_simulator(files)

    adapter = MetaTrader4Adapter(
        poll_interval_seconds=0.01,
        start_process=lambda *a, **k: _FakeProc(),
    )
    ok = adapter.initialize(str(terminal), 12345, "secret", "Demo", timeout_ms=2000)
    assert ok is True
    adapter.shutdown()


def test_history_deals_parses_response(tmp_path: Path):
    terminal = tmp_path / "terminal.exe"
    terminal.write_text("fake", encoding="utf-8")
    files = tmp_path / "MQL4" / "Files"
    files.mkdir(parents=True)
    _start_ea_simulator(files)

    adapter = MetaTrader4Adapter(
        poll_interval_seconds=0.01,
        start_process=lambda *a, **k: _FakeProc(),
    )
    assert adapter.initialize(str(terminal), 99, "pw", "Srv", timeout_ms=2000)
    # Keep history wait short in unit tests (adapter uses max(timeout, 120s) otherwise).
    adapter._timeout_ms = 2000
    deals = adapter.history_deals(1700000000, 1700003600)
    assert len(deals) == 1
    assert deals[0]["symbol"] == "EURUSD"
    adapter.shutdown()


def test_history_reuses_running_terminal_without_restart(tmp_path: Path):
    terminal = tmp_path / "terminal.exe"
    terminal.write_text("fake", encoding="utf-8")
    files = tmp_path / "MQL4" / "Files"
    files.mkdir(parents=True)
    _start_ea_simulator(files)

    starts = {"n": 0}

    def start_process(*a, **k):
        starts["n"] += 1
        return _FakeProc()

    adapter = MetaTrader4Adapter(
        poll_interval_seconds=0.01,
        start_process=start_process,
    )
    assert adapter.initialize(str(terminal), 99, "pw", "Srv", timeout_ms=2000)
    adapter._timeout_ms = 2000
    deals = adapter.history_deals(1700000000, 1700003600)
    assert len(deals) == 1
    assert starts["n"] == 1
    # Same credentials again should not relaunch.
    assert adapter.initialize(str(terminal), 99, "pw", "Srv", timeout_ms=2000)
    assert starts["n"] == 1
    adapter.shutdown(force=True)


def test_initialize_rejects_ok_with_zero_login(tmp_path: Path):
    terminal = tmp_path / "terminal.exe"
    terminal.write_text("fake", encoding="utf-8")
    files = tmp_path / "MQL4" / "Files"
    files.mkdir(parents=True)

    def loop():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            req = files / "finhub_bridge_request.json"
            if req.is_file():
                try:
                    req.unlink(missing_ok=True)
                except OSError:
                    pass
                (files / "finhub_bridge_response.json").write_text(
                    json.dumps({"ok": True, "login": 0, "server": "Demo"}),
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)

    threading.Thread(target=loop, daemon=True).start()

    adapter = MetaTrader4Adapter(
        poll_interval_seconds=0.01,
        start_process=lambda *a, **k: _FakeProc(),
    )
    ok = adapter.initialize(str(terminal), 12345, "secret", "Demo", timeout_ms=2000)
    assert ok is False
    code, desc = adapter.last_error()
    assert code == -1
    assert "not connected" in desc.lower()
    adapter.shutdown(force=True)
