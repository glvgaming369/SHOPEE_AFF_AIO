from pathlib import Path

from gsheet_paths import normalize_folder


def test_normalize_folder_is_case_insensitive_on_windows_style_paths():
    assert normalize_folder("D:/Videos/Box1") == normalize_folder("d:/videos/BOX1")


def test_normalize_folder_resolves_relative_segments(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    via_dotdot = tmp_path / "a" / "c" / ".." / "b"

    assert normalize_folder(str(nested)) == normalize_folder(str(via_dotdot))


def test_normalize_folder_distinguishes_different_folders(tmp_path: Path):
    folder_a = tmp_path / "folder_a"
    folder_b = tmp_path / "folder_b"

    assert normalize_folder(str(folder_a)) != normalize_folder(str(folder_b))
