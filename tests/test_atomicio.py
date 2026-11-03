from __future__ import annotations

from pathlib import Path

from voxpress.atomicio import atomic_write_json, read_json, read_json_checked


def test_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"詞": "彙"})
    assert read_json_checked(target) == (True, {"詞": "彙"}, None)


def test_absent_file(tmp_path: Path) -> None:
    assert read_json_checked(tmp_path / "missing.json", {}) == (False, {}, "absent")


def test_invalid_json_is_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "bad.json"
    target.write_text("{not json", encoding="utf-8")
    assert read_json_checked(target, {}) == (False, {}, "corrupt")


def test_invalid_utf8_is_corrupt_not_an_exception(tmp_path: Path) -> None:
    # e.g. a dictionary saved by an editor as Big5/cp950 instead of UTF-8.
    target = tmp_path / "big5.json"
    target.write_bytes('{"詞": "彙"}'.encode("cp950"))
    assert read_json_checked(target, {}) == (False, {}, "corrupt")
    assert read_json(target, {"fallback": True}) == {"fallback": True}
