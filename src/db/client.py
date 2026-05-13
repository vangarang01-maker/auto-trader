import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()


def _get_client():
    from supabase import create_client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL 또는 SUPABASE_KEY가 설정되지 않았습니다.")
    return create_client(url, key)


def save_news(stock_code: str, corp_name: str, articles: list[dict]) -> int:
    """뉴스 저장 (URL 중복 시 upsert). 저장 시도 건수 반환."""
    rows = [
        {
            "stock_code": stock_code,
            "corp_name": corp_name,
            "title": a["title"],
            "url": a["url"],
            "published_at": a.get("published_at"),
            "source": a.get("source"),
        }
        for a in articles
        if a.get("url") and a.get("title")
    ]
    if not rows:
        return 0
    try:
        _get_client().table("news").upsert(rows, on_conflict="url").execute()
        return len(rows)
    except Exception as e:
        print(f"  [DB 오류] 뉴스 저장 실패: {e}")
        return 0


def get_news_bulk(stock_codes: list[str], days: int = 1) -> dict[str, list[dict]]:
    """여러 종목의 최근 N일 뉴스를 한 번에 조회. {stock_code: [articles]}"""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        res = (
            _get_client()
            .table("news")
            .select("stock_code, title, url, published_at, source")
            .in_("stock_code", stock_codes)
            .gte("created_at", since)
            .order("created_at", desc=True)
            .execute()
        )
        result: dict[str, list[dict]] = {}
        for row in (res.data or []):
            result.setdefault(row["stock_code"], []).append(row)
        return result
    except Exception as e:
        print(f"  [DB 오류] 뉴스 일괄 조회 실패: {e}")
        return {}


def get_recent_news(stock_code: str, days: int = 7) -> list[dict]:
    """최근 N일 DB 뉴스 반환."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        res = (
            _get_client()
            .table("news")
            .select("title, url, published_at, source")
            .eq("stock_code", stock_code)
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"  [DB 오류] 뉴스 조회 실패 ({stock_code}): {e}")
        return []


def get_company_health(stock_code: str) -> dict | None:
    """7일 이내 캐시된 건강검진 데이터 반환. 없거나 만료면 None."""
    since = (datetime.now() - timedelta(days=7)).isoformat()
    try:
        res = (
            _get_client()
            .table("company_health")
            .select("*")
            .eq("stock_code", stock_code)
            .gte("fetched_at", since)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"  [DB 오류] 건강검진 조회 실패 ({stock_code}): {e}")
        return None


def save_company_health(metrics: dict) -> None:
    """건강검진 데이터 upsert (stock_code PK 기준)."""
    try:
        _get_client().table("company_health").upsert(metrics, on_conflict="stock_code").execute()
    except Exception as e:
        print(f"  [DB 오류] 건강검진 저장 실패 ({metrics.get('stock_code')}): {e}")


def save_market_news(crawled_at: str, headlines: list[str], stock_codes: list[str], theme_analysis: str) -> None:
    """시장 뉴스 저장 (날짜 기준 upsert)."""
    row = {
        "crawled_at": crawled_at,
        "headlines": headlines,
        "stock_codes": stock_codes,
        "theme_analysis": theme_analysis,
    }
    try:
        _get_client().table("market_news").upsert(row, on_conflict="crawled_at").execute()
    except Exception as e:
        print(f"  [DB 오류] 시장 뉴스 저장 실패: {e}")


def get_latest_market_news(crawled_at: str) -> dict | None:
    """당일 시장 뉴스 조회. 없으면 None."""
    try:
        res = (
            _get_client()
            .table("market_news")
            .select("crawled_at, headlines, stock_codes, theme_analysis")
            .eq("crawled_at", crawled_at)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"  [DB 오류] 시장 뉴스 조회 실패: {e}")
        return None


def save_news_sentiment(date: str, records: list[dict]) -> None:
    """당일 뉴스 감성 분석 결과 저장 (날짜 기준 전체 교체)."""
    if not records:
        return
    try:
        client = _get_client()
        client.table("news_sentiment").delete().eq("crawled_at", date).execute()
        rows = [{"crawled_at": date, **r} for r in records]
        client.table("news_sentiment").insert(rows).execute()
    except Exception as e:
        print(f"  [DB 오류] 뉴스 감성 저장 실패: {e}")


def get_news_sentiment(date: str) -> list[dict]:
    """당일 뉴스 감성 분석 결과 조회."""
    try:
        res = (
            _get_client()
            .table("news_sentiment")
            .select("headline, stock_code, corp_name, label, reason")
            .eq("crawled_at", date)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"  [DB 오류] 뉴스 감성 조회 실패: {e}")
        return []


def save_screening_result(picks: list[dict], screened_date: str) -> None:
    """스크리닝 결과를 screening_history 테이블에 저장."""
    if not picks:
        return
    rows = [
        {
            "screened_at": screened_date,
            "stock_code": p.get("stock_code"),
            "corp_name": p.get("corp_name"),
            "sector": p.get("sector"),
            "peg": p.get("peg"),
            "current_price": int(p["current_price"]) if p.get("current_price") else None,
            "upside_capture": p.get("upside_capture"),
            "downside_capture": p.get("downside_capture"),
        }
        for p in picks
    ]
    try:
        _get_client().table("screening_history").insert(rows).execute()
    except Exception as e:
        print(f"  [DB 오류] 스크리닝 결과 저장 실패: {e}")
