"""[V2] 고배당-저PBR-고ROE 전략 스크리닝 — 매일 07:30 1회 실행"""
import json
import os
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import exchange_calendars as xcals

from src.screening.strategy_v2 import ValueDividendScreener
from src.screening.health_check import get_or_fetch_health, score_health
from src.broker.kis_client import KISClient
from src.indicators.rsi import calc_rsi
from src.notify.telegram import send_message
from src.notify.ai_summary import summarize_pick

YEAR       = str(datetime.now().year - 1) if datetime.now().month >= 4 else str(datetime.now().year - 2)
MARKET     = "KOSPI"
PICKS_FILE = "picks_v2.json"
MAX_HOLD   = 5
SENTIMENT_BONUS = 10  # 호재 +10점, 악재 -10점

_SENT_EMOJI = {"호재": "✅", "악재": "❌", "혼조": "⚠️"}


def _dominant_label(records: list[dict]) -> str:
    labels = {r.get("label") for r in records}
    if "호재" in labels and "악재" in labels:
        return "혼조"
    return "호재" if "호재" in labels else ("악재" if "악재" in labels else "혼조")


def _get_news_context(stock_code: str, corp_name: str) -> str:
    """DB 캐시 우선으로 네이버 뉴스를 텍스트로 반환."""
    articles = []
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
    ts    = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    today = str(date.today())
    print(f"\n{'='*50}")
    print(f"[{ts}] [V2] 스크리닝 시작 (기준연도: {YEAR})")
    print(f"{'='*50}\n")

    if not xcals.get_calendar("XKRX").is_session(today):
        print("  오늘은 KRX 휴장일입니다. 스크리닝을 건너뜁니다.")
        return

    # ── 0단계: 시장 뉴스 + 감성 로드 ──────────────────────
    print("[0단계] 시장 뉴스 로드...")
    news_headlines: list[str] = []
    news_codes_set: set[str]  = set()
    news_theme_analysis: str  = ""

    try:
        from src.db.client import get_latest_market_news
        row = get_latest_market_news(today)
        if row:
            news_headlines      = row.get("headlines") or []
            news_codes_set      = set(row.get("stock_codes") or [])
            news_theme_analysis = row.get("theme_analysis") or ""
            print(f"  DB 로드: 헤드라인 {len(news_headlines)}건\n")
    except Exception as e:
        print(f"  [DB 조회 오류] {e}")

    if not news_headlines:
        news_path = Path("news.json")
        if news_path.exists():
            try:
                data            = json.loads(news_path.read_text())
                news_headlines  = data.get("headlines", [])
                news_codes_set  = set(data.get("stock_codes", []))
                news_theme_analysis = data.get("theme_analysis", "")
                print(f"  news.json 로드: 헤드라인 {len(news_headlines)}건\n")
            except Exception as e:
                print(f"  [news.json 로드 오류] {e}")

    if not news_headlines:
        try:
            from src.news.market_news import get_market_news
            news_headlines, news_codes = get_market_news()
            news_codes_set = set(news_codes)
            print(f"  live 크롤링: 헤드라인 {len(news_headlines)}건\n")
        except Exception as e:
            print(f"  [뉴스 크롤링 오류] {e}\n")

    sentiment_map: dict[str, list[dict]] = {}
    try:
        from src.db.client import get_news_sentiment
        for r in get_news_sentiment(today):
            sentiment_map.setdefault(r["stock_code"], []).append(r)
        if sentiment_map:
            print(f"  감성 데이터: {sum(len(v) for v in sentiment_map.values())}건\n")
    except Exception as e:
        print(f"  [감성 로드 오류] {e}\n")

    # ── 스크리닝 ────────────────────────────────────────────
    screener = ValueDividendScreener()
    kis = KISClient(virtual=not os.getenv("KIS_APP_KEY"))

    print("[1단계] DART 재무 필터 (ROE·부채비율·이자보상배율)...")
    result = screener.screen_all(year=YEAR, market=MARKET, workers=32)
    if result.empty:
        print("  통과 종목 없음. 종료.")
        return
    print(f"  1단계 통과: {len(result)}개\n")

    print("[2단계] KIS 밸류에이션 필터 (PBR 0.3~1.2 / 배당수익률 ≥ 2.5%, 데이터 없으면 통과)...")
    result = screener.apply_valuation_filter(result, kis)
    if result.empty:
        print("  통과 종목 없음. 종료.")
        send_message(f"[{ts}] [V2] 오늘의 후보 종목\n\n2단계(밸류에이션) 통과 종목이 없습니다.")
        return
    print(f"  2단계 통과: {len(result)}개\n")

    print("[3단계] 6개월 모멘텀 필터 (상위 20%)...")
    result = screener.apply_momentum_filter(result)
    if result.empty:
        print("  통과 종목 없음. 종료.")
        send_message(f"[{ts}] [V2] 오늘의 후보 종목\n\n3단계(모멘텀) 통과 종목이 없습니다.")
        return
    print(f"  3단계 통과: {len(result)}개\n")

    # ── 건강검진 ────────────────────────────────────────────
    print("[건강검진] 7개 지표 점수 산출...")
    try:
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
        print(f"  건강검진 완료: {len(result)}개\n")
    except Exception as e:
        print(f"  [건강검진 오류] {e} — 배당수익률 기준으로 대체\n")

    # ── 종목 선정 ────────────────────────────────────────────
    valid = result.copy()
    if "health_score" in valid.columns and valid["health_score"].notna().any():
        picks_df = valid.sort_values("health_score", ascending=False)
    else:
        picks_df = valid.sort_values("div_yield", ascending=False)
    picks = picks_df.head(MAX_HOLD).to_dict("records")

    if not picks:
        print("  선정 종목 없음. 종료.")
        return

    sort_by = "건강검진 점수" if picks[0].get("health_score") is not None else "배당수익률"
    print(f"\n[선정 종목] {sort_by} 기준 상위 {len(picks)}개")
    for p in picks:
        hs  = f"  건강검진={p['health_score']:.0f}점" if p.get("health_score") is not None else ""
        print(f"  {p['corp_name']}({p['stock_code']})  PBR={p.get('pbr')}  배당={p.get('div_yield')}%  ROE={p.get('roe')}%{hs}")

    # ── RSI 계산 ─────────────────────────────────────────────
    rsi_map: dict[str, float | None] = {}
    for p in picks:
        try:
            prices = kis.get_daily_prices(p["stock_code"], count=60)
            rsi_map[p["stock_code"]] = calc_rsi(prices) if len(prices) >= 15 else None
        except Exception:
            rsi_map[p["stock_code"]] = None

    # ── AI 요약 ──────────────────────────────────────────────
    print("\n[AI 요약] DART 공시·뉴스 수집 및 Gemini 분석 중...")
    summaries: dict[str, str] = {}
    for p in picks:
        dart_context = ""
        if p.get("corp_code"):
            try:
                dart_context = screener.client.get_company_context(p["corp_code"])
            except Exception as e:
                print(f"  [DART 컨텍스트 오류] {p['corp_name']}: {e}")
        v2_context = (
            f"[V2 핵심 지표]\n"
            f"PBR: {p.get('pbr')}배  ROE: {p.get('roe')}%  "
            f"배당수익률: {p.get('div_yield')}%  6개월수익률: {p.get('return_6m')}%"
        )
        print(f"  뉴스 수집 중: {p['corp_name']}({p['stock_code']})")
        news_context = _get_news_context(p["stock_code"], p["corp_name"])
        combined = "\n\n".join(filter(None, [v2_context, dart_context, news_context]))
        summaries[p["stock_code"]] = summarize_pick(p, combined)

    # ── 텔레그램 메시지 조립 ─────────────────────────────────
    SEP = "─" * 8
    lines = [f"[{ts}] [V2] 고배당-저PBR-고ROE 후보 {len(picks)}개", ""]

    # 섹션 1: 종목 리스트 표
    lines.append("【 오늘의 후보 종목 】")
    lines.append("종목 | RSI | 배당률 | 건강검진")
    lines.append(SEP)
    for p in picks:
        rsi     = rsi_map.get(p["stock_code"])
        rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
        signal  = " ◀매수" if rsi is not None and rsi < 35 else (" ▶매도" if rsi is not None and rsi >= 75 else "")
        hs_str  = f"{p['health_score']:.0f}점" if p.get("health_score") is not None else "-"
        div_str = f"{p.get('div_yield', '-')}%"
        news_tag = " 📰" if p["stock_code"] in news_codes_set else ""
        sent_records = sentiment_map.get(p["stock_code"], [])
        sent_tag = f" {_SENT_EMOJI[_dominant_label(sent_records)]}" if sent_records else ""
        lines.append(f"{p['corp_name']}{sent_tag}{signal}{news_tag} | {rsi_str} | {div_str} | {hs_str}")

    # 섹션 2: 시장 테마
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

    # 섹션 3: 종목별 추천 이유
    lines.append("")
    lines.append("【 종목별 추천 이유 】")
    for i, p in enumerate(picks, 1):
        lines.append(SEP)
        news_tag = " 📰" if p["stock_code"] in news_codes_set else ""
        lines.append(f"{i}. {p['corp_name']} ({p['stock_code']}){news_tag}")
        lines.append(f"PBR {p.get('pbr')}  ROE {p.get('roe')}%  배당 {p.get('div_yield')}%  6M수익률 {p.get('return_6m')}%")
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

    # picks_v2.json 저장
    save_data = [
        {
            "stock_code":   p["stock_code"],
            "corp_name":    p["corp_name"],
            "div_yield":    p.get("div_yield"),
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
        send_message(f"[{ts}] [V2] 스크리닝 실패\n\n{type(e).__name__}: {e}")
        raise
