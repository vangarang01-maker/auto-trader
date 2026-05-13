import json
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


def analyze_stock_news_batch(
    stock_news: list[tuple[str, str, list[str]]],
    batch_size: int = 15,
) -> dict[str, str]:
    """종목별 뉴스 헤드라인 배치 감성 분석.

    stock_news: [(stock_code, corp_name, [headlines]), ...]
    Returns: {stock_code: "호재" | "악재" | "혼조" | "중립"}
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not stock_news:
        return {}

    client = genai.Client(api_key=api_key)
    result: dict[str, str] = {}

    for i in range(0, len(stock_news), batch_size):
        batch = stock_news[i : i + batch_size]
        sections = []
        for code, name, headlines in batch:
            h_text = "\n".join(f"  - {h}" for h in headlines[:5])
            sections.append(f"[{name}({code})]\n{h_text}")

        prompt = f"""아래 종목들의 최근 뉴스 헤드라인을 투자 관점에서 분석하세요.
각 종목에 대해 '호재', '악재', '혼조', '중립' 중 하나로 판단하세요.

{chr(10).join(sections)}

JSON 배열만 출력 (마크다운 없이):
[{{"stock_code":"6자리코드","label":"호재 또는 악재 또는 혼조 또는 중립"}}]"""

        for model_name in _MODELS:
            try:
                resp = client.models.generate_content(model=model_name, contents=prompt)
                text = resp.text.strip()
                if "```" in text:
                    text = text[text.find("[") : text.rfind("]") + 1]
                for item in json.loads(text):
                    result[item["stock_code"]] = item["label"]
                break
            except Exception as e:
                print(f"  [배치감성 오류] {model_name}: {e}")

    return result


def analyze_news_sentiment(headlines: list[str], stock_codes: list[str]) -> list[dict]:
    """헤드라인 목록과 관련 종목코드를 받아 각 (헤드라인, 종목) 쌍의 호재/악재/혼조를 판단.

    반환: [{"headline", "stock_code", "corp_name", "label", "reason"}, ...]
    """
    if not headlines or not stock_codes:
        return []
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return []

    client = genai.Client(api_key=api_key)
    joined_headlines = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    joined_codes = ", ".join(stock_codes)

    prompt = f"""한국 주식 시장 뉴스 헤드라인 목록과 관련 종목코드가 있습니다.
각 헤드라인에서 아래 종목코드 목록의 종목과 직접 관련된 것을 찾아,
해당 종목에 '호재', '악재', '혼조' 중 하나로 판단하고 근거를 한 줄로 설명하세요.
관련 없는 헤드라인은 건너뛰고, 확실하지 않으면 포함하지 마세요.

[헤드라인]
{joined_headlines}

[관련 종목코드]
{joined_codes}

JSON 배열만 출력 (마크다운 코드블록 없이):
[{{"headline":"원문그대로","stock_code":"6자리코드","corp_name":"기업명","label":"호재 또는 악재 또는 혼조","reason":"판단근거한줄"}}]"""

    for model_name in _MODELS:
        try:
            resp = client.models.generate_content(model=model_name, contents=prompt)
            text = resp.text.strip()
            # 마크다운 펜스 제거
            if "```" in text:
                start = text.find("[")
                end = text.rfind("]") + 1
                text = text[start:end]
            return json.loads(text)
        except Exception as e:
            print(f"  [감성분석 오류] {model_name}: {e}")
    return []
