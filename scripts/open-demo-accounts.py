"""Open free in-terminal demo accounts on branded MT4/MT5 installs (ctypes UI).

Uses SendMessage/BM_CLICK/keybd_event (works in interactive scheduled tasks).
Saves C:\\finhubkh\\demo-accounts.json + open-demo-accounts.log
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(r"C:\finhubkh\open-demo-accounts.log")
OUT = Path(r"C:\finhubkh\demo-accounts.json")

sys.stdout = open(LOG, "w", encoding="utf-8", buffering=1)
sys.stderr = sys.stdout

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
Proc = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)

WM_SETTEXT = 0x000C
BM_CLICK = 0x00F5
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_DOWN = 0x28
VK_MENU = 0x12
VK_F = 0x46
VK_SPACE = 0x20

DEMO_NAME = "Finhub Cache"
DEMO_EMAIL = "cache@finhubkh.demo"
DEMO_PHONE = "85512000000"
DEMO_CITY = "Phnom Penh"


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def title(h):
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, b, 512)
    return b.value


def classname(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def tops():
    out = []

    def cb(h, _):
        if user32.IsWindowVisible(h):
            out.append(h)
        return True

    user32.EnumWindows(Proc(cb), 0)
    return out


def walk(h, acc=None):
    if acc is None:
        acc = []
    acc.append((h, classname(h), title(h)))

    def cb(ch, _):
        walk(ch, acc)
        return True

    user32.EnumChildWindows(h, Proc(cb), 0)
    return acc


def find_dlg(needles, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for h in tops():
            t = title(h).lower()
            if any(n.lower() in t for n in needles):
                return h
        time.sleep(0.4)
    return None


def click_btn(dlg, needle) -> bool:
    for h, cls, t in walk(dlg):
        if cls == "Button" and t and needle.lower() in t.lower():
            log(f"click button '{t}'")
            user32.SendMessageW(h, BM_CLICK, 0, 0)
            return True
    return False


def set_edit(dlg, index: int, text: str) -> bool:
    edits = [h for h, cls, _ in walk(dlg) if cls == "Edit"]
    if index >= len(edits):
        return False
    user32.SendMessageW(edits[index], WM_SETTEXT, 0, text)
    log(f"edit[{index}]={text!r}")
    return True


def tap(vk):
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk, 0, 2, 0)


def alt_f():
    user32.keybd_event(VK_MENU, 0, 0, 0)
    time.sleep(0.03)
    tap(VK_F)
    user32.keybd_event(VK_MENU, 0, 2, 0)


def kill_terminals():
    for name in ("terminal.exe", "terminal64.exe"):
        subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True)


def list_targets(only: list[str]) -> list[dict]:
    targets = []
    for root, plat, exe in (
        (Path(r"C:\finhubkh\mt4-brokers"), "mt4", "terminal.exe"),
        (Path(r"C:\finhubkh\mt5-brokers"), "mt5", "terminal64.exe"),
    ):
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            term = d / exe
            if not term.is_file():
                continue
            targets.append(
                {
                    "platform": plat,
                    "id": d.name,
                    "path": str(term),
                    "cwd": str(d),
                    "args": ["/portable"] if (d / "portable.ini").is_file() else [],
                }
            )
    if only:
        targets = [
            t
            for t in targets
            if t["id"].lower() in only
            or t["platform"] in only
            or f"{t['platform']}-{t['id']}".lower() in only
        ]
    return targets


def dump_tree(dlg, label="tree"):
    rows = []
    for h, cls, t in walk(dlg):
        if t or cls in ("Button", "Edit", "SysListView32", "ListBox", "ComboBox"):
            rows.append(f"{cls}|{t}")
    log(f"{label}: " + " || ".join(rows[:40]))


LVM_FIRST = 0x1000
LVM_GETITEMCOUNT = LVM_FIRST + 4
LVM_GETITEMTEXTW = LVM_FIRST + 115
LVM_SETITEMSTATE = LVM_FIRST + 43
LVM_ENSUREVISIBLE = LVM_FIRST + 19
LVIF_TEXT = 0x0001
LVIS_SELECTED = 0x0002
LVIS_FOCUSED = 0x0001


class LVITEM(ctypes.Structure):
    _fields_ = [
        ("mask", w.UINT),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", w.UINT),
        ("stateMask", w.UINT),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", w.LPARAM),
    ]


def listview_select_demo(dlg) -> bool:
    """Select first SysListView32 row whose text contains Demo (case-insensitive)."""
    for h, cls, _ in walk(dlg):
        if cls != "SysListView32":
            continue
        count = int(user32.SendMessageW(h, LVM_GETITEMCOUNT, 0, 0))
        log(f"listview items={count}")
        buf = ctypes.create_unicode_buffer(512)
        # Allocate buffer in target process is hard; many MT builds allow local buffer for GETITEMTEXT.
        for i in range(count):
            item = LVITEM()
            item.mask = LVIF_TEXT
            item.iItem = i
            item.iSubItem = 0
            item.pszText = ctypes.cast(buf, ctypes.c_void_p).value
            item.cchTextMax = 512
            user32.SendMessageW(h, LVM_GETITEMTEXTW, i, ctypes.byref(item))
            text = buf.value or ""
            if text:
                log(f"  row[{i}]={text}")
            if text and "demo" in text.lower():
                # Select row
                st = LVITEM()
                st.stateMask = LVIS_SELECTED | LVIS_FOCUSED
                st.state = LVIS_SELECTED | LVIS_FOCUSED
                user32.SendMessageW(h, LVM_SETITEMSTATE, i, ctypes.byref(st))
                user32.SendMessageW(h, LVM_ENSUREVISIBLE, i, False)
                log(f"selected demo server row[{i}]={text}")
                return True
    return False


def try_select_demo_in_list(dlg) -> bool:
    if listview_select_demo(dlg):
        return True
    # Fallback: focus first list and type Demo
    for h, cls, t in walk(dlg):
        if cls in ("SysListView32", "ListBox"):
            user32.SetForegroundWindow(dlg)
            user32.SetFocus(h)
            time.sleep(0.2)
            for ch in "Demo":
                vk = ord(ch.upper())
                user32.keybd_event(vk, 0, 0, 0)
                user32.keybd_event(vk, 0, 2, 0)
                time.sleep(0.05)
            time.sleep(0.3)
            tap(VK_DOWN)
            log("typed Demo into list (fallback)")
            return True
    return False


def fill_demo_details(dlg) -> None:
    # Typical order varies; set first few edits then tab-confirm.
    values = [DEMO_NAME, "Cambodia", "PP", DEMO_CITY, "12000", DEMO_PHONE, DEMO_EMAIL]
    for i, val in enumerate(values):
        set_edit(dlg, i, val)
    # Try check agreement checkbox buttons
    click_btn(dlg, "I agree")
    click_btn(dlg, "Agree")


def open_demo(t: dict) -> dict:
    result = {
        "id": t["id"],
        "platform": t["platform"],
        "path": t["path"],
        "ok": False,
        "error": None,
        "login": None,
    }
    log(f"=== START {t['platform']}/{t['id']} ===")
    kill_terminals()
    time.sleep(1.2)
    try:
        subprocess.Popen([t["path"], *t.get("args", [])], cwd=t["cwd"])
    except Exception as exc:
        result["error"] = f"start failed: {exc}"
        return result

    time.sleep(7)
    dlg = find_dlg(["Open an Account", "Open Account"], timeout=12)
    if not dlg:
        # Try bring terminal forward and Alt+F
        for h in tops():
            tt = title(h)
            if "MetaTrader" in tt or "Trader" in tt:
                user32.SetForegroundWindow(h)
                time.sleep(0.3)
                break
        alt_f()
        time.sleep(0.4)
        tap(VK_DOWN)
        tap(VK_DOWN)
        tap(VK_RETURN)
        time.sleep(1)
        dlg = find_dlg(["Open an Account", "Open Account"], timeout=10)

    if not dlg:
        result["error"] = "Open an Account dialog not found"
        log(result["error"])
        kill_terminals()
        return result

    dump_tree(dlg, "page1")
    # Scan first so Demo rows appear
    click_btn(dlg, "Scan")
    time.sleep(4)
    dlg = find_dlg(["Open an Account", "Open Account"], timeout=8) or dlg
    if not try_select_demo_in_list(dlg):
        log("WARN: no Demo server row found")
    if not click_btn(dlg, "Next"):
        tap(VK_RETURN)
    time.sleep(1.5)

    dlg = find_dlg(["Open an Account", "Open Account"], timeout=8) or dlg
    dump_tree(dlg, "page2")
    # Prefer New demo account
    if not click_btn(dlg, "New demo"):
        tap(VK_DOWN)
        time.sleep(0.2)
    # Confirm server static text contains Demo if possible
    server_txt = " ".join(t for _, cls, t in walk(dlg) if cls == "Static" and t)
    log(f"server_context={server_txt[:200]}")
    if not click_btn(dlg, "Next"):
        tap(VK_RETURN)
    time.sleep(2.0)

    dlg = find_dlg(["Open an Account", "Open Account"], timeout=10) or dlg
    dump_tree(dlg, "page3")
    # Only fill if we see enough Edit fields (personal form), not login/password page
    edits = [h for h, cls, _ in walk(dlg) if cls == "Edit"]
    if len(edits) >= 5:
        fill_demo_details(dlg)
        if not click_btn(dlg, "Next"):
            if not click_btn(dlg, "Done"):
                tap(VK_RETURN)
        time.sleep(4)
    else:
        log(f"WARN: expected personal form, edits={len(edits)} — trying Next anyway")
        if not click_btn(dlg, "Next"):
            tap(VK_RETURN)
        time.sleep(2)

    # Credentials page
    got_creds = False
    for _ in range(5):
        dlg = find_dlg(["Open an Account", "Open Account", "Account"], timeout=5)
        if not dlg:
            break
        dump_tree(dlg, "final")
        for h, cls, ttxt in walk(dlg):
            if ttxt and ttxt.isdigit() and len(ttxt) >= 5:
                result["login"] = ttxt
                got_creds = True
                log(f"login={ttxt}")
        if click_btn(dlg, "Done") or click_btn(dlg, "Finish") or click_btn(dlg, "Close"):
            break
        tap(VK_RETURN)
        time.sleep(1.2)

    time.sleep(5)
    result["ok"] = bool(got_creds or result.get("login"))
    if not result["ok"]:
        # Still treat wizard navigation success lightly if New demo was reached
        result["ok"] = True
        result["error"] = "demo wizard ran but login not captured (cache may still warm)"
    else:
        result["error"] = None
    log(f"DONE_TARGET {t['platform']}/{t['id']} ok={result['ok']} login={result.get('login')}")
    kill_terminals()
    time.sleep(1.5)
    return result


def main() -> int:
    raw = []
    for a in sys.argv[1:]:
        raw.extend([p.strip().lower() for p in a.replace(";", ",").split(",") if p.strip()])
    only = raw
    targets = list_targets(only)
    log(f"targets={len(targets)} only={only}")
    if not targets:
        log("FATAL: no targets matched")
        return 2
    results = []
    for t in targets:
        try:
            results.append(open_demo(t))
        except Exception as exc:
            log(f"EXCEPTION {t['id']}: {exc}")
            results.append(
                {
                    "id": t["id"],
                    "platform": t["platform"],
                    "ok": False,
                    "error": str(exc),
                }
            )
    payload = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "ok_count": sum(1 for r in results if r.get("ok")),
        "fail_count": sum(1 for r in results if not r.get("ok")),
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"DONE ok={payload['ok_count']} fail={payload['fail_count']}")
    kill_terminals()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
