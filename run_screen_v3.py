"""[V3] 뉴스 모멘텀 전략 스크리닝 — 07:35 KST 실행

파이프라인:
  1단계: market_news 당일 언급 종목 + KOSPI 시총 상위 100 = 유니버스
  2단계: news 테이블에서 당일 뉴스 읽기 (run_news.py가 미리 저장)
  3단계: Gemini 배치 감성 분석 → 호재 종목만
  4단계: health_check DB 캐시 → 점수 산출
  5단계: 뉴스 점수(기사수 × 호재) + health_score 상위 MAX_HOLD개 선정
"""
import json
import os
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import exchange_calendars as xcals
import FinanceDataReader as fdr

from src.broker.kis_client import KISClient
from src.indicators.rsi import calc_rsi
from src.notify.telegram import send_message
from src.notify.ai_summary import analyze_stock_news_batch, summarize_pick

YEAR       = str(datetime.now().year - 1) if datetime.now().month >= 4 else str(datetime.now().year - 2)
PICKS_FILE = "picks_v3.json"
MAX_HOLD   = 5
MIN_ARTICLES = 2   # 뉴스 최소 기사 수

_SENT_EMOJI = {"호재": "✅", "악재": "❌", "혼조": "⚠️"}


def _get_news_context(stock_code: str, corp_name: str) -> str:
    try:
        from src.db.client import get_recent_news
        articles = get_recent_news(stock_code, days=1)
        if not articles:
            return ""
        titles = [a.get("title", "") for a in articles[:10] if a.get("title")]
        return "최근 뉴스:\n" + "\n".join(f"- {t}" for t in titles)
    except Exception:
        return ""


def main():
    ts    = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    today = str(date.today())
    print(f"\n{'='*50}")
    print(f"[{ts}] [V3] 뉴스 모멘텀 스크리닝 시작")
    print(f"{'='*50}\n")

    if not xcals.get_calendar("XKRX").is_session(today):
        print("  오늘은 KRX 휴장일입니다. 스크리닝을 건너뜁니다.")
        return

    from src.db.client import get_latest_market_news, get_news_bulk

    # ── 0단계: 시장 뉴스 로드 ─────────────────────────────
    print("[0단계] 시장 뉴스 로드...")
    news_theme_analysis = ""
    market_codes: set[str] = set()
    try:
        row = get_latest_market_news(today)
        if row:
            market_codes        = set(row.get("stock_codes") or [])
            news_theme_analysis = row.get("theme_analysis") or ""
            print(f"  당일 언급 종목: {len(market_codes)}개\n")
    except Exception as e:
        print(f"  [DB 오류] {e}\n")

    # ── 1단계: 유니버스 구성 ──────────────────────────────
    print("[1단계] 유니버스 구성...")
    listing      = fdr.StockListing("KOSPI")
    marcap_col   = next((c for c in ["Marcap", "MarketCap"] if c in listing.columns), None)
    top100       = listing.nlargest(100, marcap_col) if marcap_col else listing.head(100)
    code_to_name = dict(zip(listing["Code"], listing["Name"]))

    top100_codes   = set(top100["Code"].tolist())
    universe_codes = list(top100_codes | market_codes)
    print(f"  유니버스: {len(universe_codes)}개 (시총상위100 + 당일언급 {len(market_codes)}개)\n")

    # ── 2단계: DB에서 당일 뉴스 읽기 ──────────────────────
    print("[2단계] 뉴스 DB 조회...")
    news_map   = get_news_bulk(universe_codes, days=1)
    candidates = {
        code: articles
        for code, articles in news_map.items()
        if len(articles) >= MIN_ARTICLES
    }
    print(f"  기사 {MIN_ARTICLES}건 이상: {len(candidates)}개 종목\n")

    if not candidates:
        msg = f"[{ts}] [V3] 뉴스 모멘텀 스크리닝\n\n뉴스 데이터가 없습니다. (run_news.py 실행 확인)"
        send_message(msg)
        return

    # ── 3단계: Gemini 배치 감성 분석 ──────────────────────
    print("[3단계] Gemini 배치 감성 분석...")
    stock_news_input = [
        (code, code_to_name.get(code, code), [a["title"] for a in arts])
        for code, arts in candidates.items()
    ]
    sentiment_map = analyze_stock_news_batch(stock_news_input)

    positive_codes = [code for code, lbl in sentiment_map.items() if lbl == "호재"]
    print(f"  호재 판정: {len(positive_codes)}개 종목\n")

    if not positive_codes:
        msg = f"[{ts}] [V3] 뉴스 모멘텀 스크리닝\n\n호재 종목이 없습니다."
        send_message(msg)
        return

    # ── 4단계: health_score (DB 캐시 우선, 없으면 0) ──────
    print("[4단계] 건강검진 점수 조회 (DB 캐시)...")
    try:
        from src.db.client import get_company_health
        from src.screening.health_check import score_health

        kis = KISClient(virtual=not os.getenv("KIS_APP_KEY"))
        health_scores: dict[str, float] = {}
        for code in positive_codes:
            cached = get_company_health(code)
            if cached:
                health_scores[code] = score_health(cached)
            else:
                health_scores[code] = 50.0  # 캐시 없으면 중간값
    except Exception as e:
        print(f"  [건강검진 오류] {e}")
        health_scores = {code: 50.0 for code in positive_codes}

    # ── 5단계: 뉴스 점수 + health_score 합산 → 상위 선정 ──
    print("\n[5단계] 종목 점수 산출...")
    scored = []
    for code in positive_codes:
        article_count = len(candidates.get(code, []))
        news_score    = article_count * 10          # 기사 1건 = 10점
        total_score   = round(news_score + health_scores.get(code, 50.0), 1)
        corp_name     = code_to_name.get(code, code)
        scored.append({
            "stock_code":   code,
            "corp_name":    corp_name,
            "news_label":   sentiment_map.get(code, ""),
            "article_count": article_count,
            "news_score":   news_score,
            "health_score": health_scores.get(code, 50.0),
            "total_score":  total_score,
        })

    scored.sort(key=lambda x: x["total_score"], reverse=True)
    picks_raw = scored[:MAX_HOLD]

    print(f"\n[선정 종목] 상위 {len(picks_raw)}개")
    for p in picks_raw:
        print(f"  {p['corp_name']}({p['stock_code']})  뉴스={p['article_count']}건  "
              f"뉴스점수={p['news_score']}  건강={p['health_score']}  합계={p['total_score']}")

    # ── 현재가 + RSI 조회 ──────────────────────────────────
    picks: list[dict] = []
    rsi_map: dict[str, float | None] = {}
    for p in picks_raw:
        try:
            price = kis.get_current_price(p["stock_code"])
        except Exception:
            price = 0
        try:
            prices = kis.get_daily_prices(p["stock_code"], count=60)
            rsi_map[p["stock_code"]] = calc_rsi(prices) if len(prices) >= 15 else None
        except Exception:
            rsi_map[p["stock_code"]] = None
        picks.append({**p, "current_price": price})

    # ── AI 요약 ───────────────────────────────────────────
    print("\n[AI 요약] Gemini 분석 중...")
    summaries: dict[str, str] = {}
    for p in picks:
        news_ctx = _get_news_context(p["stock_code"], p["corp_name"])
        v3_ctx   = (
            f"[V3 뉴스 점수]\n"
            f"당일 기사 {p['article_count']}건  감성: {p['news_label']}  "
            f"건강검진: {p['health_score']}점"
        )
        combined = "\n\n".join(filter(None, [v3_ctx, news_ctx]))
        summaries[p["stock_code"]] = summarize_pick(p, combined)

    # ── 텔레그램 메시지 조립 ──────────────────────────────
    SEP   = "─" * 8
    lines = [f"[{ts}] [V3] 뉴스 모멘텀 후보 {len(picks)}개", ""]

    lines.append("【 오늘의 후보 종목 】")
    lines.append("종목 | RSI | 뉴스 | 점수")
    lines.append(SEP)
    for p in picks:
        rsi     = rsi_map.get(p["stock_code"])
        rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
        signal  = " ◀매수" if rsi is not None and rsi < 35 else ("")
        emoji   = _SENT_EMOJI.get(p["news_label"], "")
        lines.append(
            f"{p['corp_name']}{signal} | {rsi_str} | "
            f"{emoji}{p['article_count']}건 | {p['total_score']}점"
        )

    lines.append("")
    lines.append("【 오늘의 시장 테마 】")
    if news_theme_analysis:
        for line in news_theme_analysis.splitlines():
            lines.append(line)
    else:
        lines.append("(테마 데이터 없음)")

    lines.append("")
    lines.append("【 종목별 추천 이유 】")
    for i, p in enumerate(picks, 1):
        lines.append(SEP)
        lines.append(f"{i}. {p['corp_name']} ({p['stock_code']})")
        lines.append(f"뉴스 {p['article_count']}건  감성: {_SENT_EMOJI.get(p['news_label'], '')} {p['news_label']}")
        summary = summaries.get(p["stock_code"], "")
        if summary:
            for line in summary.splitlines():
                lines.append(line)
        else:
            lines.append("(AI 요약 없음)")

    send_message("\n".join(lines))

    # ── picks_v3.json 저장 ────────────────────────────────
    save_data = [
        {
            "stock_code":    p["stock_code"],
            "corp_name":     p["corp_name"],
            "health_score":  p.get("health_score"),
            "article_count": p.get("article_count"),
            "news_label":    p.get("news_label"),
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
        send_message(f"[{ts}] [V3] 스크리닝 실패\n\n{type(e).__name__}: {e}")
        raise
