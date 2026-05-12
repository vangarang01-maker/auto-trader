"""섹터 모멘텀 유틸리티

- get_top_sectors : 주어진 종목풀에서 최근 N개월 수익률 상위 섹터 추출
- apply_sector_filter : 상위 섹터에 속하는 종목만 통과 (종목 수 부족 시 원본 반환)
- calc_theme_bonus : 테마 분석 텍스트에 섹터가 언급되면 보너스 점수 반환
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import FinanceDataReader as fdr
import pandas as pd


def get_top_sectors(df: pd.DataFrame, months: int = 1, top_n: int = 3) -> set[str]:
    """
    df: stock_code + sector 컬럼 필요.
    최근 months개월 수익률 기준 상위 top_n 섹터 반환.
    섹터 데이터 부족 시 빈 set 반환 → 호출부에서 필터 생략.
    """
    if "sector" not in df.columns:
        return set()

    start = (date.today() - timedelta(days=months * 31)).strftime("%Y-%m-%d")
    sector_rets: dict[str, list[float]] = {}

    def _calc(row: dict) -> tuple[str, float] | None:
        sector = str(row.get("sector") or "").strip()
        if not sector or sector in ("-", "nan", "None", ""):
            return None
        try:
            prices = fdr.DataReader(str(row["stock_code"]), start)["Close"].dropna()
            if len(prices) < 10:
                return None
            return sector, (prices.iloc[-1] / prices.iloc[0] - 1) * 100
        except Exception:
            return None

    rows = [row for _, row in df.iterrows()]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(_calc, r) for r in rows]):
            r = f.result()
            if r:
                s, ret = r
                sector_rets.setdefault(s, []).append(ret)

    # 종목 2개 이상인 섹터만 집계
    avg = {s: sum(v) / len(v) for s, v in sector_rets.items() if len(v) >= 2}
    if not avg:
        return set()

    top = sorted(avg.items(), key=lambda x: -x[1])[:top_n]
    print(f"\n  [섹터 모멘텀] 최근 {months}개월 상위 {top_n}개 섹터:")
    for s, r in top:
        sign = "+" if r >= 0 else ""
        print(f"    {s}: {sign}{r:.1f}%")
    return {s for s, _ in top}


def apply_sector_filter(df: pd.DataFrame, top_sectors: set[str], min_keep: int = 5) -> pd.DataFrame:
    """
    top_sectors에 속하는 종목만 반환.
    결과가 min_keep 미만이면 필터 생략 후 원본 반환.
    top_sectors가 비어 있으면 원본 반환.
    """
    if not top_sectors or "sector" not in df.columns:
        return df
    filtered = df[df["sector"].isin(top_sectors)].reset_index(drop=True)
    if len(filtered) < min_keep:
        print(f"  [섹터 필터] 통과 {len(filtered)}개 < 최소 {min_keep}개 → 생략")
        return df
    print(f"  [섹터 필터] {len(df)}개 → {len(filtered)}개")
    return filtered


def calc_theme_bonus(sector: str, theme_analysis: str, bonus: float = 10.0) -> float:
    """theme_analysis 텍스트에 sector 키워드가 포함되면 bonus 반환, 아니면 0."""
    if not sector or not theme_analysis:
        return 0.0
    return bonus if str(sector).strip().lower() in theme_analysis.lower() else 0.0
