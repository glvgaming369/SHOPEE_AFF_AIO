"""Local, folder-scoped record of SP IDs already pushed.

This sits in front of the (authoritative but slower) live Google Sheet check: it lets a
re-run skip already-pushed videos from a given folder without an API call, and gives the
user an explicit way to forget a folder if they intentionally want to push it again. The
Sheet check in gsheet_sheets_client.get_used_video_ids() still runs regardless, so clearing
this cache can never cause a real duplicate row to land in the spreadsheet — it only forgets
the local fast-path memory.
"""
from __future__ import annotations

import json
from pathlib import Path

from gsheet_config import DATA_DIR
from gsheet_paths import normalize_folder

CACHE_PATH = DATA_DIR / "push_cache.json"


def _load_raw() -> dict[str, list[str]]:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_raw(data: dict[str, list[str]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_pushed_ids(folder: str) -> set[str]:
    return set(_load_raw().get(normalize_folder(folder), []))


def add_pushed_ids(folder: str, sp_ids: list[str]) -> None:
    if not sp_ids:
        return
    key = normalize_folder(folder)
    data = _load_raw()
    existing = set(data.get(key, []))
    existing.update(sp_ids)
    data[key] = sorted(existing)
    _save_raw(data)


def clear_folder_cache(folder: str) -> bool:
    """Forget this folder's cache. Returns True if there was anything to clear."""
    key = normalize_folder(folder)
    data = _load_raw()
    if key not in data:
        return False
    del data[key]
    _save_raw(data)
    return True
