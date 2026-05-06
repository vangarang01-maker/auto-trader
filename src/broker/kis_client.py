import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_CACHE_FILE = ".kis_token.json"


class KISClient:
    def __init__(self):
        self.app_key = os.getenv("KIS_APP_KEY")
        self.app_secret = os.getenv("KIS_APP_SECRET")
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY, KIS_APP_SECRET가 .env에 설정되지 않았습니다.")
        self._token: str | None = None
        self._token_expires_at: float = 0
        self._load_cached_token()

    def _load_cached_token(self):
        try:
            with open(TOKEN_CACHE_FILE) as f:
                data = json.load(f)
            if data.get("expires_at", 0) > time.time() + 60:
                self._token = data["token"]
                self._token_expires_at = data["expires_at"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            pass

    def _save_cached_token(self):
        with open(TOKEN_CACHE_FILE, "w") as f:
            json.dump({"token": self._token, "expires_at": self._token_expires_at}, f)

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token

        resp = requests.post(
            f"{BASE_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 86400) - 60
        self._save_cached_token()
        return self._token

    def _headers(self, tr_id: str) -> dict:
        return {
            "authorization": f"Bearer {self._get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def get_stock_quote(self, stock_code: str) -> dict:
        """현재가·EPS·PER 조회"""
        resp = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self._headers("FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"KIS 오류: {data.get('msg1')}")
        out = data["output"]
        return {
            "price": int(out["stck_prpr"]),
            "eps":   float(out.get("eps") or 0),
            "per":   float(out.get("per") or 0),
        }

    def get_current_price(self, stock_code: str) -> int:
        return self.get_stock_quote(stock_code)["price"]
