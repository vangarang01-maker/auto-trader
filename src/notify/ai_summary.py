import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

_MODELS = ["gemini-3.1-flash-lite", "gemini-3-flash-preview"]


def _format_val(val, suffix="") -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "N/A"
    return f"{val}{suffix}"


def summarize_pick(pick: dict, context: str = "") -> str:
    """재무 지표 + 기업 컨텍스트(공시·업종)를 Gemini에 전달해 분석을 받아온다.
    gemini-3.1-flash-lite 우선 시도, 실패 시 gemini-3-flash-preview로 폴백.
    API 키 미설정 또는 전체 실패 시 빈 문자열 반환.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ""

    client = genai.Client(api_key=api_key)

    context_section = f"\n\n[기업 정보 및 최근 공시]\n{context}" if context else ""

    prompt = f"""다음 종목의 재무 지표{" 및 기업 정보" if context else ""}를 바탕으로, 아래 두 가지를 한국어로 작성해줘.
숫자를 단순 나열하지 말고 의미 위주로, 불필요한 인사말 없이 바로 본문만 작성해.

[재무 지표]
종목명: {pick['corp_name']} ({pick['stock_code']})
PEG: {_format_val(pick.get('peg'))}
순이익 성장률: {_format_val(pick.get('net_income_growth'), '%')}
매출 성장률: {_format_val(pick.get('revenue_growth'), '%')}
부채비율: {_format_val(pick.get('debt_ratio'), '%')}
KOSPI 상승포착률: {_format_val(pick.get('upside_capture'), '%')}
KOSPI 하락포착률: {_format_val(pick.get('downside_capture'), '%')}{context_section}

답변 형식:
투자포인트: (1~2줄)
리스크: (1~2줄)"""

    for model_name in _MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            print(f"  [Gemini 오류] {model_name} / {pick.get('corp_name', '')}: {e}")

    return ""


def analyze_market_themes(headlines: list[str]) -> str:
    """헤드라인 전체를 Gemini에 보내 오늘의 주도 섹터/테마를 분석받는다."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not headlines:
        return ""

    client = genai.Client(api_key=api_key)

    joined = "\n".join(f"- {h}" for h in headlines)
    prompt = f"""다음은 오늘 한국 증시 관련 뉴스 헤드라인이다.
이 헤드라인들을 분석해서 오늘의 주도 섹터 또는 테마를 3개 이내로 선정하고,
각각 선정 이유를 한 줄로 설명해줘.
숫자 나열 말고 의미 위주로, 불필요한 인사말 없이 바로 본문만 작성해.

[헤드라인]
{joined}

답변 형식:
주도 섹터/테마: (이름 1~3개, 쉼표 구분)
분석: (섹터별로 한 줄씩)"""

    for model_name in _MODELS:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            return response.text.strip()
        except Exception as e:
            print(f"  [Gemini 오류] {model_name}: {e}")

    return ""
