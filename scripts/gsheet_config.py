"""Persist the last-used spreadsheet URL, video folder, and credentials path locally."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "gsheet_push"
CONFIG_PATH = DATA_DIR / "config.json"


@dataclass
class AppConfig:
    spreadsheet_url: str = ""
    video_folder: str = ""
    credentials_path: str = ""

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**{**asdict(cls()), **filtered})

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
