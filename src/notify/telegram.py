import os
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


def send_message(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("  [텔레그램] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 미설정 → 생략")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  [텔레그램] 전송 실패: {e}")
