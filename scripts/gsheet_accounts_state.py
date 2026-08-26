"""Persist the imported-accounts table (profile, selected, qty) between tool sessions."""
from __future__ import annotations

import json
from pathlib import Path

from gsheet_account import AccountRow
from gsheet_config import DATA_DIR

STATE_PATH = DATA_DIR / "accounts_state.json"


def load_accounts() -> list[AccountRow]:
    if not STATE_PATH.exists():
        return []
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []

    rows: list[AccountRow] = []
    for item in data:
        if not isinstance(item, dict) or "profile" not in item:
            continue
        try:
            rows.append(
                AccountRow(
                    profile=str(item["profile"]),
                    selected=bool(item.get("selected", True)),
                    qty=max(0, int(item.get("qty", 0))),
                    current_jobs=max(0, int(item.get("current_jobs", 0))),
                    completed_jobs=max(0, int(item.get("completed_jobs", 0))),
                    pending_jobs=max(0, int(item.get("pending_jobs", 0))),
                )
            )
        except (TypeError, ValueError):
            continue
    return rows


def save_accounts(rows: list[AccountRow]) -> None:
    data = [
        {
            "profile": r.profile,
            "selected": r.selected,
            "qty": r.qty,
            "current_jobs": r.current_jobs,
            "completed_jobs": r.completed_jobs,
            "pending_jobs": r.pending_jobs,
        }
        for r in rows
    ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
