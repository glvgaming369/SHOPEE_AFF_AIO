"""Tim chrome.exe va mo 1 tien trinh Chrome rieng cho 1 profile (--user-data-dir +
--profile-directory) - dung khi bam "Launch" o UI quan ly tai khoan. Moi profile la 1
tien trinh Chrome doc lap (ngay ca khi Chrome dang mo o profile khac roi), cho phep chay
song song nhieu tai khoan nhu da chot voi nguoi dung.

profile_path luu trong DB la duong dan THU MUC PROFILE day du (vd
"C:\\Users\\Administrator\\AppData\\Local\\Google\\Chrome\\User Data\\Profile 7"), nhung
Chrome CLI can tach rieng 2 phan: --user-data-dir la thu muc "User Data" CHA, con
--profile-directory chi la TEN thu muc con (vd "Profile 7") - xem split_profile_path().
"""
import os
import shutil
import subprocess

COMMON_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_chrome_exe():
    """Tra ve duong dan chrome.exe, hoac None neu khong tim thay o cac vi tri thuong gap
    lan trong PATH."""
    which = shutil.which("chrome") or shutil.which("chrome.exe")
    if which:
        return which
    for p in COMMON_CHROME_PATHS:
        if os.path.isfile(p):
            return p
    return None


def split_profile_path(profile_path):
    """"...\\User Data\\Profile 7" -> ("...\\User Data", "Profile 7"). Neu path khong co
    thu muc cha (truong hop la duong dan la), tra ve (profile_path, "Default") - it xay ra
    trong thuc te vi nguoi dung luon nhap duong dan profile day du."""
    profile_path = profile_path.rstrip("\\/")
    parent = os.path.dirname(profile_path)
    name = os.path.basename(profile_path)
    if not parent or not name:
        return profile_path, "Default"
    return parent, name


def launch_profile(profile_path, url, chrome_exe=None):
    """Mo 1 tien trinh Chrome moi cho dung profile_path nay, dieu huong toi url. Tra ve
    subprocess.Popen (khong cho - goi khong block). Nem loi neu khong tim thay chrome.exe
    hoac profile_path rong."""
    if not profile_path:
        raise ValueError("profile_path rong")
    chrome_exe = chrome_exe or find_chrome_exe()
    if not chrome_exe:
        raise RuntimeError(
            "Khong tim thay chrome.exe (da thu PATH va cac vi tri cai dat thuong gap). "
            "Kiem tra Chrome da cai dung chuan chua, hoac sua COMMON_CHROME_PATHS."
        )
    user_data_dir, profile_dir = split_profile_path(profile_path)
    args = [
        chrome_exe,
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_dir}",
        url,
    ]
    return subprocess.Popen(args)
