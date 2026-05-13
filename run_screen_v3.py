"""[V3] 섹터 주도주 전략 스크리닝 — 07:35 KST 실행

파이프라인:
  0단계: KOSPI 전 종목 로드 (FDR) — 시총 3,000억↑, 섹터 있는 종목만
  1단계: 전 종목 5일 수익률·거래량 배율 병렬 계산 (FDR, workers=8)
         → 섹터별 평균 수익률 → 주도 섹터 TOP_N_SECTORS 선정
  2단계: 주도 섹터 내 종목 복합 점수 산출
         모멘텀(35%) + 거래량(25%) + 건강검진(30%) + 뉴스감성(10%)
  3단계: 상위 MAX_HOLD개 → RSI + AI요약 → 텔레그램
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import exchange_calendars as xcals
import FinanceDataReader as fdr
import pandas as pd

from src.broker.kis_client import KISClient
from src.indicators.rsi import calc_rsi
from src.notify.telegram import send_message
from src.notify.ai_summary import summarize_pick

YEAR            = str(datetime.now().year - 1) if datetime.now().month >= 4 else str(datetime.now().year - 2)
PICKS_FILE      = "picks_v3.json"
MAX_HOLD        = 5
TOP_N_SECTORS   = 3
MOMENTUM_DAYS   = 5
VOLUME_LOOKBACK = 20
MIN_MARCAP      = 300_000_000_000  # 3,000억

_SENT_EMOJI = {"호재": "✅", "악재": "❌", "혼조": "⚠️"}


def _dominant_label(records: list[dict]) -> str:
    labels = {r.get("label") for r in records}
    if "호재" in labels and "악재" in labels:
        return "혼조"
    return "호재" if "호재" in labels else ("악재" if "악재" in labels else "혼조")


def main():
    ts    = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    today = str(date.today())
    print(f"\n{'='*50}")
    print(f"[{ts}] [V3] 섹터 주도주 스크리닝 시작")
    print(f"{'='*50}\n")

    if not xcals.get_calendar("XKRX").is_session(today):
        print("  오늘은 KRX 휴장일입니다. 스크리닝을 건너뜁니다.")
        return

    kis = KISClient(virtual=not os.getenv("KIS_APP_KEY"))

    # ── 뉴스 감성·테마 로드 ───────────────────────────────────
    sentiment_map: dict[str, list[dict]] = {}
    news_theme_analysis = ""
    try:
        from src.db.client import get_news_sentiment, get_latest_market_news
        for r in get_news_sentiment(today):
            sentiment_map.setdefault(r["stock_code"], []).append(r)
        row = get_latest_market_news(today)
        if row:
            news_theme_analysis = row.get("theme_analysis") or ""
        if sentiment_map:
            print(f"  감성 데이터: {sum(len(v) for v in sentiment_map.values())}건\n")
    except Exception as e:
        print(f"  [DB 오류] {e}\n")

    # ── 0단계: KOSPI 전 종목 로드 ─────────────────────────────
    print("[0단계] KOSPI 전 종목 로드...")
    listing = fdr.StockListing("KOSPI")

    marcap_col = next((c for c in ["Marcap", "MarketCap"] if c in listing.columns), None)
    if marcap_col:
        listing = listing[listing[marcap_col] >= MIN_MARCAP]

    sector_col = next((c for c in ["Sector", "Industry"] if c in listing.columns), None)
    if sector_col:
        listing = listing[
            listing[sector_col].notna() & (listing[sector_col].str.strip() != "")
        ].copy()
        listing.rename(columns={sector_col: "Sector"}, inplace=True)
    else:
        listing = listing.copy()
        listing["Sector"] = "전체"

    code_to_name   = dict(zip(listing["Code"], listing["Name"]))
    code_to_sector = dict(zip(listing["Code"], listing["Sector"]))
    codes = listing["Code"].tolist()
    print(f"  {len(codes)}개 종목 (시총 {MIN_MARCAP // 100_000_000:,}억↑)\n")

    # ── 1단계: 5일 수익률·거래량 배율 병렬 계산 ──────────────
    print("[1단계] 5일 수익률·거래량 배율 계산 중...")
    start_date = (date.today() - timedelta(days=40)).strftime("%Y-%m-%d")

    def _fetch(code: str) -> tuple[str, dict | None]:
        try:
            df = fdr.DataReader(code, start_date)[["Close", "Volume"]].dropna()
            if len(df) < VOLUME_LOOKBACK + 2:
                return code, None
            return_5d    = round(
                (df["Close"].iloc[-1] / df["Close"].iloc[-(MOMENTUM_DAYS + 1)] - 1) * 100, 2
            )
            vol_avg      = df["Volume"].iloc[-(VOLUME_LOOKBACK + 1):-1].mean()
            volume_ratio = round(df["Volume"].iloc[-1] / vol_avg, 2) if vol_avg > 0 else 1.0
            return code, {"return_5d": return_5d, "volume_ratio": volume_ratio}
        except Exception:
            return code, None

    stock_data: dict[str, dict] = {}
    done = 0
    total = len(codes)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch, c): c for c in codes}
        for f in as_completed(futures):
            done += 1
            if done % 50 == 0 or done == total:
                print(f"\r  진행: {done}/{total}", end="", flush=True)
            code, data = f.result()
            if data:
                stock_data[code] = data
    print(f"\n  데이터 수신: {len(stock_data)}개\n")

    if not stock_data:
        send_message(f"[{ts}] [V3] 가격 데이터 없음. FDR 연결 확인 필요.")
        return

    # ── 섹터별 평균 수익률 → 주도 섹터 선정 ──────────────────
    df_all = pd.DataFrame([
        {"stock_code": c, "sector": code_to_sector[c], **stock_data[c]}
        for c in stock_data if c in code_to_sector
    ])

    sector_avg = (
        df_all.groupby("sector")["return_5d"]
        .agg(["mean", "count"])
        .query("count >= 3")
        .sort_values("mean", ascending=False)
    )
    if sector_avg.empty:
        send_message(f"[{ts}] [V3] 유효한 섹터 없음.")
        return

    top_sectors = sector_avg.head(TOP_N_SECTORS).index.tolist()
    print("[주도 섹터]")
    for s in top_sectors:
        print(f"  {s}: {sector_avg.loc[s, 'mean']:+.2f}% ({int(sector_avg.loc[s, 'count'])}개)")
    print()

    # ── 2단계: 복합 점수 산출 ─────────────────────────────────
    print("[2단계] 복합 점수 산출...")
    universe = df_all[df_all["sector"].isin(top_sectors)].copy()

    universe["momentum_score"] = universe["return_5d"].rank(pct=True) * 100
    universe["volume_score"]   = (universe["volume_ratio"] / 5 * 100).clip(0, 100)

    try:
        from src.db.client import get_company_health
        from src.screening.health_check import score_health
    except Exception:
        get_company_health = lambda c: None
        score_health       = lambda d: 50.0

    health_map: dict[str, float] = {}
    bonus_map:  dict[str, float] = {}
    for code in universe["stock_code"]:
        cached = get_company_health(code)
        health_map[code] = score_health(cached) if cached else 50.0
        recs = sentiment_map.get(code, [])
        label = _dominant_label(recs) if recs else None
        bonus_map[code] = 15.0 if label == "호재" else (-10.0 if label == "악재" else 0.0)

    universe["health_score"] = universe["stock_code"].map(health_map)
    universe["news_bonus"]   = universe["stock_code"].map(bonus_map)
    universe["total_score"]  = (
        universe["momentum_score"] * 0.35
        + universe["volume_score"]  * 0.25
        + universe["health_score"]  * 0.30
        + universe["news_bonus"]    * 0.10
    ).round(1)

    picks_raw = universe.sort_values("total_score", ascending=False).head(MAX_HOLD).to_dict("records")

    print(f"\n[선정 종목] 상위 {len(picks_raw)}개")
    for p in picks_raw:
        name = code_to_name.get(p["stock_code"], p["stock_code"])
        print(
            f"  {name}({p['stock_code']})  섹터={p['sector']}  "
            f"5일={p['return_5d']:+.1f}%  거래량={p['volume_ratio']:.1f}x  "
            f"건강={p['health_score']:.0f}  점수={p['total_score']}"
        )

    # ── RSI 조회 ─────────────────────────────────────────────
    rsi_map: dict[str, float | None] = {}
    for p in picks_raw:
        try:
            prices = kis.get_daily_prices(p["stock_code"], count=60)
            rsi_map[p["stock_code"]] = calc_rsi(prices) if len(prices) >= 15 else None
        except Exception:
            rsi_map[p["stock_code"]] = None

    # ── AI 요약 ───────────────────────────────────────────────
    print("\n[AI 요약] Gemini 분석 중...")
    summaries: dict[str, str] = {}
    for p in picks_raw:
        name = code_to_name.get(p["stock_code"], p["stock_code"])
        ctx  = (
            f"[V3 섹터 주도주 지표]\n"
            f"섹터: {p['sector']}  5일수익률: {p['return_5d']:+.1f}%  "
            f"거래량배율: {p['volume_ratio']:.1f}x  건강검진: {p['health_score']:.0f}점"
        )
        try:
            from src.db.client import get_recent_news
            arts = get_recent_news(p["stock_code"], days=1)
            if arts:
                ctx += "\n최근 뉴스:\n" + "\n".join(f"- {a['title']}" for a in arts[:5])
        except Exception:
            pass
        summaries[p["stock_code"]] = summarize_pick(
            {"stock_code": p["stock_code"], "corp_name": name}, ctx
        )

    # ── 텔레그램 ─────────────────────────────────────────────
    SEP   = "─" * 8
    lines = [f"[{ts}] [V3] 섹터 주도주 후보 {len(picks_raw)}개", ""]

    lines.append(f"【 주도 섹터 】 {' / '.join(top_sectors)}")
    lines.append("")
    lines.append("【 오늘의 후보 종목 】")
    lines.append("종목 | RSI | 5일수익률 | 거래량 | 점수")
    lines.append(SEP)
    for p in picks_raw:
        name    = code_to_name.get(p["stock_code"], p["stock_code"])
        rsi     = rsi_map.get(p["stock_code"])
        rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
        signal  = " ◀매수" if rsi is not None and rsi < 35 else ""
        recs    = sentiment_map.get(p["stock_code"], [])
        sent_tag = f" {_SENT_EMOJI[_dominant_label(recs)]}" if recs else ""
        lines.append(
            f"{name}{sent_tag}{signal} | {rsi_str} | "
            f"{p['return_5d']:+.1f}% | {p['volume_ratio']:.1f}x | {p['total_score']}점"
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
    for i, p in enumerate(picks_raw, 1):
        name = code_to_name.get(p["stock_code"], p["stock_code"])
        lines.append(SEP)
        lines.append(f"{i}. {name} ({p['stock_code']}) — {p['sector']}")
        lines.append(
            f"5일수익률 {p['return_5d']:+.1f}%  거래량 {p['volume_ratio']:.1f}x  "
            f"건강검진 {p['health_score']:.0f}점  총점 {p['total_score']}점"
        )
        recs = sentiment_map.get(p["stock_code"], [])
        if recs:
            label = _dominant_label(recs)
            lines.append(f"[뉴스 감성] {_SENT_EMOJI[label]} {label}")
        summary = summaries.get(p["stock_code"], "")
        if summary:
            for line in summary.splitlines():
                lines.append(line)
        else:
            lines.append("(AI 요약 없음)")

    send_message("\n".join(lines))

    # ── picks_v3.json 저장 ────────────────────────────────────
    save_data = [
        {
            "stock_code":   p["stock_code"],
            "corp_name":    code_to_name.get(p["stock_code"], p["stock_code"]),
            "sector":       p["sector"],
            "return_5d":    p["return_5d"],
            "volume_ratio": p["volume_ratio"],
            "health_score": p["health_score"],
            "total_score":  p["total_score"],
        }
        for p in picks_raw
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
