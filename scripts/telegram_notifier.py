"""Gui thong bao Telegram khi phat hien captcha trong luc chay UI automation.

Rut gon tu D:\\Shopee369\\3-dang\\src\\telegram_notifier.py (chi giu phan can cho du an
nay: gui tin qua urllib thuan - khong can them thu vien requests, thread nen + hang doi
de tranh spam/flood, SSL context bo verify de tranh loi cert tren Windows). Bo cac ham
khong lien quan (device offline, quota, session summary) vi du an nay khong co khai
niem "thiet bi"/"quota".

Doc TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tu .env qua python-dotenv (xem .env.example).

Chay truc tiep de test ket noi that:
    python scripts/telegram_notifier.py
"""
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = (bot_token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.enabled = bool(self.bot_token and self.chat_id)
        self._queue = []
        self._lock = threading.Lock()

        # Bo qua verify SSL - tranh loi CERTIFICATE_VERIFY_FAILED hay gap tren Windows/
        # Python cu khi goi Telegram API (giong module tham khao).
        try:
            self.ssl_context = ssl._create_unverified_context()
        except AttributeError:
            self.ssl_context = None

        if self.enabled:
            threading.Thread(target=self._sender_loop, daemon=True).start()

    # ── Public API ──────────────────────────────────────────────────────────

    def notify_captcha(self, item_id: str, link: str):
        msg = (
            f"🛡️ <b>PHÁT HIỆN CAPTCHA</b>\n"
            f"🔗 Item: <code>{self.esc(item_id)}</code>\n"
            f"{self.esc(link)}\n"
            f"⚠️ Vui lòng vào điện thoại giải captcha để tool tiếp tục.\n"
            f"🕐 {self._now()}"
        )
        self._enqueue(msg)

    def notify_captcha_resolved(self, item_id: str):
        msg = (
            f"✅ <b>ĐÃ GIẢI CAPTCHA</b>\n"
            f"🔗 Item: <code>{self.esc(item_id)}</code> — tool tiếp tục chạy.\n"
            f"🕐 {self._now()}"
        )
        self._enqueue(msg)

    def notify_captcha_timeout(self, item_id: str):
        msg = (
            f"⏰ <b>HẾT GIỜ CHỜ CAPTCHA</b>\n"
            f"🔗 Item: <code>{self.esc(item_id)}</code> — bỏ qua item này, tool tiếp tục "
            f"link tiếp theo.\n"
            f"🕐 {self._now()}"
        )
        self._enqueue(msg)

    def send_custom(self, message: str):
        self._enqueue(message)

    def test_connection(self) -> tuple[bool, str]:
        """Gui tin nhan test dong bo, tra ve (thanh cong, thong bao loi)."""
        if not self.bot_token or not self.chat_id:
            return False, "Thieu Bot Token hoac Chat ID."
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.chat_id,
                "text": "🔔 <b>KẾT NỐI THÀNH CÔNG!</b>\nTin nhắn kiểm tra từ Crawl_Shopee_appMobile.",
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10, context=self.ssl_context) as resp:
                if resp.status == 200:
                    return True, "Đã gửi tin nhắn test thành công."
                return False, f"HTTP {resp.status}"
        except Exception as e:
            return False, f"Loi: {e}"

    # ── Internal ────────────────────────────────────────────────────────────

    def _enqueue(self, message: str):
        if not self.enabled:
            return
        with self._lock:
            self._queue.append(message)

    def _sender_loop(self):
        while True:
            item = None
            with self._lock:
                if self._queue:
                    item = self._queue.pop(0)
            if item:
                self._send(item)
                time.sleep(1.0)  # throttle nhe, tranh flood (Telegram khuyen ~1 msg/s)
            else:
                time.sleep(0.5)

    def _send(self, text: str):
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10, context=self.ssl_context):
                pass
        except Exception as e:
            print(f"[Telegram] Gui that bai: {e}. Noi dung: {text}")

    @staticmethod
    def esc(text) -> str:
        if not text:
            return ""
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%H:%M:%S %d/%m/%Y")


def load_notifier_from_env(env_path=".env") -> TelegramNotifier:
    """Doc TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tu .env (qua python-dotenv). Neu thieu
    thi tra ve notifier voi enabled=False (khong crash - cac ham notify_* chi la no-op)."""
    from dotenv import load_dotenv
    import os

    load_dotenv(env_path)
    return TelegramNotifier(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )


if __name__ == "__main__":
    notifier = load_notifier_from_env()
    if not notifier.enabled:
        print("CANH BAO: thieu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID trong .env - "
              "copy .env.example thanh .env roi dien gia tri that.")
    else:
        ok, msg = notifier.test_connection()
        print(f"test_connection: ok={ok} | {msg}")
