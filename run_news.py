"""뉴스 크롤링만 실행 — DB 저장 후 결과 출력.

단독 실행:  python run_news.py
DB 저장:    market_news 테이블 (날짜 기준 upsert)
로컬 캐시:  news.json (로컬 개발용 fallback)
"""
import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from src.news.market_news import get_market_news
from src.notify.ai_summary import analyze_market_themes, analyze_news_sentiment
from src.notify.telegram import send_message

NEWS_FILE = "news.json"


def main():
    ts = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
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

    # 뉴스 감성 분석
    print("\n[뉴스 감성 분석 중...]")
    sentiment_records: list[dict] = []
    try:
        sentiment_records = analyze_news_sentiment(headlines, stock_codes)
        if sentiment_records:
            _EMOJI = {"호재": "✅", "악재": "❌", "혼조": "⚠️"}
            for s in sentiment_records:
                emoji = _EMOJI.get(s.get("label", ""), "")
                print(f"  {emoji} {s.get('label')} — {s.get('corp_name')}({s.get('stock_code')}): {s.get('reason')}")
        else:
            print("  (관련 종목 없음 또는 분석 실패)")
    except Exception as e:
        print(f"  [감성 분석 오류] {e}")

    # DB 저장
    try:
        from src.db.client import save_market_news, save_news_sentiment
        save_market_news(today, headlines, stock_codes, theme_analysis)
        print(f"\n→ DB 저장 완료 (market_news / {today})")
        if sentiment_records:
            save_news_sentiment(today, sentiment_records)
            print(f"→ DB 저장 완료 (news_sentiment / {len(sentiment_records)}건)")
    except Exception as e:
        print(f"\n  [DB 오류] {e}")

    # 종목별 뉴스 크롤링 (KOSPI 시총 상위 100 + 당일 언급 종목)
    print("\n[종목별 뉴스 크롤링] KOSPI 시총 상위 100 + 당일 언급 종목...")
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import FinanceDataReader as fdr
        from src.news.crawler import crawl_naver_news
        from src.db.client import save_news

        listing    = fdr.StockListing("KOSPI")
        marcap_col = next((c for c in ["Marcap", "MarketCap"] if c in listing.columns), None)
        top100     = listing.nlargest(100, marcap_col) if marcap_col else listing.head(100)
        code_to_name = dict(zip(listing["Code"], listing["Name"]))

        universe_codes = set(top100["Code"].tolist()) | set(stock_codes)
        universe = [(c, code_to_name[c]) for c in universe_codes if c in code_to_name]
        print(f"  유니버스: {len(universe)}개 종목")

        total_saved = 0
        done        = 0
        total       = len(universe)

        def _crawl(code, name):
            arts = crawl_naver_news(name, max_articles=5)
            if arts:
                save_news(code, name, arts)
            return len(arts)

        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = {ex.submit(_crawl, c, n): c for c, n in universe}
            for f in as_completed(futs):
                done += 1
                if done % 20 == 0 or done == total:
                    print(f"\r  진행: {done}/{total}", end="", flush=True)
                total_saved += f.result()
        print(f"\n  완료: {total_saved}건 저장")
    except Exception as e:
        print(f"  [종목별 뉴스 오류] {e}")

    # 로컬 캐시 (로컬 개발 시 run_screen.py fallback용)
    data = {
        "crawled_at": ts,
        "headlines": headlines,
        "stock_codes": stock_codes,
        "theme_analysis": theme_analysis,
    }
    Path(NEWS_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # 텔레그램 완료 알림
    top3 = "\n".join(f"• {h}" for h in headlines[:3]) if headlines else "(없음)"
    send_message(
        f"[{ts}] 뉴스 크롤링 완료\n"
        f"헤드라인 {len(headlines)}건 / 관련종목 {len(stock_codes)}개\n\n"
        f"{top3}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        ts = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
        send_message(f"[{ts}] 뉴스 크롤링 실패\n\n{type(e).__name__}: {e}")
        raise
