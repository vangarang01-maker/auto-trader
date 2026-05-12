import os
import time
import OpenDartReader
from dotenv import load_dotenv

load_dotenv()


class DartClient:
    def __init__(self):
        api_key = os.getenv("DART_API_KEY")
        if not api_key:
            raise ValueError("DART_API_KEY가 .env에 설정되지 않았습니다.")
        self.dart = OpenDartReader(api_key)

    def get_company_info(self, corp_code: str) -> dict:
        return self.dart.company(corp_code)

    def get_dividends_paid(self, corp_code: str, year: str) -> int:
        """DART 현금흐름표 '배당금의지급' 조회 (배당수익률 계산용)."""
        try:
            df = self.dart.finstate_all(corp_code, int(year), "11011")
            if df is None or df.empty:
                return 0
            row = df[df["account_nm"] == "배당금의지급"]
            if row.empty:
                return 0
            val = str(row.iloc[0].get("thstrm_amount", "0")).replace(",", "").strip()
            return abs(int(val or 0))
        except Exception:
            return 0

    def get_financial_statements(self, corp_code: str, year: str, report_type: str = "11011") -> object:
        """
        report_type: 11011=사업보고서, 11012=반기보고서, 11013=1분기, 11014=3분기
        """
        for attempt in range(3):
            try:
                return self.dart.finstate(corp_code, year, report_type)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)  # 1s, 2s

    def search_disclosures(self, corp_code: str, start_date: str, end_date: str) -> object:
        return self.dart.list(corp_code, start=start_date, end=end_date)

    def find_corp_code(self, company_name: str) -> str | None:
        result = self.dart.find_corp_code(company_name)
        return result

    def get_company_context(self, corp_code: str) -> str:
        """AI 분석용 기업 컨텍스트: 기업 개요 + 최근 6개월 공시 목록"""
        from datetime import datetime, timedelta
        lines = []

        try:
            info = self.dart.company(corp_code)
            if info is not None:
                prd = getattr(info, "get", lambda k, d=None: info[k] if k in info else d)
                if info.get("prd_nm"):
                    lines.append(f"주요제품/서비스: {str(info['prd_nm'])[:200]}")
                if info.get("induty_code"):
                    lines.append(f"업종코드: {info['induty_code']}")
        except Exception:
            pass

        try:
            end   = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
            disc  = self.dart.list(corp_code, start=start, end=end)
            if disc is not None and not disc.empty:
                col = "report_nm" if "report_nm" in disc.columns else disc.columns[2]
                titles = disc[col].dropna().head(10).tolist()
                lines.append("최근 6개월 공시:")
                lines.extend(f"- {t}" for t in titles)
        except Exception:
            pass

        return "\n".join(lines)

    def get_listed_corp_codes(self, market: str | None = None) -> "pd.DataFrame":
        """상장 종목 목록 반환
        market: 'KOSPI' | 'KOSDAQ' | None(전체)
        KOSPI/KOSDAQ 지정 시 FinanceDataReader로 시장 필터링
        """
        import ssl, certifi, pandas as pd
        ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

        all_corps = self.dart.corp_codes
        all_corps = all_corps[all_corps["stock_code"].notna() & (all_corps["stock_code"].str.strip() != "")].copy()
        all_corps.loc[:, "stock_code"] = all_corps["stock_code"].str.strip()

        if market in ("KOSPI", "KOSDAQ"):
            import FinanceDataReader as fdr
            for attempt in range(3):
                try:
                    raw = fdr.StockListing(market)
                    cols = {"Code": "stock_code"}
                    if "Sector" in raw.columns:
                        cols["Sector"] = "sector"
                    elif "Industry" in raw.columns:
                        cols["Industry"] = "sector"
                    listing = raw[list(cols.keys())].rename(columns=cols)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
            all_corps = all_corps.merge(listing, on="stock_code", how="inner")

        return all_corps.reset_index(drop=True)
