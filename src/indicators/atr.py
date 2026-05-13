"""ATR (Average True Range) — Wilder 방식"""


def calc_atr(ohlcv: list[dict], period: int = 14) -> float:
    """
    ohlcv: [{high, low, close}, ...] 오래된 순.
    데이터 부족 시 float('nan') 반환.
    """
    if len(ohlcv) < period + 1:
        return float("nan")

    true_ranges = []
    for i in range(1, len(ohlcv)):
        h      = ohlcv[i]["high"]
        lo     = ohlcv[i]["low"]
        prev_c = ohlcv[i - 1]["close"]
        true_ranges.append(max(h - lo, abs(h - prev_c), abs(lo - prev_c)))

    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 2)
