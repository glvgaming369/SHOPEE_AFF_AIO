from pathlib import Path

import gsheet_push_cache as push_cache_module
from gsheet_push_cache import add_pushed_ids, clear_folder_cache, get_pushed_ids


def _use_temp_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(push_cache_module, "CACHE_PATH", tmp_path / "push_cache.json")
    monkeypatch.setattr(push_cache_module, "DATA_DIR", tmp_path)


def test_get_pushed_ids_is_empty_when_no_cache_file(tmp_path: Path, monkeypatch):
    _use_temp_cache(tmp_path, monkeypatch)

    assert get_pushed_ids(str(tmp_path)) == set()


def test_add_then_get_round_trips_for_the_same_folder(tmp_path: Path, monkeypatch):
    _use_temp_cache(tmp_path, monkeypatch)

    add_pushed_ids(str(tmp_path), ["111", "222"])

    assert get_pushed_ids(str(tmp_path)) == {"111", "222"}


def test_cache_is_scoped_per_folder(tmp_path: Path, monkeypatch):
    _use_temp_cache(tmp_path, monkeypatch)
    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()

    add_pushed_ids(str(folder_a), ["111"])

    assert get_pushed_ids(str(folder_a)) == {"111"}
    assert get_pushed_ids(str(folder_b)) == set()


def test_clear_folder_cache_removes_only_that_folder(tmp_path: Path, monkeypatch):
    _use_temp_cache(tmp_path, monkeypatch)
    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()
    add_pushed_ids(str(folder_a), ["111"])
    add_pushed_ids(str(folder_b), ["222"])

    cleared = clear_folder_cache(str(folder_a))

    assert cleared is True
    assert get_pushed_ids(str(folder_a)) == set()
    assert get_pushed_ids(str(folder_b)) == {"222"}


def test_clear_folder_cache_returns_false_when_nothing_to_clear(tmp_path: Path, monkeypatch):
    _use_temp_cache(tmp_path, monkeypatch)

    assert clear_folder_cache(str(tmp_path)) is False


def test_add_pushed_ids_is_idempotent_across_repeated_runs(tmp_path: Path, monkeypatch):
    _use_temp_cache(tmp_path, monkeypatch)

    add_pushed_ids(str(tmp_path), ["111"])
    add_pushed_ids(str(tmp_path), ["111", "222"])

    assert get_pushed_ids(str(tmp_path)) == {"111", "222"}
