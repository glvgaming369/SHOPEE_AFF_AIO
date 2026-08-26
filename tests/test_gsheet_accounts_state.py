from pathlib import Path

import gsheet_accounts_state as accounts_state_module
from gsheet_account import AccountRow
from gsheet_accounts_state import load_accounts, save_accounts


def _use_temp_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(accounts_state_module, "STATE_PATH", tmp_path / "accounts_state.json")
    monkeypatch.setattr(accounts_state_module, "DATA_DIR", tmp_path)


def test_load_returns_empty_list_when_no_file_exists(tmp_path: Path, monkeypatch):
    _use_temp_state(tmp_path, monkeypatch)

    assert load_accounts() == []


def test_load_returns_empty_list_on_corrupt_json(tmp_path: Path, monkeypatch):
    _use_temp_state(tmp_path, monkeypatch)
    accounts_state_module.STATE_PATH.write_text("{not valid", encoding="utf-8")

    assert load_accounts() == []


def test_save_then_load_round_trips(tmp_path: Path, monkeypatch):
    _use_temp_state(tmp_path, monkeypatch)
    rows = [
        AccountRow(profile="acc_a", selected=True, qty=5, current_jobs=12),
        AccountRow(profile="acc_b", selected=False, qty=0, current_jobs=0),
    ]

    save_accounts(rows)

    assert load_accounts() == rows


def test_load_skips_malformed_entries_but_keeps_valid_ones(tmp_path: Path, monkeypatch):
    _use_temp_state(tmp_path, monkeypatch)
    accounts_state_module.STATE_PATH.write_text(
        '[{"profile": "ok", "selected": true, "qty": 3}, {"no_profile_key": true}, "not_a_dict"]',
        encoding="utf-8",
    )

    assert load_accounts() == [AccountRow(profile="ok", selected=True, qty=3)]


def test_load_clamps_negative_qty_to_zero(tmp_path: Path, monkeypatch):
    _use_temp_state(tmp_path, monkeypatch)
    accounts_state_module.STATE_PATH.write_text(
        '[{"profile": "acc", "selected": true, "qty": -5}]', encoding="utf-8"
    )

    assert load_accounts() == [AccountRow(profile="acc", selected=True, qty=0)]
