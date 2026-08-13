from pathlib import Path

from workers.terminal_map import resolve_terminal_path


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
