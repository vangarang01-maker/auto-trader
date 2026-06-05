"""매수 게이트 백테스트 — 임계값별 신호 빈도 + 단순 수익 시뮬.

현재 picks 종목을 최근 6개월 일봉으로 돌려, RSI/거래량 임계 조합마다
- 매수 신호 발생 일수
- 신호 진입 시 익절(+15%)/손절(-7%)/RSI>=75 청산 수익률
을 집계한다. 코드 수정 전 합리적 임계 탐색용.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import FinanceDataReader as fdr

START, END = "2025-12-01", "2026-06-05"
RSI_PERIOD = 14
VOL_RATIO  = 1.5
TP, SL     = 0.15, 0.07
RSI_SELL   = 75


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def load_codes():
    out = []
    for f in ["picks_v1.json", "picks_v2.json", "picks_v3.json"]:
        for p in json.loads(Path(f).read_text(encoding="utf-8")):
            out.append((p["stock_code"], p["corp_name"]))
    # dedup, keep order
    seen, res = set(), []
    for c, n in out:
        if c not in seen:
            seen.add(c); res.append((c, n))
    return res


def simulate(df, rsi_thr, use_vol):
    """신호일 진입 → 익절/손절/RSI매도 청산. 종목 1개 시뮬.
    return: (signal_days, trades:list[ret%])"""
    rsi = wilder_rsi(df["Close"], RSI_PERIOD)
    avg20 = df["Volume"].rolling(20).mean().shift(1)
    vr = df["Volume"] / avg20

    buy = rsi < rsi_thr
    if use_vol:
        buy = buy & (vr >= VOL_RATIO)

    sig_days = int(buy.sum())

    closes = df["Close"].values
    rsis = rsi.values
    n = len(df)
    trades = []
    in_pos = False
    entry = 0.0
    for i in range(n):
        if not in_pos:
            if bool(buy.iloc[i]) and not np.isnan(closes[i]):
                in_pos = True; entry = closes[i]
        else:
            cur = closes[i]
            r = cur / entry - 1
            if r >= TP or r <= -SL or (not np.isnan(rsis[i]) and rsis[i] >= RSI_SELL):
                trades.append(r * 100); in_pos = False
    if in_pos:  # 미청산 평가손익
        trades.append((closes[-1] / entry - 1) * 100)
    return sig_days, trades


def main():
    codes = load_codes()
    data = {}
    for c, n in codes:
        try:
            df = fdr.DataReader(c, START, END)
            if len(df) >= 40:
                data[c] = (n, df)
        except Exception as e:
            print(f"  skip {c}: {e}")

    print(f"종목 {len(data)}개 / 기간 {START}~{END}\n")

    grid = [
        ("RSI<35 AND vol1.5x (현재)", 35, True),
        ("RSI<40 AND vol1.5x",        40, True),
        ("RSI<45 AND vol1.5x",        45, True),
        ("RSI<50 AND vol1.5x",        50, True),
        ("RSI<35 (vol무시)",          35, False),
        ("RSI<45 (vol무시)",          45, False),
        ("RSI<50 (vol무시)",          50, False),
    ]

    print(f"{'게이트':28} {'신호일':>6} {'매매수':>6} {'승률':>6} {'평균손익':>8} {'합계손익':>8}")
    print("-" * 70)
    for label, thr, uv in grid:
        tot_sig = 0; all_tr = []
        for c, (n, df) in data.items():
            s, tr = simulate(df, thr, uv)
            tot_sig += s; all_tr += tr
        if all_tr:
            wins = sum(1 for r in all_tr if r > 0)
            win_rate = wins / len(all_tr) * 100
            avg = sum(all_tr) / len(all_tr)
            tot = sum(all_tr)
        else:
            win_rate = avg = tot = 0
        print(f"{label:28} {tot_sig:6d} {len(all_tr):6d} {win_rate:5.0f}% {avg:7.1f}% {tot:7.1f}%")


if __name__ == "__main__":
    main()
