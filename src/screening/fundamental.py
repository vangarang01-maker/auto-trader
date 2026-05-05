import pandas as pd
from src.dart.client import DartClient


class FundamentalScreener:
    """DART 재무 데이터 기반 종목 스크리닝"""

    def __init__(self):
        self.client = DartClient()

    def get_key_metrics(self, corp_code: str, year: str) -> dict:
        """사업보고서에서 핵심 재무 지표 추출"""
        df = self.client.get_financial_statements(corp_code, year)
        if df is None or df.empty:
            return {}

        metrics = {}
        target_accounts = {
            "매출액": "revenue",
            "영업이익": "operating_profit",
            "당기순이익": "net_income",
            "자산총계": "total_assets",
            "부채총계": "total_liabilities",
            "자본총계": "equity",
        }

        for kor, eng in target_accounts.items():
            row = df[df["account_nm"] == kor]
            if not row.empty:
                val = row.iloc[0].get("thstrm_amount", 0)
                metrics[eng] = int(str(val).replace(",", "") or 0)

        if metrics.get("equity") and metrics["equity"] != 0:
            metrics["debt_ratio"] = round(
                metrics.get("total_liabilities", 0) / metrics["equity"] * 100, 2
            )
        if metrics.get("revenue") and metrics["revenue"] != 0:
            metrics["operating_margin"] = round(
                metrics.get("operating_profit", 0) / metrics["revenue"] * 100, 2
            )

        return metrics

    def screen(self, corp_codes: list[str], year: str, min_operating_margin: float = 10.0, max_debt_ratio: float = 200.0) -> pd.DataFrame:
        """여러 종목을 조건으로 필터링"""
        results = []
        for code in corp_codes:
            metrics = self.get_key_metrics(code, year)
            if not metrics:
                continue
            if (
                metrics.get("operating_margin", 0) >= min_operating_margin
                and metrics.get("debt_ratio", 999) <= max_debt_ratio
            ):
                results.append({"corp_code": code, **metrics})

        return pd.DataFrame(results)
