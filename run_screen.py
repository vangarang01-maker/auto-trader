"""매일 07:30 1회 실행 — 종목 스크리닝 후 picks.json 저장"""
import json
from datetime import datetime, date
from pathlib import Path

import exchange_calendars as xcals

from src.screening.fundamental import FundamentalScreener
from src.portfolio.manager import PortfolioManager
from src.notify.telegram import send_message
from src.notify.ai_summary import summarize_pick

YEAR     = str(datetime.now().year - 1) if datetime.now().month >= 4 else str(datetime.now().year - 2)
MARKET   = "KOSPI"
PICKS_FILE = "picks.json"


def _get_news_context(stock_code: str, corp_name: str) -> str:
    """DB 캐시 우선으로 네이버 뉴스를 가져와 텍스트로 반환."""
    articles = []

    # DB에 최근 7일 뉴스가 5건 이상이면 재크롤링 생략
    try:
        from src.db.client import get_recent_news, save_news
        cached = get_recent_news(stock_code, days=7)
        if len(cached) >= 5:
            articles = cached
        else:
            from src.news.crawler import crawl_naver_news
            articles = crawl_naver_news(stock_code)
            if articles:
                save_news(stock_code, corp_name, articles)
    except Exception:
        # Supabase 미설정 시 바로 크롤링
        try:
            from src.news.crawler import crawl_naver_news
            articles = crawl_naver_news(stock_code)
        except Exception as e:
            print(f"  [뉴스 오류] {stock_code}: {e}")

    if not articles:
        return ""
    titles = [a.get("title", "") for a in articles[:10] if a.get("title")]
    return "최근 뉴스:\n" + "\n".join(f"- {t}" for t in titles)


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = str(date.today())
    print(f"\n{'='*50}")
    print(f"[{ts}] 스크리닝 시작 (기준연도: {YEAR})")
    print(f"{'='*50}\n")

    if not xcals.get_calendar("XKRX").is_session(today):
        print("  오늘은 KRX 휴장일입니다. 스크리닝을 건너뜁니다.")
        return

    screener = FundamentalScreener()

    print("[1단계] DART 재무 스크리닝...")
    dart_picks = screener.screen_all(year=YEAR, market=MARKET, workers=32)
    if dart_picks.empty:
        print("  통과 종목 없음. 종료.")
        return
    print(f"  1단계 통과: {len(dart_picks)}개\n")

    print("[2단계] KIS PEG 필터...")
    result = screener.apply_peg_filter(dart_picks)
    if result.empty:
        print("  PEG 통과 종목 없음. 종료.")
        return

    print(f"  2단계 통과: {len(result)}개\n")

    print("[3단계] KOSPI 초과성과 필터...")
    result = screener.apply_kospi_outperformance_filter(result)
    if result.empty:
        print("  KOSPI 초과성과 종목 없음. 종료.")
        send_message(f"[{ts}] 오늘의 자동매매 후보 종목\n\n3단계(KOSPI 초과성과) 통과 종목이 없습니다.")
        return

    print(f"  3단계 통과: {len(result)}개\n")

    pm = PortfolioManager(dry_run=True)
    picks = pm.select_picks(result)
    if not picks:
        print("  PEG 계산된 종목 없음. 종료.")
        send_message(f"[{ts}] 오늘의 자동매매 후보 종목\n\nPEG 계산 가능한 종목이 없습니다.")
        return

    print(f"\n[선정 종목] PEG 기준 상위 {len(picks)}개")
    for p in picks:
        print(f"  {p['corp_name']}({p['stock_code']})  PEG={p['peg']}  현재가={p['current_price']:,.0f}원")

    # AI 요약 (DART 공시 + 뉴스 컨텍스트 포함)
    print("\n[AI 요약] DART 공시·뉴스 수집 및 Gemini 분석 중...")
    DIV = "─" * 28
    lines = [f"[{ts}] 자동매매 후보 종목 {len(picks)}개"]
    for i, p in enumerate(picks, 1):
        up_str = f"{p['upside_capture']:.1f}%" if isinstance(p.get('upside_capture'), float) else "-"
        dn_str = f"{p['downside_capture']:.1f}%" if isinstance(p.get('downside_capture'), float) else "-"
        sector = p.get('sector') or ""
        sector_str = f"  |  {sector}" if sector else ""

        # DART 기업 컨텍스트
        dart_context = ""
        if p.get("corp_code"):
            try:
                dart_context = screener.client.get_company_context(p["corp_code"])
            except Exception as e:
                print(f"  [DART 컨텍스트 오류] {p['corp_name']}: {e}")

        # 뉴스 컨텍스트 (DB 캐시 우선)
        print(f"  뉴스 수집 중: {p['corp_name']}({p['stock_code']})")
        news_context = _get_news_context(p["stock_code"], p["corp_name"])

        combined_context = "\n\n".join(filter(None, [dart_context, news_context]))
        summary = summarize_pick(p, combined_context)

        lines.append(f"\n{DIV}")
        lines.append(f"{i}. {p['corp_name']} ({p['stock_code']}){sector_str}")
        lines.append(f"   PEG {p['peg']}  |  현재가 {p['current_price']:,.0f}원")
        lines.append(f"   상승포착 {up_str}  |  하락포착 {dn_str}")
        if summary:
            lines.append("")
            for line in summary.splitlines():
                lines.append(f"   {line}")

    lines.append(f"\n{DIV}")
    lines.append("관심종목 추가 후 RSI 신호 대기")
    send_message("\n".join(lines))

    # DB에 스크리닝 결과 저장
    try:
        from src.db.client import save_screening_result
        save_screening_result(picks, today)
        print(f"\n  [DB] 스크리닝 결과 {len(picks)}건 저장 완료.")
    except Exception as e:
        print(f"\n  [DB 오류] 스크리닝 결과 저장 실패: {e}")

    # stock_code, corp_name, peg 만 저장 (current_price는 trade 시점에 재조회)
    save_data = [{"stock_code": p["stock_code"], "corp_name": p["corp_name"], "peg": p["peg"]} for p in picks]
    Path(PICKS_FILE).write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
    print(f"\n→ {PICKS_FILE} 저장 완료.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        send_message(f"[{ts}] 스크리닝 실패\n\n{type(e).__name__}: {e}")
        raise
