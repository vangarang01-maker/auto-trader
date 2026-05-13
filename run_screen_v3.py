"""[V3] 섹터 주도주 전략 스크리닝 — 07:35 KST 실행

파이프라인:
  0단계: 네이버 금융 업종 시세 → 당일 상승 상위 섹터 TOP_N_SECTORS 선정
  1단계: 해당 섹터 종목 코드 수집 (네이버 업종 상세)
  2단계: 종목별 5일 수익률·거래량 배율 병렬 계산 (FDR, workers=8)
  3단계: 복합 점수 (모멘텀 35% + 거래량 25% + 건강검진 30% + 뉴스감성 10%)
  4단계: 상위 MAX_HOLD개 → RSI + AI요약 → 텔레그램
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import exchange_calendars as xcals
import FinanceDataReader as fdr
import pandas as pd
import requests
from bs4 import BeautifulSoup

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

_HEADERS    = {"User-Agent": "Mozilla/5.0"}
_SENT_EMOJI = {"호재": "✅", "악재": "❌", "혼조": "⚠️"}


# ── 네이버 금융 업종 스크래퍼 ──────────────────────────────

def _get_sector_rankings() -> list[dict]:
    """네이버 금융 업종 시세에서 당일 등락률 순 섹터 목록 반환."""
    url = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
    r = requests.get(url, headers=_HEADERS, timeout=10)
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "html.parser")

    sectors = []
    for row in soup.select("table.type_1 tr"):
        tds = row.select("td")
        if len(tds) < 4:
            continue
        a = tds[0].select_one("a")
        if not a:
            continue
        name = a.get_text(strip=True)
        m = re.search(r"no=(\d+)", a.get("href", ""))
        if not m:
            continue
        try:
            pct = float(
                tds[2].get_text(strip=True)
                .replace("+", "").replace("%", "").replace(",", "")
            )
        except ValueError:
            continue
        sectors.append({"name": name, "no": m.group(1), "change_pct": pct})

    return sorted(sectors, key=lambda x: x["change_pct"], reverse=True)


def _get_sector_stocks(sector_no: str) -> list[str]:
    """네이버 금융 업종 상세에서 종목 코드 목록 반환 (페이지 순회)."""
    codes: list[str] = []
    page = 1
    while True:
        url = (
            f"https://finance.naver.com/sise/sise_group_detail.naver"
            f"?type=upjong&no={sector_no}&page={page}"
        )
        r = requests.get(url, headers=_HEADERS, timeout=10)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        new = [
            a["href"].split("code=")[1][:6]
            for a in soup.select("table.type_5 a[href*='code=']")
        ]
        if not new:
            break
        codes.extend(new)
        if not soup.select_one("a.pgRR"):
            break
        page += 1
    return list(set(codes))


# ── 유틸 ──────────────────────────────────────────────────

def _dominant_label(records: list[dict]) -> str:
    labels = {r.get("label") for r in records}
    if "호재" in labels and "악재" in labels:
        return "혼조"
    return "호재" if "호재" in labels else ("악재" if "악재" in labels else "혼조")


# ── 메인 ──────────────────────────────────────────────────

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

    # ── 0단계: 네이버 업종 시세 → 주도 섹터 선정 ─────────────
    print("[0단계] 네이버 금융 업종 시세 조회...")
    try:
        sector_rankings = _get_sector_rankings()
    except Exception as e:
        send_message(f"[{ts}] [V3] 업종 시세 조회 실패: {e}")
        return

    if not sector_rankings:
        send_message(f"[{ts}] [V3] 업종 시세 데이터 없음.")
        return

    top_sectors = sector_rankings[:TOP_N_SECTORS]
    print(f"  전체 {len(sector_rankings)}개 업종 중 상위 {TOP_N_SECTORS}개 선정")
    for s in top_sectors:
        print(f"  {s['name']}: {s['change_pct']:+.2f}%")
    print()

    # ── 1단계: 해당 섹터 종목 코드 수집 ──────────────────────
    print("[1단계] 주도 섹터 종목 수집...")
    universe_codes: dict[str, str] = {}  # code → sector_name
    for s in top_sectors:
        codes = _get_sector_stocks(s["no"])
        for c in codes:
            universe_codes[c] = s["name"]
        print(f"  {s['name']}: {len(codes)}개 종목")
    print(f"  총 유니버스: {len(universe_codes)}개\n")

    if not universe_codes:
        send_message(f"[{ts}] [V3] 유니버스 종목 없음.")
        return

    # 시총 필터 (FDR StockListing)
    try:
        listing = fdr.StockListing("KOSPI")
        marcap_col = next((c for c in ["Marcap", "MarketCap"] if c in listing.columns), None)
        if marcap_col:
            valid_codes = set(
                listing.loc[listing[marcap_col] >= MIN_MARCAP, "Code"].tolist()
            )
            before = len(universe_codes)
            universe_codes = {c: s for c, s in universe_codes.items() if c in valid_codes}
            print(f"  시총 {MIN_MARCAP // 100_000_000:,}억↑ 필터: {before} → {len(universe_codes)}개\n")
    except Exception:
        pass

    code_to_name: dict[str, str] = {}
    try:
        listing_all = fdr.StockListing("KOSPI")
        code_to_name = dict(zip(listing_all["Code"], listing_all["Name"]))
    except Exception:
        pass

    # ── 2단계: 5일 수익률·거래량 배율 병렬 계산 ──────────────
    print("[2단계] 5일 수익률·거래량 배율 계산 중...")
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

    codes = list(universe_codes.keys())
    stock_data: dict[str, dict] = {}
    done = 0
    total = len(codes)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch, c): c for c in codes}
        for f in as_completed(futures):
            done += 1
            if done % 20 == 0 or done == total:
                print(f"\r  진행: {done}/{total}", end="", flush=True)
            code, data = f.result()
            if data:
                stock_data[code] = data
    print(f"\n  데이터 수신: {len(stock_data)}개\n")

    if not stock_data:
        send_message(f"[{ts}] [V3] 가격 데이터 없음.")
        return

    # ── 3단계: 복합 점수 산출 ─────────────────────────────────
    print("[3단계] 복합 점수 산출...")
    df_univ = pd.DataFrame([
        {"stock_code": c, "sector": universe_codes[c], **stock_data[c]}
        for c in stock_data if c in universe_codes
    ])

    df_univ["momentum_score"] = df_univ["return_5d"].rank(pct=True) * 100
    df_univ["volume_score"]   = (df_univ["volume_ratio"] / 5 * 100).clip(0, 100)

    try:
        from src.db.client import get_company_health
        from src.screening.health_check import score_health
    except Exception:
        get_company_health = lambda c: None
        score_health       = lambda d: 50.0

    health_map: dict[str, float] = {}
    bonus_map:  dict[str, float] = {}
    for code in df_univ["stock_code"]:
        cached = get_company_health(code)
        health_map[code] = score_health(cached) if cached else 50.0
        recs = sentiment_map.get(code, [])
        label = _dominant_label(recs) if recs else None
        bonus_map[code] = 15.0 if label == "호재" else (-10.0 if label == "악재" else 0.0)

    df_univ["health_score"] = df_univ["stock_code"].map(health_map)
    df_univ["news_bonus"]   = df_univ["stock_code"].map(bonus_map)
    df_univ["total_score"]  = (
        df_univ["momentum_score"] * 0.35
        + df_univ["volume_score"]  * 0.25
        + df_univ["health_score"]  * 0.30
        + df_univ["news_bonus"]    * 0.10
    ).round(1)

    picks_raw = df_univ.sort_values("total_score", ascending=False).head(MAX_HOLD).to_dict("records")

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

    sector_summary = "  /  ".join(
        f"{s['name']} ({s['change_pct']:+.2f}%)" for s in top_sectors
    )
    lines.append(f"【 주도 섹터 】")
    lines.append(sector_summary)
    lines.append("")
    lines.append("【 오늘의 후보 종목 】")
    lines.append("종목 | RSI | 5일수익률 | 거래량 | 점수")
    lines.append(SEP)
    for p in picks_raw:
        name     = code_to_name.get(p["stock_code"], p["stock_code"])
        rsi      = rsi_map.get(p["stock_code"])
        rsi_str  = f"{rsi:.1f}" if rsi is not None else "N/A"
        signal   = " ◀매수" if rsi is not None and rsi < 35 else ""
        recs     = sentiment_map.get(p["stock_code"], [])
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
