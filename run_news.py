"""뉴스 크롤링만 실행 — DB 저장 후 결과 출력.

단독 실행:  python run_news.py
DB 저장:    market_news 테이블 (날짜 기준 upsert)
로컬 캐시:  news.json (로컬 개발용 fallback)
"""
import json
from datetime import date, datetime
from pathlib import Path

from src.news.market_news import get_market_news
from src.notify.ai_summary import analyze_market_themes

NEWS_FILE = "news.json"


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = str(date.today())
    print(f"\n[{ts}] 시장 뉴스 크롤링 시작")
    print("=" * 50)

    headlines, stock_codes = get_market_news()

    print(f"\n[헤드라인 {len(headlines)}건]")
    for i, h in enumerate(headlines, 1):
        print(f"  {i:>2}. {h}")

    print(f"\n[관련 종목코드 {len(stock_codes)}개]")
    if stock_codes:
        print("  " + ", ".join(stock_codes))
    else:
        print("  (없음 — 기사 본문에서 종목코드를 찾지 못했습니다)")

    print("\n[Gemini 시장 테마 분석 중...]")
    theme_analysis = analyze_market_themes(headlines)
    if theme_analysis:
        print(f"\n{theme_analysis}")
    else:
        print("  (GEMINI_API_KEY 미설정 또는 분석 실패)")

    # DB 저장
    try:
        from src.db.client import save_market_news
        save_market_news(today, headlines, stock_codes, theme_analysis)
        print(f"\n→ DB 저장 완료 (market_news / {today})")
    except Exception as e:
        print(f"\n  [DB 오류] {e}")

    # 로컬 캐시 (로컬 개발 시 run_screen.py fallback용)
    data = {
        "crawled_at": ts,
        "headlines": headlines,
        "stock_codes": stock_codes,
        "theme_analysis": theme_analysis,
    }
    Path(NEWS_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
