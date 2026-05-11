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

        # 연결재무제표 우선, 없으면 별도재무제표 사용
        if "연결재무제표" in df["fs_nm"].values:
            df = df[df["fs_nm"] == "연결재무제표"]
        df = df.drop_duplicates(subset=["account_nm"], keep="first")

        # 계정명 후보 목록 (기업·회계기준마다 다름)
        account_candidates = {
            "revenue":          ["매출액"],
            "operating_profit": ["영업이익"],
            "net_income":       ["당기순이익(손실)", "당기순이익"],
            "total_assets":     ["자산총계"],
            "total_liabilities":["부채총계"],
            "current_liab":     ["유동부채"],
            "equity":           ["자본총계"],
            "operating_cf":     ["영업활동으로 인한 현금흐름", "영업활동현금흐름"],
            "eps":              ["기본주당순이익(원)", "기본주당순이익", "주당순이익"],
        }

        metrics: dict = {}
        for eng, candidates in account_candidates.items():
            for name in candidates:
                row = df[df["account_nm"] == name]
                if not row.empty:
                    metrics[eng] = to_int(row.iloc[0].get("thstrm_amount", 0))
                    if eng not in ("operating_cf", "eps"):
                        metrics[f"{eng}_prev"] = to_int(row.iloc[0].get("frmtrm_amount", 0))
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

    def screen_all(self, year: str, market: str = "KOSPI", workers: int = 8) -> pd.DataFrame:
        """상장 종목 병렬 스크리닝
        market: 'KOSPI' | 'KOSDAQ' | None(전체)
        """
        listed = self.client.get_listed_corp_codes(market=market)
        total = len(listed)
        corp_names = dict(zip(listed["corp_code"], listed["corp_name"]))
        stock_codes = dict(zip(listed["corp_code"], listed["stock_code"]))
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
                if self._is_lynch_pick(metrics):
                    results.append({"corp_code": code, "corp_name": corp_names.get(code, ""), "stock_code": stock_codes.get(code, ""), **metrics})

        print()
        return pd.DataFrame(results)

    def apply_kospi_outperformance_filter(self, df: pd.DataFrame, months: int = 3) -> pd.DataFrame:
        """KOSPI 대비 초과성과 종목만 통과
        upside capture > 100%  : KOSPI 상승일에 더 많이 오름
        downside capture < 100%: KOSPI 하락일에 덜 떨어짐
        데이터 부족(공통 20일 미만) 또는 조회 실패 시 제외.
        """
        import FinanceDataReader as fdr
        from datetime import datetime, timedelta

        end_dt = datetime.now()
        start_str = (end_dt - timedelta(days=months * 31)).strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d")

        try:
            kospi = fdr.DataReader("KS11", start_str, end_str)
            kospi_ret = kospi["Close"].pct_change().dropna()
        except Exception as e:
            print(f"  [KOSPI 조회 오류] {e} → 필터 생략")
            return df

        def check_stock(row):
            code = row.get("stock_code", "")
            if not code:
                return None
            try:
                stock = fdr.DataReader(code, start_str, end_str)
                if stock.empty:
                    return None
                stock_ret = stock["Close"].pct_change().dropna()
                common = kospi_ret.index.intersection(stock_ret.index)
                if len(common) < 20:
                    return None
                k = kospi_ret.loc[common]
                s = stock_ret.loc[common]
                up = k > 0
                dn = k < 0
                if up.sum() < 5 or dn.sum() < 5:
                    return None
                up_cap = s[up].mean() / k[up].mean() * 100
                dn_cap = s[dn].mean() / k[dn].mean() * 100
                if up_cap > dn_cap:
                    r = row.copy()
                    r["upside_capture"] = round(up_cap, 1)
                    r["downside_capture"] = round(dn_cap, 1)
                    return r
            except Exception as e:
                print(f"  [KOSPI 비교 오류] {code}: {e}")
            return None

        rows_input = [row for _, row in df.iterrows()]
        results = []
        total = len(rows_input)
        done = 0

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(check_stock, row): row for row in rows_input}
            for future in as_completed(futures):
                done += 1
                print(f"\r  진행: {done}/{total}", end="", flush=True)
                result = future.result()
                if result is not None:
                    results.append(result)

        print()
        return pd.DataFrame(results)

    def apply_peg_filter(self, df: pd.DataFrame, max_peg: float = 1.0) -> pd.DataFrame:
        """KIS PER 기반 PEG 계산 후 필터링
        PEG = PER / 순이익성장률(%)
        KIS 조회 실패 종목은 통과 처리
        """
        import os
        from src.broker.kis_client import KISClient
        virtual = not os.getenv("KIS_APP_KEY")
        kis = KISClient(virtual=virtual)

        rows = []
        for _, row in df.iterrows():
            stock_code = row.get("stock_code", "")
            growth = row.get("net_income_growth", 0)

            if not stock_code or not growth or growth <= 0:
                rows.append(row)
                continue

            try:
                quote = kis.get_stock_quote(stock_code)
                per = quote["per"]
                if not per or per <= 0:
                    rows.append(row)
                    continue
                peg = round(per / growth, 2)
                row = row.copy()
                row["current_price"] = quote["price"]
                row["eps"] = quote["eps"]
                row["per"] = per
                row["peg"] = peg
                if peg <= max_peg:
                    rows.append(row)
            except Exception as e:
                print(f"  [KIS 오류] {stock_code}: {e}")
                rows.append(row)  # KIS 오류 시 통과

        result = pd.DataFrame(rows)
        if "peg" not in result.columns:
            result["peg"] = float("nan")
        if "current_price" not in result.columns:
            result["current_price"] = float("nan")
        return result
