import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.dart.client import DartClient


class FundamentalScreener:
    """DART 재무 데이터 기반 종목 스크리닝"""

    def __init__(self):
        self.client = DartClient()

    def get_key_metrics(self, corp_code: str, year: str) -> dict:
        """사업보고서에서 핵심 재무 지표 추출 (당기 + 전기 YoY 포함)"""
        df = self.client.get_financial_statements(corp_code, year)
        if df is None or df.empty:
            return {}

        def to_int(val) -> int:
            try:
                return int(str(val).replace(",", "").strip() or 0)
            except (ValueError, TypeError):
                return 0

        target_accounts = {
            "매출액": "revenue",
            "영업이익": "operating_profit",
            "당기순이익": "net_income",
            "자산총계": "total_assets",
            "부채총계": "total_liabilities",
            "자본총계": "equity",
        }
        # 현금흐름표 계정명은 회계기준(IFRS/K-GAAP)에 따라 다름
        cf_account_candidates = ["영업활동으로 인한 현금흐름", "영업활동현금흐름"]

        metrics: dict = {}
        for kor, eng in target_accounts.items():
            row = df[df["account_nm"] == kor]
            if not row.empty:
                metrics[eng] = to_int(row.iloc[0].get("thstrm_amount", 0))
                metrics[f"{eng}_prev"] = to_int(row.iloc[0].get("frmtrm_amount", 0))

        for cf_name in cf_account_candidates:
            row = df[df["account_nm"] == cf_name]
            if not row.empty:
                metrics["operating_cf"] = to_int(row.iloc[0].get("thstrm_amount", 0))
                break

        # EPS - 계정명이 기업마다 다를 수 있음
        for eps_name in ("기본주당순이익(원)", "기본주당순이익", "주당순이익"):
            row = df[df["account_nm"] == eps_name]
            if not row.empty:
                metrics["eps"] = to_int(row.iloc[0].get("thstrm_amount", 0))
                break

        if metrics.get("equity"):
            metrics["debt_ratio"] = round(
                metrics.get("total_liabilities", 0) / metrics["equity"] * 100, 2
            )
        if metrics.get("revenue"):
            metrics["operating_margin"] = round(
                metrics.get("operating_profit", 0) / metrics["revenue"] * 100, 2
            )

        for field in ("revenue", "net_income", "operating_profit"):
            curr, prev = metrics.get(field, 0), metrics.get(f"{field}_prev", 0)
            if prev:
                metrics[f"{field}_growth"] = round((curr - prev) / abs(prev) * 100, 2)

        return metrics

    def _is_lynch_pick(self, m: dict) -> bool:
        """피터 린치 기준 (DART 가능 범위)
        PEG < 1은 KIS API 연동 후 추가 예정
        """
        cf_ok = m.get("operating_cf", 1) > 0  # 데이터 없으면 통과 처리
        return (
            m.get("net_income", 0) > 0
            and m.get("net_income_growth", -999) >= 20
            and m.get("revenue_growth", -999) >= 10
            and m.get("debt_ratio", 999) <= 50
            and cf_ok
        )

    def screen_all(self, year: str, market: str = "Y", workers: int = 8) -> pd.DataFrame:
        """상장 종목 병렬 스크리닝
        market: Y=KOSPI, K=KOSDAQ, 빈 문자열=전체
        """
        listed = self.client.get_listed_corp_codes()
        total = len(listed)
        corp_names = dict(zip(listed["corp_code"], listed["corp_name"]))
        stock_codes = dict(zip(listed["corp_code"], listed["stock_code"].str.strip()))
        market_label = {"Y": "KOSPI", "K": "KOSDAQ"}.get(market, "전체")
        print(f"  {market_label} 필터 적용 중 ({total}개 후보)...")

        results = []
        done = 0

        def fetch(code):
            try:
                if market:
                    info = self.client.get_company_info(code)
                    if info.get("corp_cls") != market:
                        return code, {}
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
                if self._is_lynch_pick(metrics):
                    results.append({"corp_code": code, "corp_name": corp_names.get(code, ""), "stock_code": stock_codes.get(code, ""), **metrics})

        print()
        return pd.DataFrame(results)

    def apply_peg_filter(self, df: pd.DataFrame, max_peg: float = 1.0) -> pd.DataFrame:
        """KIS 현재가로 PEG 계산 후 필터링
        PEG = (현재가 / EPS) / 순이익성장률
        EPS 또는 현재가 조회 실패 종목은 통과 처리
        """
        from src.broker.kis_client import KISClient
        kis = KISClient()

        rows = []
        for _, row in df.iterrows():
            stock_code = row.get("stock_code", "")
            eps = row.get("eps", 0)
            growth = row.get("net_income_growth", 0)

            if not stock_code or not eps or eps <= 0 or not growth or growth <= 0:
                rows.append(row)
                continue

            try:
                price = kis.get_current_price(stock_code)
                pe = price / eps
                peg = round(pe / growth, 2)
                row = row.copy()
                row["current_price"] = price
                row["pe_ratio"] = round(pe, 2)
                row["peg_ratio"] = peg
                if peg <= max_peg:
                    rows.append(row)
            except Exception:
                rows.append(row)  # KIS 오류 시 통과

        return pd.DataFrame(rows)
