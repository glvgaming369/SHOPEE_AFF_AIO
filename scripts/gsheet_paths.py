"""Shared folder-path normalization so folder-scoped dedup features agree on identity."""
from __future__ import annotations

from pathlib import Path


def normalize_folder(folder: str) -> str:
    """Canonical key for a folder path: resolved + lowercased (Windows paths are
    case-insensitive), so 'D:\\a' and 'd:\\A\\..\\a\\' are recognized as the same folder.
    """
    return str(Path(folder).resolve()).lower()
