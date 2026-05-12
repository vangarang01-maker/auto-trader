"""전략 V2 — 고배당-저PBR-고ROE 퀀트 스크리너

파이프라인:
  1단계 DART: 순이익>0, ROE≥10%, 부채비율≤150%, 이자보상배율≥3
  2단계 KIS:  PBR 0.3~1.2, 배당수익률≥2.5%
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
        return pd.DataFrame(results) if results else pd.DataFrame()

    # ── 네이버 금융 배당수익률 per-stock 조회 ────────────────

    @staticmethod
    def fetch_div_naver(
        stock_codes: list[str],
        workers: int = 5,
    ) -> dict[str, float]:
        """네이버 금융 per-stock 배당수익률 스크래핑 (로그인 불필요).

        DART 필터 통과 종목(~100-200개)에 대해서만 호출.
        실패 종목은 건너뜀 → apply_valuation_filter에서 div=None 통과 처리.
        """
        import requests
        from bs4 import BeautifulSoup

        def fetch_one(code: str) -> tuple[str, float | None]:
            try:
                r = requests.get(
                    f"https://finance.naver.com/item/main.naver?code={code}",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=5,
                )
                r.encoding = "euc-kr"
                dvr = BeautifulSoup(r.text, "html.parser").find(id="_dvr")
                if dvr:
                    return code, round(float(dvr.get_text(strip=True)), 2)
            except Exception:
                pass
            return code, None

        result: dict[str, float] = {}
        total = len(stock_codes)
        done  = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_one, c): c for c in stock_codes}
            for future in as_completed(futures):
                done += 1
                if done % 20 == 0 or done == total:
                    print(f"\r  [Naver DIV] {done}/{total}", end="", flush=True)
                code, div = future.result()
                if div is not None and div > 0:
                    result[code] = div
        print()
        print(f"  [Naver DIV] {len(result)}개 종목 배당수익률 수신")
        return result

    # ── pykrx 배당수익률 일괄 조회 (KRX 로그인 필요, 현재 미사용) ──

    @staticmethod
    def fetch_div_map(market: str = "KOSPI") -> dict[str, float]:
        """pykrx로 시장 전체 배당수익률 일괄 조회 시도.

        ⚠️ data.krx.co.kr가 인증 없이 MDCSTAT03501 블록을 거부(400 LOGOUT)하므로
        pykrx 1.0.51·1.2.x 모두 빈 DataFrame 반환. 항상 {} 반환 예상.
        DIV 조회는 fetch_div_naver()를 대신 사용할 것.
        """
        try:
            import sys
            if "pkg_resources" not in sys.modules:
                import types, importlib.metadata
                _pkg = types.ModuleType("pkg_resources")
                _pkg.get_distribution = lambda n: type("D", (), {"version": importlib.metadata.version(n)})()
                sys.modules["pkg_resources"] = _pkg

            from pykrx import stock as krx_stock
            from datetime import date as _date
            today = _date.today().strftime("%Y%m%d")
            df = krx_stock.get_market_fundamental_by_ticker(today, market=market, alternative=True)
            if df is None or df.empty or "DIV" not in df.columns:
                return {}
            return {
                str(ticker).zfill(6): round(float(div), 2)
                for ticker, div in df["DIV"].items()
                if float(div) > 0
            }
        except Exception:
            return {}

    # ── 2단계: KIS 밸류에이션 필터 ────────────────────────

    def apply_valuation_filter(
        self,
        df: pd.DataFrame,
        kis,
        pbr_lo: float = 0.3,
        pbr_hi: float = 1.2,
        min_div: float = 2.5,
        div_map: dict[str, float] | None = None,
    ) -> pd.DataFrame:
        """PBR 범위 + 배당수익률 최소치 필터.

        div_map: {stock_code: DIV%} — pykrx로 사전 조회한 전 종목 배당수익률.
                 None이면 DART finstate_all fallback 시도.
                 둘 다 없으면 div=None → 통과 처리.
        """
        rows = []
        total = len(df)
        for i, (_, row) in enumerate(df.iterrows(), 1):
            print(f"\r  진행: {i}/{total}", end="", flush=True)
            try:
                val    = kis.get_stock_valuation(row["stock_code"])
                pbr    = val.get("pbr")
                shares = val.get("shares", 0)
                price  = val.get("price") or 0

                # 배당수익률 우선순위: pykrx → DART finstate_all → None(통과)
                div: float | None = None
                if div_map is not None:
                    div = div_map.get(row["stock_code"])
                if div is None:
                    total_div = row.get("total_div_paid", 0) or 0
                    if total_div > 0 and shares > 0 and price > 0:
                        div = round(total_div / (shares * price) * 100, 2)

                if pbr is None or not (pbr_lo <= pbr <= pbr_hi):
                    continue
                if div is not None and div < min_div:
                    continue
                rows.append({
                    **row.to_dict(),
                    "pbr":           pbr,
                    "div_yield":     div if div is not None else 0.0,
                    "current_price": price,
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
