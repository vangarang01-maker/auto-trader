"""매일 07:30 1회 실행 — 종목 스크리닝 후 picks.json 저장"""
import json
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import exchange_calendars as xcals

import os

from src.screening.fundamental import FundamentalScreener
from src.portfolio.manager import PortfolioManager
from src.broker.kis_client import KISClient
from src.indicators.rsi import calc_rsi
from src.notify.telegram import send_message
from src.notify.ai_summary import summarize_pick

YEAR     = str(datetime.now().year - 1) if datetime.now().month >= 4 else str(datetime.now().year - 2)
MARKET   = "KOSPI"
PICKS_FILE = "picks_v1.json"
NEWS_FILE  = "news.json"
SENTIMENT_BONUS = 10  # 호재 +10점, 악재 -10점 (건강검진 점수 조정)

_SENT_EMOJI = {"호재": "✅", "악재": "❌", "혼조": "⚠️"}


def _dominant_label(records: list[dict]) -> str:
    labels = {r.get("label") for r in records}
    if "호재" in labels and "악재" in labels:
        return "혼조"
    return "호재" if "호재" in labels else ("악재" if "악재" in labels else "혼조")


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
            articles = crawl_naver_news(corp_name)
            if articles:
                save_news(stock_code, corp_name, articles)
    except Exception:
        # Supabase 미설정 시 바로 크롤링
        try:
            from src.news.crawler import crawl_naver_news
            articles = crawl_naver_news(corp_name)
        except Exception as e:
            print(f"  [뉴스 오류] {stock_code}: {e}")

    if not articles:
        return ""
    titles = [a.get("title", "") for a in articles[:10] if a.get("title")]
    return "최근 뉴스:\n" + "\n".join(f"- {t}" for t in titles)


def main():
    ts = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    today = str(date.today())
    print(f"\n{'='*50}")
    print(f"[{ts}] 스크리닝 시작 (기준연도: {YEAR})")
    print(f"{'='*50}\n")

    if not xcals.get_calendar("XKRX").is_session(today):
        print("  오늘은 KRX 휴장일입니다. 스크리닝을 건너뜁니다.")
        return

    print("[0단계] 시장 뉴스 로드...")
    news_headlines: list[str] = []
    news_codes_set: set[str] = set()
    news_theme_analysis: str = ""

    # 1순위: DB
    try:
        from src.db.client import get_latest_market_news
        row = get_latest_market_news(today)
        if row:
            news_headlines = row.get("headlines") or []
            news_codes_set = set(row.get("stock_codes") or [])
            news_theme_analysis = row.get("theme_analysis") or ""
            print(f"  DB 로드: 헤드라인 {len(news_headlines)}건, 관련주 {len(news_codes_set)}개\n")
    except Exception as e:
        print(f"  [DB 조회 오류] {e}")

    # 2순위: news.json 로컬 캐시
    if not news_headlines:
        news_path = Path(NEWS_FILE)
        if news_path.exists():
            try:
                data = json.loads(news_path.read_text())
                news_headlines = data.get("headlines", [])
                news_codes_set = set(data.get("stock_codes", []))
                news_theme_analysis = data.get("theme_analysis", "")
                print(f"  {NEWS_FILE} 로드: 헤드라인 {len(news_headlines)}건\n")
            except Exception as e:
                print(f"  [news.json 로드 오류] {e}")

    # 3순위: live 크롤링
    if not news_headlines:
        try:
            from src.news.market_news import get_market_news
            news_headlines, news_codes = get_market_news()
            news_codes_set = set(news_codes)
            print(f"  live 크롤링: 헤드라인 {len(news_headlines)}건, 관련주 {len(news_codes_set)}개\n")
        except Exception as e:
            print(f"  [뉴스 크롤링 오류] {e}\n")

    # 뉴스 감성 맵 로드 (stock_code → [{label, reason, headline}, ...])
    sentiment_map: dict[str, list[dict]] = {}
    try:
        from src.db.client import get_news_sentiment
        for r in get_news_sentiment(today):
            sentiment_map.setdefault(r["stock_code"], []).append(r)
        if sentiment_map:
            print(f"  감성 데이터: {sum(len(v) for v in sentiment_map.values())}건 ({len(sentiment_map)}개 종목)\n")
    except Exception as e:
        print(f"  [감성 로드 오류] {e}\n")

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

    print("[섹터 모멘텀] 최근 1개월 상위 섹터 필터...")
    try:
        from src.screening.sector_momentum import get_top_sectors, apply_sector_filter
        top_sectors = get_top_sectors(dart_picks, months=1, top_n=3)
        result = apply_sector_filter(result, top_sectors, min_keep=5)
        print()
    except Exception as e:
        print(f"  [섹터 모멘텀 오류] {e} — 생략\n")

    kis = KISClient(virtual=not os.getenv("KIS_APP_KEY"))

    print("[건강검진] 후보 종목 7개 지표 점수 산출...")
    try:
        from src.screening.health_check import get_or_fetch_health, score_health

        health_scores = []
        for _, row in result.iterrows():
            health = get_or_fetch_health(
                stock_code=row["stock_code"],
                corp_name=row["corp_name"],
                dart_metrics=row.to_dict(),
                kis=kis,
                year=YEAR,
            )
            health_scores.append(score_health(health))
        result = result.reset_index(drop=True).copy()
        result["health_score"] = health_scores
        # 뉴스 감성 가중치 반영 (호재 +BONUS, 악재 -BONUS)
        for i in result.index:
            recs = sentiment_map.get(result.at[i, "stock_code"], [])
            if not recs:
                continue
            label = _dominant_label(recs)
            bonus = SENTIMENT_BONUS if label == "호재" else (-SENTIMENT_BONUS if label == "악재" else 0)
            if bonus:
                result.at[i, "health_score"] = round(
                    max(0.0, min(100.0, result.at[i, "health_score"] + bonus)), 1
                )
                sign = "+" if bonus > 0 else ""
                print(f"  [{label}] {result.at[i, 'corp_name']} 건강검진 {sign}{bonus}점 반영")
        # 테마 섹터 보너스 반영
        try:
            from src.screening.sector_momentum import calc_theme_bonus
            THEME_BONUS = 10
            for i in result.index:
                bonus = calc_theme_bonus(result.at[i, "sector"], news_theme_analysis, THEME_BONUS)
                if bonus:
                    result.at[i, "health_score"] = round(
                        min(100.0, result.at[i, "health_score"] + bonus), 1
                    )
                    print(f"  [테마 보너스] {result.at[i, 'corp_name']} +{bonus:.0f}점 (섹터: {result.at[i, 'sector']})")
        except Exception:
            pass
        print(f"  건강검진 완료: {len(result)}개\n")
    except Exception as e:
        print(f"  [건강검진 오류] {e} — PEG 기준으로 대체\n")

    pm = PortfolioManager(dry_run=True)
    picks = pm.select_picks(result)
    if not picks:
        print("  PEG 계산된 종목 없음. 종료.")
        send_message(f"[{ts}] 오늘의 자동매매 후보 종목\n\nPEG 계산 가능한 종목이 없습니다.")
        return

    sort_by = "건강검진 점수" if picks and picks[0].get("health_score") is not None else "PEG"
    print(f"\n[선정 종목] {sort_by} 기준 상위 {len(picks)}개")
    for p in picks:
        hs = f"  건강검진={p['health_score']:.0f}점" if p.get("health_score") is not None else ""
        print(f"  {p['corp_name']}({p['stock_code']})  PEG={p['peg']}  현재가={p['current_price']:,.0f}원{hs}")

    # RSI 계산
    rsi_map: dict[str, float | None] = {}
    for p in picks:
        try:
            prices = kis.get_daily_prices(p["stock_code"], count=60)
            rsi_map[p["stock_code"]] = calc_rsi(prices) if len(prices) >= 15 else None
        except Exception:
            rsi_map[p["stock_code"]] = None

    # AI 요약 수집
    print("\n[AI 요약] DART 공시·뉴스 수집 및 Gemini 분석 중...")
    summaries: dict[str, str] = {}
    for p in picks:
        dart_context = ""
        if p.get("corp_code"):
            try:
                dart_context = screener.client.get_company_context(p["corp_code"])
            except Exception as e:
                print(f"  [DART 컨텍스트 오류] {p['corp_name']}: {e}")
        print(f"  뉴스 수집 중: {p['corp_name']}({p['stock_code']})")
        news_context = _get_news_context(p["stock_code"], p["corp_name"])
        combined_context = "\n\n".join(filter(None, [dart_context, news_context]))
        summaries[p["stock_code"]] = summarize_pick(p, combined_context)

    # ── 텔레그램 메시지 조립 ────────────────────────────────
    SEP = "─" * 8
    lines = [f"[{ts}] [V1 피터 린치] 후보 {len(picks)}개", ""]

    # 상단: 종목 리스트 표
    lines.append("【 오늘의 후보 종목 】")
    lines.append("종목 | RSI | 건강검진")
    lines.append(SEP)
    for p in picks:
        rsi = rsi_map.get(p["stock_code"])
        rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
        signal = " ◀매수" if rsi is not None and rsi < 35 else (" ▶매도" if rsi is not None and rsi >= 75 else "")
        hs_str = f"{p['health_score']:.0f}점" if p.get("health_score") is not None else "-"
        news_tag = " 📰" if p["stock_code"] in news_codes_set else ""
        sent_records = sentiment_map.get(p["stock_code"], [])
        sent_tag = f" {_SENT_EMOJI[_dominant_label(sent_records)]}" if sent_records else ""
        lines.append(f"{p['corp_name']}{sent_tag}{signal}{news_tag} | {rsi_str} | {hs_str}")

    # 중단: 시장 테마
    lines.append("")
    lines.append("【 오늘의 시장 테마 】")
    if news_theme_analysis:
        for line in news_theme_analysis.splitlines():
            lines.append(line)
    elif news_headlines:
        for h in news_headlines[:3]:
            lines.append(f"• {h}")
    else:
        lines.append("(뉴스 데이터 없음)")

    # 하단: 종목별 추천 이유
    lines.append("")
    lines.append("【 종목별 추천 이유 】")
    for i, p in enumerate(picks, 1):
        lines.append(SEP)
        news_tag = " 📰" if p["stock_code"] in news_codes_set else ""
        lines.append(f"{i}. {p['corp_name']} ({p['stock_code']}){news_tag}")
        sent_records = sentiment_map.get(p["stock_code"], [])
        if sent_records:
            label = _dominant_label(sent_records)
            lines.append(f"[뉴스 감성] {_SENT_EMOJI[label]} {label}")
            for r in sent_records[:3]:
                lines.append(f"• {r.get('reason', '')}")
        summary = summaries.get(p["stock_code"], "")
        if summary:
            for line in summary.splitlines():
                lines.append(line)
        else:
            lines.append("(AI 요약 없음)")

    send_message("\n".join(lines))

    # DB에 스크리닝 결과 저장
    try:
        from src.db.client import save_screening_result
        save_screening_result(picks, today)
        print(f"\n  [DB] 스크리닝 결과 {len(picks)}건 저장 완료.")
    except Exception as e:
        print(f"\n  [DB 오류] 스크리닝 결과 저장 실패: {e}")

    save_data = [
        {
            "stock_code":   p["stock_code"],
            "corp_name":    p["corp_name"],
            "peg":          p.get("peg"),
            "health_score": p.get("health_score"),
        }
        for p in picks
    ]
    Path(PICKS_FILE).write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
    print(f"\n→ {PICKS_FILE} 저장 완료.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        ts = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
        send_message(f"[{ts}] 스크리닝 실패\n\n{type(e).__name__}: {e}")
        raise
