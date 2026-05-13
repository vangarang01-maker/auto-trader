"""KOSPI 200일 이동평균 기반 시장 국면 판별"""
from datetime import date, timedelta

import FinanceDataReader as fdr


def get_market_regime() -> dict:
    """
    Returns:
        regime   : 'bull' | 'bear' | 'unknown'
        kospi    : 현재 KOSPI 지수
        ma200    : 200일 이동평균
        pct_diff : (kospi / ma200 - 1) * 100
    """
    try:
        start = (date.today() - timedelta(days=320)).strftime("%Y-%m-%d")
        close = fdr.DataReader("KS11", start)["Close"].dropna()
        if len(close) < 200:
            return {"regime": "unknown", "kospi": None, "ma200": None, "pct_diff": None}
        kospi    = round(float(close.iloc[-1]), 2)
        ma200    = round(float(close.tail(200).mean()), 2)
        pct_diff = round((kospi / ma200 - 1) * 100, 2)
        return {
            "regime":   "bull" if kospi > ma200 else "bear",
            "kospi":    kospi,
            "ma200":    ma200,
            "pct_diff": pct_diff,
        }
    except Exception as e:
        print(f"  [시장 국면 오류] {e}")
        return {"regime": "unknown", "kospi": None, "ma200": None, "pct_diff": None}
