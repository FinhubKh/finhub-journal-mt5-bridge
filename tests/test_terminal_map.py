from pathlib import Path

from workers.terminal_lock import lock_key_for_terminal
from workers.terminal_map import list_terminal_slots, pick_terminal_path, resolve_terminal_path


def test_longest_prefix_wins(tmp_path: Path):
    a = tmp_path / "a" / "terminal64.exe"
    b = tmp_path / "b" / "terminal64.exe"
    a.parent.mkdir(); b.parent.mkdir()
    a.write_text("x"); b.write_text("x")
    default = tmp_path / "default" / "terminal64.exe"
    default.parent.mkdir(); default.write_text("x")

    map_path = tmp_path / "map.json"
    map_path.write_text(
        f'''{{
          "default": "{default.as_posix()}",
          "prefixes": {{
            "XM-": "{a.as_posix()}",
            "XMGlobal": "{b.as_posix()}"
          }}
        }}''',
        encoding="utf-8",
    )

    assert resolve_terminal_path(
        "XMGlobal-MT5 2",
        default_path=str(default),
        map_path=str(map_path),
    ) == str(b)

    assert resolve_terminal_path(
        "XM-MT5",
        default_path=str(default),
        map_path=str(map_path),
    ) == str(a)


def test_missing_mapped_file_falls_back(tmp_path: Path):
    default = tmp_path / "default" / "terminal64.exe"
    default.parent.mkdir(); default.write_text("x")
    map_path = tmp_path / "map.json"
    map_path.write_text(
        f'''{{
          "default": "{default.as_posix()}",
          "prefixes": {{
            "Exness-": "C:/does/not/exist/terminal64.exe"
          }}
        }}''',
        encoding="utf-8",
    )
    assert resolve_terminal_path(
        "Exness-MT5Real36",
        default_path=str(default),
        map_path=str(map_path),
    ) == str(default)


def test_lock_key_differs_by_terminal_path():
    a = lock_key_for_terminal("mt5", r"C:\finhubkh\mt5-slot-1\terminal64.exe", fallback_key="legacy")
    b = lock_key_for_terminal("mt5", r"C:\finhubkh\mt5-slot-2\terminal64.exe", fallback_key="legacy")
    assert a != b
    assert a.startswith("finhubkh:mt5:lock:")
    assert lock_key_for_terminal("mt4", "", fallback_key="legacy-mt4") == "legacy-mt4"


def test_list_terminal_slots_filters_missing(tmp_path: Path):
    slot1 = tmp_path / "slot1" / "terminal64.exe"
    slot1.parent.mkdir(); slot1.write_text("x")
    default = tmp_path / "default" / "terminal64.exe"
    default.parent.mkdir(); default.write_text("x")
    map_path = tmp_path / "map.json"
    map_path.write_text(
        f'''{{
          "default": "{default.as_posix()}",
          "slots": [
            "{slot1.as_posix()}",
            "{(tmp_path / "missing" / "terminal64.exe").as_posix()}"
          ],
          "prefixes": {{}}
        }}''',
        encoding="utf-8",
    )
    slots = list_terminal_slots(default_path=str(default), map_path=str(map_path))
    assert str(slot1) in slots
    assert str(default) in slots
    assert len(slots) == 2


class _FakeRedis:
    def __init__(self, held_keys=None):
        self.held = set(held_keys or [])

    def get(self, key):
        return "1" if key in self.held else None


def test_pick_terminal_path_skips_held_slot(tmp_path: Path):
    slot1 = tmp_path / "slot1" / "terminal64.exe"
    slot2 = tmp_path / "slot2" / "terminal64.exe"
    slot1.parent.mkdir(); slot2.parent.mkdir()
    slot1.write_text("x"); slot2.write_text("x")
    map_path = tmp_path / "map.json"
    map_path.write_text(
        f'''{{
          "default": "{slot1.as_posix()}",
          "slots": ["{slot1.as_posix()}", "{slot2.as_posix()}"],
          "prefixes": {{}}
        }}''',
        encoding="utf-8",
    )
    held_key = lock_key_for_terminal("mt5", str(slot1), fallback_key="legacy")
    redis = _FakeRedis(held_keys={held_key})
    picked = pick_terminal_path(
        "UnknownBroker",
        platform="mt5",
        default_path=str(slot1),
        map_path=str(map_path),
        redis_client=redis,
        fallback_lock_key="legacy",
    )
    assert picked == str(slot2)
