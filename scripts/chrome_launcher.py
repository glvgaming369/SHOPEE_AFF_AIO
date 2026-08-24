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

# QUAN TRONG: --user-data-dir RIENG BIET HOAN TOAN, KHONG dung chung goc "Google\Chrome\User
# Data" voi cac profile trong bang 'devices' - da kiem chung THAT (2026-08-25) dung chung
# goc do (chi khac --profile-directory) van bi Chrome GOP vao dung cua so dang mo san (co
# che khoa singleton cua Chrome tinh theo --user-data-dir, KHONG phai --profile-directory -
# van de nay da tung gay bug "double-bind" tuong tu voi cac profile devices khac trong du
# an nay). Dung 1 thu muc GOC hoan toan rieng (ngoai "Google\Chrome") moi dam bao Chrome mo
# TIEN TRINH/CUA SO DOC LAP that su, khong dung cham gi den cua so Chrome nguoi dung dang
# lam viec.
ACTIVATION_LINK_USER_DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\ShopeeToolChromeProfiles")
ACTIVATION_LINK_PROFILE_NAME = "ActivationLinks"


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


def launch_activation_link(url, chrome_exe=None):
    """Mo link kich hoat dang nhap (nut "Kích hoạt link đăng nhập") bang 1 CUA SO Chrome
    RIENG BIET, dung 1 profile CO DINH chi danh cho tinh nang nay (KHONG dung profile/tai
    khoan Shopee nao dang dang ky trong 'devices') - dam bao KHONG dieu huong/chiem tab
    nguoi dung dang lam viec o cua so Chrome chinh.

    Da thu qua "--new-tab" truoc do nhung KHONG du: Chrome van TU CHUYEN tab dang xem
    sang tab moi (da kiem chung that 2026-08-25 - GetForegroundWindow cap he dieu hanh
    khong doi, nhung tab DANG CHON trong CHINH cua so Chrome nguoi dung van bi doi). Mo O
    CUA SO KHAC (profile rieng) la cach DUY NHAT tranh hoan toan viec nay qua CLI thuong -
    Chrome khong co co chinh thuc de mo "tab nen" (background tab) tu ben ngoai; mo tab
    nen that su can Chrome DevTools Protocol (Target.createTarget voi background=true),
    doi hoi Chrome dang chay phai bat san --remote-debugging-port - nguoi dung 2026-08-25
    chon phuong an don gian hon nay (cua so rieng) thay vi doi cach mo Chrome hang ngay.

    Profile duoc Chrome TU TAO (rong) trong lan mo dau tien, cac lan sau dung lai (giu
    cookie/session dang nhap giua cac lan kich hoat link) - khong can dang ky truoc trong DB."""
    profile_path = os.path.join(ACTIVATION_LINK_USER_DATA_DIR, ACTIVATION_LINK_PROFILE_NAME)
    return launch_profile(profile_path, url, chrome_exe=chrome_exe)
