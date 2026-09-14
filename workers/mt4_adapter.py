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


class _ExternalProc:
    """Placeholder for a terminal started outside this process (schtasks)."""

    def poll(self):
        return None

    def terminate(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None


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

        # Prefer a warm terminal already logged into this account (common on the
        # interactive VPS session). Force-restart only when verify fails.
        if self._terminal_process_running():
            payload = self._verify_via_ipc(timeout_ms=min(20000, self._timeout_ms))
            if payload and self._verify_payload_ok(payload):
                self._process = self._process or _ExternalProc()
                self._session_ready = True
                return True

        self._clear_ipc()
        try:
            self._write_request(self._verify_payload())
        except OSError as exc:
            self._last_error = (-1, f"Cannot write MT4 request ({exc})")
            return False

        # Start after the request exists so OnInit can pick it up.
        if not self._ensure_terminal_running(force_restart=True):
            return False

        payload = self._wait_response(timeout_ms=self._timeout_ms)
        if not self._verify_payload_ok(payload):
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

    def _verify_payload(self) -> dict:
        return {
            "action": "verify",
            "login": self._login,
            "server": self._server,
            "request_id": f"verify-{int(time.time())}",
        }

    def _verify_via_ipc(self, *, timeout_ms: int) -> dict | None:
        self._clear_ipc()
        try:
            self._write_request(self._verify_payload())
        except OSError as exc:
            self._last_error = (-1, f"Cannot write MT4 request ({exc})")
            return None
        return self._wait_response(timeout_ms=timeout_ms)

    def _verify_payload_ok(self, payload: dict | None) -> bool:
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
        return True

    def _terminal_process_running(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            return True
        if os.name != "nt":
            return False
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq terminal.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            return "terminal.exe" in (out.stdout or "").lower()
        except Exception:
            return False

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

    def _write_login_ini(self) -> str:
        """Write portable login.ini; return absolute path."""
        cfg_dir = Path(self._terminal_path).resolve().parent / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        # Use write_bytes: Path.write_text on Windows expands \n -> \r\n and would
        # turn an explicit \r\n join into broken \r\r\n lines MT4 cannot parse.
        login_ini = (
            "[Common]\n"
            f"Login={self._login}\n"
            f"Password={self._password}\n"
            f"Server={self._server}\n"
            "KeepPrivate=1\n"
            "NewsEnable=0\n"
        )
        path = cfg_dir / "login.ini"
        path.write_bytes(login_ini.replace("\n", "\r\n").encode("ascii"))
        return str(path)

    def _launch_terminal_interactive(self, args: list[str], cwd: str) -> bool:
        """Start MT4 in the logged-on console session (required for broker login).

        Workers may run in session 0; Popen from there leaves MT4 unable to
        authenticate. schtasks /IT launches into the interactive desktop.
        """
        task = "FinhubkhMt4LoginLaunch"
        # Tiny .bat: `start "title" exe args` so /portable is not eaten by start.exe.
        bat = Path(self._terminal_path).resolve().parent / "_finhub_mt4_launch.bat"
        exe = args[0]
        rest = " ".join(f'"{a}"' for a in args[1:])
        bat.write_text(
            "@echo off\r\n"
            f'cd /d "{cwd}"\r\n'
            f'start "mt4" "{exe}" {rest}\r\n',
            encoding="ascii",
        )
        subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"], capture_output=True)
        create = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                task,
                "/TR",
                str(bat),
                "/SC",
                "ONCE",
                "/ST",
                "23:59",
                "/RL",
                "HIGHEST",
                "/F",
                "/IT",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if create.returncode != 0:
            self._last_error = (
                -1,
                f"Failed to schedule interactive MT4 launch ({create.stderr or create.stdout})",
            )
            return False
        run = subprocess.run(
            ["schtasks", "/Run", "/TN", task],
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode != 0:
            self._last_error = (
                -1,
                f"Failed to run interactive MT4 launch ({run.stderr or run.stdout})",
            )
            return False
        # Wait until terminal.exe appears (or timeout).
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._terminal_process_running():
                time.sleep(2)
                return True
            time.sleep(0.5)
        self._last_error = (-1, "MT4 interactive launch did not start terminal.exe")
        return False

    def _ensure_terminal_running(self, *, force_restart: bool = False) -> bool:
        if (
            not force_restart
            and self._process is not None
            and self._process.poll() is None
        ):
            return True
        if not force_restart and self._terminal_process_running():
            return True
        if not self._terminal_path:
            self._last_error = (-1, "MT4 terminal path missing")
            return False
        # Fresh login — kill leftover MT4 first.
        self._kill_stale_terminals()
        try:
            config_path = self._write_login_ini()
        except OSError as exc:
            self._last_error = (-1, f"Cannot write MT4 login.ini ({exc})")
            return False

        # Include /login /password /server. Popen/list argv keeps '@' intact;
        # login.ini alone is not always applied on headless relaunches.
        args = [
            self._terminal_path,
            "/portable",
            f"/config:{config_path}",
            f"/login:{self._login}",
            f"/password:{self._password}",
            f"/server:{self._server}",
        ]
        cwd = str(Path(self._terminal_path).parent)

        # Unit tests inject start_process — keep that path.
        if self._start_process is not subprocess.Popen:
            try:
                self._process = self._start_process(
                    args,
                    cwd=cwd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                self._last_error = (-1, f"Failed to start MT4 ({exc})")
                self._process = None
                return False
            return True

        if os.name == "nt":
            if self._launch_terminal_interactive(args, cwd):
                # Track an external process handle so warm-session reuse works.
                self._process = _ExternalProc()
                return True
            # Fall back to direct Popen (may still work in interactive workers).
            try:
                self._process = subprocess.Popen(
                    args,
                    cwd=cwd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                self._last_error = (-1, f"Failed to start MT4 ({exc})")
                self._process = None
                return False
            time.sleep(3)
            return True

        try:
            self._process = self._start_process(
                args,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self._last_error = (-1, f"Failed to start MT4 ({exc})")
            self._process = None
            return False
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
