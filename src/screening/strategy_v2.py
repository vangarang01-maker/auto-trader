"""전략 V2 — 고배당-저PBR-고ROE 퀀트 스크리너

파이프라인:
  1단계 DART: 순이익>0, ROE≥10%, 부채비율≤150%, 이자보상배율≥3
  2단계 KIS:  PBR 0.8~1.2, 배당수익률≥3.5%
  3단계 FDR:  6개월 수익률 상위 20% (최소 5개 보장)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import FinanceDataReader as fdr
import pandas as pd

from src.dart.client import DartClient


class ValueDividendScreener:

    def __init__(self):
        self.client = DartClient()

    # ── DART 재무 지표 ──────────────────────────────────────

    def get_key_metrics(self, corp_code: str, year: str) -> dict:
        df = self.client.get_financial_statements(corp_code, year)
        if df is None or df.empty:
            return {}

        def to_int(val) -> int:
            try:
                return int(str(val).replace(",", "").strip() or 0)
            except (ValueError, TypeError):
                return 0

        if "연결재무제표" in df["fs_nm"].values:
            df = df[df["fs_nm"] == "연결재무제표"]
        df = df.drop_duplicates(subset=["account_nm"], keep="first")

        account_candidates = {
            "net_income":        ["당기순이익(손실)", "당기순이익"],
            "equity":            ["자본총계"],
            "total_liabilities": ["부채총계"],
            "operating_profit":  ["영업이익"],
            "interest_expense":  ["이자비용", "금융원가"],
            "revenue":           ["매출액"],
        }

        metrics: dict = {}
        for eng, candidates in account_candidates.items():
            for name in candidates:
                row = df[df["account_nm"] == name]
                if not row.empty:
                    metrics[eng] = to_int(row.iloc[0].get("thstrm_amount", 0))
                    break

        equity = metrics.get("equity", 0)
        if not equity:
            return {}

        metrics["roe"] = round(metrics.get("net_income", 0) / equity * 100, 1)
        metrics["debt_ratio"] = round(metrics.get("total_liabilities", 0) / equity * 100, 1)

        op = metrics.get("operating_profit", 0)
        interest = metrics.get("interest_expense", 0)
        metrics["interest_coverage"] = round(op / interest, 1) if interest > 0 else None

        return metrics

    def _passes_dart_filter(self, m: dict) -> bool:
        if m.get("net_income", 0) <= 0:
            return False
        if m.get("roe", -999) < 10:
            return False
        if m.get("debt_ratio", 999) > 150:
            return False
        ic = m.get("interest_coverage")
        if ic is not None and ic < 3:
            return False
        return True

    # ── 1단계: DART 전수 스크리닝 ──────────────────────────

    def screen_all(self, year: str, market: str = "KOSPI", workers: int = 32) -> pd.DataFrame:
        listed = self.client.get_listed_corp_codes(market=market)
        total = len(listed)
        corp_names  = dict(zip(listed["corp_code"], listed["corp_name"]))
        stock_codes = dict(zip(listed["corp_code"], listed["stock_code"]))
        sectors     = dict(zip(listed["corp_code"], listed.get("sector", pd.Series(dtype=str))))
        print(f"  {market or '전체'} {total}개 종목 조회 시작...")

        results = []
        done = 0

        def fetch(code):
            try:
                return code, self.get_key_metrics(code, year)
            except Exception:
                return code, {}

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch, code): code for code in listed["corp_code"]}
            for future in as_completed(futures):
                done += 1
                if done % 100 == 0 or done == total:
                    print(f"\r  진행: {done}/{total}", end="", flush=True)
                code, metrics = future.result()
                if not metrics:
                    continue
                if self._passes_dart_filter(metrics):
                    results.append({
                        "corp_code":  code,
                        "corp_name":  corp_names.get(code, ""),
                        "stock_code": stock_codes.get(code, ""),
                        "sector":     sectors.get(code, ""),
                        **metrics,
                    })

        print()
        return pd.DataFrame(results)

    # ── 2단계: KIS 밸류에이션 필터 ────────────────────────

    def apply_valuation_filter(
        self,
        df: pd.DataFrame,
        kis,
        pbr_lo: float = 0.3,
        pbr_hi: float = 1.2,
        min_div: float = 2.5,
    ) -> pd.DataFrame:
        """PBR 범위 + 배당수익률 최소치 필터."""
        rows = []
        total = len(df)
        for i, (_, row) in enumerate(df.iterrows(), 1):
            print(f"\r  진행: {i}/{total}", end="", flush=True)
            try:
                val = kis.get_stock_valuation(row["stock_code"])
                pbr = val.get("pbr")
                div = val.get("div_yield")
                if pbr is None or not (pbr_lo <= pbr <= pbr_hi):
                    continue
                if div is not None and div < min_div:
                    continue
                rows.append({
                    **row.to_dict(),
                    "pbr":           pbr,
                    "div_yield":     div if div is not None else 0.0,
                    "current_price": val.get("price"),
                })
            except Exception:
                continue
        print()
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # ── 3단계: 6개월 모멘텀 필터 ──────────────────────────

    def apply_momentum_filter(
        self,
        df: pd.DataFrame,
        top_pct: float = 0.20,
    ) -> pd.DataFrame:
        """최근 6개월 절대 수익률 상위 top_pct (최소 5개 보장)."""
        start = (date.today() - timedelta(days=182)).strftime("%Y-%m-%d")

        records = []
        for _, row in df.iterrows():
            try:
                prices = fdr.DataReader(row["stock_code"], start)["Close"].dropna()
                if len(prices) < 20:
                    continue
                ret = round((prices.iloc[-1] / prices.iloc[0] - 1) * 100, 1)
                records.append({**row.to_dict(), "return_6m": ret})
            except Exception:
                continue

        if not records:
            return pd.DataFrame()

        result = pd.DataFrame(records).sort_values("return_6m", ascending=False)
        n_keep = max(5, int(len(result) * top_pct))
        return result.head(n_keep).reset_index(drop=True)
