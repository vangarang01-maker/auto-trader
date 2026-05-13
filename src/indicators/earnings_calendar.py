"""DART 잠정실적 공시 기반 실적 발표 리스크 감지"""
from datetime import date, timedelta

_EARNINGS_KEYWORDS = ["잠정실적", "영업(잠정)실적", "연간실적", "분기실적"]


def get_earnings_risk(dart_client, corp_code: str, days_lookback: int = 7) -> dict:
    """
    최근 N일 이내 잠정실적 공시 여부 확인.

    Returns:
        risk  : 공시 발견 시 True
        label : "실적공시" | "공시없음" | "오류"
        detail: 공시 제목 + 날짜 (risk=True 시)
    """
    end   = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=days_lookback)).strftime("%Y%m%d")
    try:
        disc = dart_client.list(corp_code, start=start, end=end)
        if disc is None or disc.empty:
            return {"risk": False, "label": "공시없음", "detail": ""}
        title_col = "report_nm" if "report_nm" in disc.columns else disc.columns[2]
        for _, row in disc.iterrows():
            title = str(row.get(title_col, ""))
            if any(k in title for k in _EARNINGS_KEYWORDS):
                filed = str(row.get("rcept_dt", ""))[:8]
                return {"risk": True, "label": "실적공시", "detail": f"{title} ({filed})"}
        return {"risk": False, "label": "공시없음", "detail": ""}
    except Exception:
        return {"risk": False, "label": "오류", "detail": ""}
