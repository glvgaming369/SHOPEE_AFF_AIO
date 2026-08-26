from pathlib import Path

import gsheet_config as config_module
from gsheet_config import AppConfig


def test_load_falls_back_to_defaults_when_no_file_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "missing.json")

    cfg = AppConfig.load()

    assert cfg == AppConfig()


def test_load_falls_back_to_defaults_on_corrupt_json(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)

    cfg = AppConfig.load()

    assert cfg == AppConfig()


def test_load_ignores_unknown_keys_from_a_future_schema(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"spreadsheet_url": "abc", "some_future_field": 123}', encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)

    cfg = AppConfig.load()

    assert cfg.spreadsheet_url == "abc"


def test_save_then_load_round_trips(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path)

    original = AppConfig(spreadsheet_url="url", video_folder="folder", credentials_path="creds")
    original.save()

    assert AppConfig.load() == original
