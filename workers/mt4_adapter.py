"""Portable MetaTrader 4 adapter via companion EA file IPC.

There is no official Python API for MT4. The bridge starts a portable terminal
with login credentials, then the always-on companion EA
(`FinhubJournal_BridgeExport.mq4`) reads a request file and writes a response
JSON of MT5-shaped deals under MQL4/Files.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


REQUEST_NAME = "finhub_bridge_request.json"
RESPONSE_NAME = "finhub_bridge_response.json"


def _utc_ts(value) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    return int(datetime.now(timezone.utc).timestamp())


def files_dir_for_terminal(terminal_path: str) -> Path:
    """Portable MT4 keeps MQL4/Files next to terminal.exe."""
    root = Path(terminal_path or "").expanduser().resolve()
    if root.is_file():
        root = root.parent
    return root / "MQL4" / "Files"


class MetaTrader4Adapter:
    def __init__(
        self,
        *,
        files_dir: str | Path | None = None,
        poll_interval_seconds: float = 0.5,
        start_process=None,
    ):
        self._files_dir_override = Path(files_dir) if files_dir else None
        self._poll_interval_seconds = poll_interval_seconds
        self._start_process = start_process or subprocess.Popen
        self._terminal_path = ""
        self._process = None
        self._last_error = (1, "OK")
        self._login = None
        self._password = None
        self._server = None
        self._timeout_ms = 15000
        self._session_ready = False

    def _files_dir(self) -> Path:
        if self._files_dir_override is not None:
            return self._files_dir_override
        return files_dir_for_terminal(self._terminal_path)

    def _request_path(self) -> Path:
        return self._files_dir() / REQUEST_NAME

    def _response_path(self) -> Path:
        return self._files_dir() / RESPONSE_NAME

    def last_error(self):
        return self._last_error

    def _same_session(self, path, login, password, server) -> bool:
        return (
            self._session_ready
            and self._process is not None
            and self._process.poll() is None
            and self._terminal_path == str(path or "")
            and self._login == int(login)
            and self._password == str(password)
            and self._server == str(server)
        )

    def initialize(self, path, login, password, server, timeout_ms=15000) -> bool:
        # Keep OS-native path so Path checks work on Windows and in unit tests.
        path = str(path or "")
        login_i = int(login)
        password_s = str(password)
        server_s = str(server)
        self._timeout_ms = int(timeout_ms or 15000)
        self._last_error = (1, "OK")

        if self._same_session(path, login_i, password_s, server_s):
            return True

        # Account/path changed — drop the warm session before relaunch.
        if self._session_ready:
            self.shutdown(force=True)

        self._terminal_path = path
        self._login = login_i
        self._password = password_s
        self._server = server_s
        self._session_ready = False

        if not self._terminal_path or not Path(self._terminal_path).is_file():
            self._last_error = (-1, "MT4 terminal path missing or not found")
            return False

        files = self._files_dir()
        try:
            files.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._last_error = (-1, f"Cannot create MT4 Files dir ({exc})")
            return False

        self._clear_ipc()
        try:
            self._write_request(
                {
                    "action": "verify",
                    "login": self._login,
                    "server": self._server,
                    "request_id": f"verify-{int(time.time())}",
                }
            )
        except OSError as exc:
            self._last_error = (-1, f"Cannot write MT4 request ({exc})")
            return False

        # Start after the request exists so OnInit can pick it up.
        if not self._ensure_terminal_running(force_restart=True):
            return False

        payload = self._wait_response(timeout_ms=self._timeout_ms)
        if not payload:
            self._last_error = (-1, "MT4 companion EA did not respond (is the EA attached?)")
            return False
        if not payload.get("ok"):
            self._last_error = (-1, str(payload.get("error") or "Login failed"))
            return False
        connected_login = int(payload.get("login") or 0)
        if connected_login and connected_login != self._login:
            self._last_error = (
                -1,
                f"MT4 logged into {connected_login}, expected {self._login}",
            )
            return False
        self._session_ready = True
        return True

    def history_deals(self, date_from, date_to, on_chunk=None) -> list[dict]:
        if not self._login:
            self._last_error = (-1, "MT4 not initialized")
            return []

        # Reuse the warm terminal — companion OnTimer/OnCalculate picks up IPC.
        self._clear_ipc()
        try:
            self._write_request(
                {
                    "action": "history",
                    "login": self._login,
                    "server": self._server,
                    "from_ts": _utc_ts(date_from),
                    "to_ts": _utc_ts(date_to),
                    "request_id": f"history-{int(time.time())}",
                }
            )
        except OSError as exc:
            self._last_error = (-1, f"Cannot write MT4 history request ({exc})")
            return []

        if not self._ensure_terminal_running(force_restart=False):
            return []

        if callable(on_chunk):
            try:
                on_chunk()
            except Exception:
                pass

        wait_ms = max(int(self._timeout_ms or 15000), 90000)
        payload = self._wait_response(timeout_ms=wait_ms)
        if not payload:
            self._last_error = (-1, "MT4 history export timed out")
            return []
        if not payload.get("ok"):
            self._last_error = (-1, str(payload.get("error") or "History export failed"))
            return []

        deals = payload.get("deals") or []
        if not isinstance(deals, list):
            self._last_error = (-1, "Invalid MT4 history payload")
            return []
        return [d for d in deals if isinstance(d, dict)]

    def shutdown(self, force: bool = True):
        self._clear_ipc()
        self._session_ready = False
        if not force:
            return
        proc = self._process
        self._process = None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
        self._kill_stale_terminals()

    def _kill_stale_terminals(self) -> None:
        """Ensure only one portable MT4 is running for this path."""
        if os.name != "nt":
            return
        try:
            subprocess.run(
                ["taskkill", "/IM", "terminal.exe", "/F"],
                capture_output=True,
                check=False,
            )
            time.sleep(1.5)
        except Exception:
            pass

    def _ensure_terminal_running(self, *, force_restart: bool = False) -> bool:
        if (
            not force_restart
            and self._process is not None
            and self._process.poll() is None
        ):
            return True
        if not self._terminal_path:
            self._last_error = (-1, "MT4 terminal path missing")
            return False
        # Fresh login — kill leftover MT4 first.
        self._kill_stale_terminals()
        # Write login.ini so MT4 has credentials even if /login args are ignored.
        # Use write_bytes: Path.write_text on Windows expands \n -> \r\n and would
        # turn an explicit \r\n join into broken \r\r\n lines MT4 cannot parse.
        try:
            cfg_dir = Path(self._terminal_path).resolve().parent / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            login_ini = (
                "[Common]\n"
                f"Login={self._login}\n"
                f"Password={self._password}\n"
                f"Server={self._server}\n"
                "KeepPrivate=1\n"
                "NewsEnable=0\n"
            )
            (cfg_dir / "login.ini").write_bytes(login_ini.replace("\n", "\r\n").encode("ascii"))
        except OSError:
            pass

        config_path = str(Path(self._terminal_path).resolve().parent / "config" / "login.ini")
        # Pass login on CLI too (list argv, so '@' in password is safe).
        args = [
            self._terminal_path,
            "/portable",
            f"/config:{config_path}",
            f"/login:{self._login}",
            f"/password:{self._password}",
            f"/server:{self._server}",
        ]
        try:
            self._process = self._start_process(
                args,
                cwd=str(Path(self._terminal_path).parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self._last_error = (-1, f"Failed to start MT4 ({exc})")
            self._process = None
            return False
        # Real Windows terminals need a moment for process start; OnInit itself
        # waits for broker connection, so keep this short.
        if os.name == "nt" and self._start_process is subprocess.Popen:
            time.sleep(3)
        return True

    def _write_request(self, payload: dict) -> None:
        path = self._request_path()
        tmp = path.with_suffix(".tmp")
        data = json.dumps(payload, separators=(",", ":"))
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)

    def _clear_ipc(self) -> None:
        for path in (self._request_path(), self._response_path()):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _wait_response(self, *, timeout_ms: int) -> dict | None:
        deadline = time.monotonic() + max(1, timeout_ms) / 1000.0
        path = self._response_path()
        while time.monotonic() < deadline:
            if path.is_file():
                try:
                    raw = path.read_text(encoding="utf-8")
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass
                        return payload
                except (OSError, json.JSONDecodeError):
                    pass
            time.sleep(self._poll_interval_seconds)
        return None
