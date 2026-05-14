"""V3 섹터 주도주 — 데이터 수집·점수 산출 모듈"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import FinanceDataReader as fdr
import pandas as pd
import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}


# ── 네이버 금융 업종 ──────────────────────────────────────────

_SECTOR_BLACKLIST = {"기타"}


def get_sector_rankings(top_n: int = 3) -> list[dict]:
    """네이버 금융 업종 시세에서 당일 등락률 순 섹터 목록 반환."""
    r = requests.get(
        "https://finance.naver.com/sise/sise_group.naver?type=upjong",
        headers=_HEADERS, timeout=10,
    )
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
        if name in _SECTOR_BLACKLIST:
            continue
        m = re.search(r"no=(\d+)", a.get("href", ""))
        if not m:
            continue
        try:
            pct = float(
                tds[1].get_text(strip=True)
                .replace("+", "").replace("%", "").replace(",", "")
            )
        except ValueError:
            continue
        sectors.append({"name": name, "no": m.group(1), "change_pct": pct})

    return sorted(sectors, key=lambda x: x["change_pct"], reverse=True)[:top_n]


def get_sector_stocks(sector_no: str) -> list[str]:
    """네이버 금융 업종 상세에서 종목 코드 목록 반환."""
    codes: list[str] = []
    page = 1
    while True:
        r = requests.get(
            f"https://finance.naver.com/sise/sise_group_detail.naver"
            f"?type=upjong&no={sector_no}&page={page}",
            headers=_HEADERS, timeout=10,
        )
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


# ── 가격·거래량 ───────────────────────────────────────────────

def fetch_stock_momentum(
    codes: list[str],
    momentum_days: int = 5,
    volume_lookback: int = 20,
    workers: int = 8,
) -> dict[str, dict]:
    """종목별 N일 수익률·거래량 배율 병렬 계산. 데이터 부족 종목은 제외."""
    start = (date.today() - timedelta(days=40)).strftime("%Y-%m-%d")
    need  = volume_lookback + 2

    def _fetch(code: str) -> tuple[str, dict | None]:
        try:
            df = fdr.DataReader(code, start)[["Close", "Volume"]].dropna()
            if len(df) < need:
                return code, None
            return_nd    = round((df["Close"].iloc[-1] / df["Close"].iloc[-(momentum_days + 1)] - 1) * 100, 2)
            vol_avg      = df["Volume"].iloc[-(volume_lookback + 1):-1].mean()
            volume_ratio = round(df["Volume"].iloc[-1] / vol_avg, 2) if vol_avg > 0 else 1.0
            return code, {"return_5d": return_nd, "volume_ratio": volume_ratio}
        except Exception:
            return code, None

    result: dict[str, dict] = {}
    done, total = 0, len(codes)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch, c): c for c in codes}
        for f in as_completed(futures):
            done += 1
            if done % 20 == 0 or done == total:
                print(f"\r  진행: {done}/{total}", end="", flush=True)
            code, data = f.result()
            if data:
                result[code] = data
    print()
    return result


# ── 건강검진 ─────────────────────────────────────────────────

def fetch_health_map(
    codes: list[str],
    code_to_name: dict[str, str],
    kis,
    workers: int = 8,
) -> dict[str, float]:
    """종목별 건강검진 점수. DB 캐시 우선, 없으면 KIS 조회 (PER/PBR/배당만)."""
    from datetime import datetime, timezone

    from src.db.client import get_company_health, save_company_health
    from src.screening.health_check import score_health

    def _score(code: str) -> tuple[str, float]:
        cached = get_company_health(code)
        if cached:
            raw = score_health(cached)
            # DART 데이터 없는 캐시(roe/roic/op_margin/debt_ratio 모두 None)면 정규화
            dart_missing = all(cached.get(k) is None for k in ("roe", "roic", "op_margin", "debt_ratio"))
            return code, min(100.0, round(raw * 2.0, 1)) if dart_missing else raw
        try:
            val = kis.get_stock_valuation(code)
            metrics = {
                "stock_code": code,
                "corp_name":  code_to_name.get(code, code),
                "per":        val.get("per"),
                "pbr":        val.get("pbr"),
                "div_yield":  val.get("div_yield"),
                "roe":        None,
                "roic":       None,
                "op_margin":  None,
                "debt_ratio": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            save_company_health(metrics)
            # V3는 DART 없이 per/pbr/div_yield만 사용 → 만점 50 → 100점으로 정규화
            raw = score_health(metrics)
            return code, min(100.0, round(raw * 2.0, 1))
        except Exception:
            return code, 50.0

    result: dict[str, float] = {}
    done, total = 0, len(codes)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_score, c): c for c in codes}
        for f in as_completed(futures):
            done += 1
            if done % 20 == 0 or done == total:
                print(f"\r  진행: {done}/{total}", end="", flush=True)
            code, score = f.result()
            result[code] = score
    print()
    return result


# ── 복합 점수 ─────────────────────────────────────────────────

def score_universe(
    stock_data: dict[str, dict],
    universe_codes: dict[str, str],        # code → sector_name
    health_map: dict[str, float],          # code → health_score
    news_bonus_map: dict[str, float],      # code → bonus
) -> pd.DataFrame:
    """복합 점수(모멘텀 35% + 거래량 25% + 건강검진 30% + 뉴스감성 10%) 산출."""
    df = pd.DataFrame([
        {"stock_code": c, "sector": universe_codes[c], **stock_data[c]}
        for c in stock_data if c in universe_codes
    ])
    if df.empty:
        return df

    df["momentum_score"] = df["return_5d"].rank(pct=True) * 100
    df["volume_score"]   = (df["volume_ratio"] / 5 * 100).clip(0, 100)
    df["health_score"]   = df["stock_code"].map(health_map)
    df["news_bonus"]     = df["stock_code"].map(news_bonus_map)
    df["total_score"]    = (
        df["momentum_score"] * 0.35
        + df["volume_score"]  * 0.25
        + df["health_score"]  * 0.30
        + df["news_bonus"]    * 0.10
    ).round(1)

    return df.sort_values("total_score", ascending=False)
