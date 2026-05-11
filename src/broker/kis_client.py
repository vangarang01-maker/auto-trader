import os
import json
import time
import warnings
import requests
import urllib3
from dotenv import load_dotenv

load_dotenv()

# KIS 모의투자 서버 SSL 인증서 호스트명 불일치 문제 우회
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REAL_URL    = "https://openapi.koreainvestment.com:9443"
VIRTUAL_URL = "https://openapivts.koreainvestment.com:29443"


class KISClient:
    def __init__(self, virtual: bool = False):
        self.virtual = virtual
        if virtual:
            self.app_key    = os.getenv("KIS_VIRTUAL_APP_KEY")
            self.app_secret = os.getenv("KIS_VIRTUAL_APP_SECRET")
            self.base_url   = VIRTUAL_URL
            self._token_file = ".kis_token_virtual.json"
        else:
            self.app_key    = os.getenv("KIS_APP_KEY")
            self.app_secret = os.getenv("KIS_APP_SECRET")
            self.base_url   = REAL_URL
            self._token_file = ".kis_token.json"

        if not self.app_key or not self.app_secret:
            key = "KIS_VIRTUAL_APP_KEY/SECRET" if virtual else "KIS_APP_KEY/SECRET"
            raise ValueError(f"{key}가 .env에 설정되지 않았습니다.")

        account = os.getenv("KIS_ACCOUNT", "")
        parts = account.replace("-", "")
        self.cano     = parts[:8]   # 계좌번호 앞 8자리
        self.acnt_cd  = parts[8:] or "01"  # 계좌상품코드

        self._token: str | None = None
        self._token_expires_at: float = 0
        self._load_cached_token()

    # ── 토큰 ──────────────────────────────────────────────

    def _load_cached_token(self):
        try:
            with open(self._token_file) as f:
                data = json.load(f)
            if data.get("expires_at", 0) > time.time() + 60:
                self._token = data["token"]
                self._token_expires_at = data["expires_at"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            pass

    def _save_cached_token(self):
        with open(self._token_file, "w") as f:
            json.dump({"token": self._token, "expires_at": self._token_expires_at}, f)

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        resp = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=30,
            verify=False,
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

    # ── 시세 ──────────────────────────────────────────────

    def get_stock_quote(self, stock_code: str) -> dict:
        """현재가·EPS·PER 조회"""
        for attempt in range(2):
            try:
                resp = requests.get(
                    f"{REAL_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                    headers={
                        "authorization": f"Bearer {self._get_token()}",
                        "appkey": self.app_key,
                        "appsecret": self.app_secret,
                        "tr_id": "FHKST01010100",
                        "custtype": "P",
                    },
                    params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
                    timeout=30,
                    verify=False,
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt == 1:
                    raise
                time.sleep(2)
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

    def get_stock_valuation(self, stock_code: str) -> dict:
        """PER·PBR·배당수익률·현재가 조회 (건강검진용)."""
        for attempt in range(2):
            try:
                resp = requests.get(
                    f"{REAL_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                    headers={
                        "authorization": f"Bearer {self._get_token()}",
                        "appkey": self.app_key,
                        "appsecret": self.app_secret,
                        "tr_id": "FHKST01010100",
                        "custtype": "P",
                    },
                    params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
                    timeout=30,
                    verify=False,
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt == 1:
                    raise
                time.sleep(2)
        resp.raise_for_status()
        out = resp.json().get("output", {})
        div = float(out.get("divi_rate") or 0) or float(out.get("dvol_per") or 0) or None
        return {
            "price":     int(out.get("stck_prpr") or 0),
            "per":       float(out.get("per") or 0) or None,
            "pbr":       float(out.get("pbr") or 0) or None,
            "div_yield": div,
        }

    def get_current_price(self, stock_code: str) -> int:
        return self.get_stock_quote(stock_code)["price"]

    def get_daily_prices(self, stock_code: str, count: int = 60) -> list[float]:
        """최근 N 거래일 종가 리스트 (오래된 순). 시세는 항상 실서버."""
        from datetime import datetime, timedelta
        end   = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")
        for attempt in range(2):
            try:
                resp = requests.get(
                    f"{REAL_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                    headers={
                        "authorization": f"Bearer {self._get_token()}",
                        "appkey": self.app_key,
                        "appsecret": self.app_secret,
                        "tr_id": "FHKST03010100",
                        "custtype": "P",
                    },
                    params={
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD": stock_code,
                        "FID_INPUT_DATE_1": start,
                        "FID_INPUT_DATE_2": end,
                        "FID_PERIOD_DIV_CODE": "D",
                        "FID_ORG_ADJ_PRC": "1",
                    },
                    timeout=30,
                    verify=False,
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt == 1:
                    raise
                time.sleep(2)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"KIS 오류: {data.get('msg1')}")
        items = data.get("output2", [])
        prices = [float(item["stck_clpr"]) for item in reversed(items) if item.get("stck_clpr")]
        return prices[-count:]

    # ── 잔고 ──────────────────────────────────────────────

    def get_holdings(self) -> list[dict]:
        """보유 종목 조회. [{stock_code, qty, avg_price}]"""
        tr_id = "VTTC8434R" if self.virtual else "TTTC8434R"
        resp = requests.get(
            f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self._headers(tr_id),
            params={
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            timeout=30,
            verify=False,
        )
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"잔고 조회 오류: {data.get('msg1')} (HTTP {resp.status_code})")
        return [
            {
                "stock_code": item["pdno"],
                "qty": int(item["hldg_qty"]),
                "avg_price": float(item["pchs_avg_pric"]),
            }
            for item in data.get("output1", [])
            if int(item.get("hldg_qty", 0)) > 0
        ]

    # ── 주문 ──────────────────────────────────────────────

    def place_order(self, stock_code: str, side: str, qty: int) -> dict:
        """시장가 주문. side: 'buy' | 'sell'"""
        if self.virtual:
            tr_id = "VTTC0802U" if side == "buy" else "VTTC0801U"
        else:
            tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"

        resp = requests.post(
            f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
            headers=self._headers(tr_id),
            json={
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_cd,
                "PDNO": stock_code,
                "ORD_DVSN": "01",   # 시장가
                "ORD_QTY": str(qty),
                "ORD_UNPR": "0",
            },
            timeout=30,
            verify=False,
        )
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise ValueError(f"주문 오류({side} {stock_code} {qty}주): {data.get('msg1')} (HTTP {resp.status_code})")
        return data
