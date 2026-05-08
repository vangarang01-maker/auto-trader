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
