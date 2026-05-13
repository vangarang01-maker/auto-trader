"""외국인 당일(전일) 순매수 동향 — KIS FHKST01010900"""
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_REAL_URL = "https://openapi.koreainvestment.com:9443"
_LARGE_SELL = 50_000  # 순매도 5만주 이상 → 대량 순매도


def get_foreign_flow(kis, stock_code: str) -> dict:
    """
    Returns:
        net_qty    : 외국인 순매수량 (양수=매수, 음수=매도)
        score_bonus: +10(순매수 ≥ 1천주) / -15(대량순매도) / 0(중립)
        label      : "외국인순매수" | "외국인대량순매도" | "외국인중립" | "데이터없음"
    """
    try:
        resp = requests.get(
            f"{_REAL_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers={
                "authorization": f"Bearer {kis._get_token()}",
                "appkey":        kis.app_key,
                "appsecret":     kis.app_secret,
                "tr_id":         "FHKST01010900",
                "custtype":      "P",
            },
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
            },
            timeout=10,
            verify=False,
        )
        data = resp.json()
        if data.get("rt_cd") != "0":
            return {"net_qty": 0, "score_bonus": 0, "label": "데이터없음"}

        out = data.get("output", {})

        def _i(val) -> int:
            try:
                return int(str(val).replace(",", "").replace("+", "").strip() or "0")
            except (ValueError, TypeError):
                return 0

        net_qty = _i(out.get("frgn_ntby_qty", ""))
        if net_qty == 0:
            net_qty = _i(out.get("frgn_shnu_vol", 0)) - _i(out.get("frgn_seln_vol", 0))

        if net_qty >= 1_000:
            return {"net_qty": net_qty, "score_bonus": 10,  "label": "외국인순매수"}
        elif net_qty <= -_LARGE_SELL:
            return {"net_qty": net_qty, "score_bonus": -15, "label": "외국인대량순매도"}
        else:
            return {"net_qty": net_qty, "score_bonus": 0,   "label": "외국인중립"}

    except Exception:
        return {"net_qty": 0, "score_bonus": 0, "label": "데이터없음"}
