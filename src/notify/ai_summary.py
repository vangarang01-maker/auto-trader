import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

_MODEL = "gemini-3-flash-preview"


def _format_val(val, suffix="") -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "N/A"
    return f"{val}{suffix}"


def summarize_pick(pick: dict) -> str:
    """종목 재무 지표를 Gemini에게 전달해 투자 포인트 요약을 받아온다.
    API 키 미설정 또는 호출 실패 시 빈 문자열 반환.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ""

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(_MODEL)

    prompt = f"""다음 종목의 정량 지표를 바탕으로, 개인 투자자에게 2~3줄로 핵심 투자 포인트와 주의사항을 한국어로 요약해줘.
숫자를 단순 나열하지 말고 의미 위주로 써줘. 불필요한 인사말 없이 바로 본문만 작성해.

종목명: {pick['corp_name']} ({pick['stock_code']})
PEG: {_format_val(pick.get('peg'))}
순이익 성장률: {_format_val(pick.get('net_income_growth'), '%')}
매출 성장률: {_format_val(pick.get('revenue_growth'), '%')}
부채비율: {_format_val(pick.get('debt_ratio'), '%')}
KOSPI 상승포착률: {_format_val(pick.get('upside_capture'), '%')}
KOSPI 하락포착률: {_format_val(pick.get('downside_capture'), '%')}"""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"  [Gemini 오류] {pick.get('corp_name', '')}: {e}")
        return ""
