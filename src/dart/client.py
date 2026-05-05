import os
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

    def get_financial_statements(self, corp_code: str, year: str, report_type: str = "11011") -> object:
        """
        report_type: 11011=사업보고서, 11012=반기보고서, 11013=1분기, 11014=3분기
        """
        return self.dart.finstate(corp_code, year, report_type)

    def search_disclosures(self, corp_code: str, start_date: str, end_date: str) -> object:
        return self.dart.list(corp_code, start=start_date, end=end_date)

    def find_corp_code(self, company_name: str) -> str | None:
        result = self.dart.find_corp_code(company_name)
        return result
