from datetime import datetime, timezone, timedelta

import feedparser

_ALLOWED_SOURCES = {
    "연합뉴스",
    "한국경제",
    "매일경제",
    "매일경제 마켓",
    "서울경제",
    "머니투데이",
    "Chosunbiz",
    "연합인포맥스",
}

_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
_KST     = timezone(timedelta(hours=9))


def _is_within_hours(entry, hours: int = 24) -> bool:
    """feedparser entry가 최근 N시간 이내 발행됐는지 확인."""
    parsed = getattr(entry, "published_parsed", None)
    if not parsed:
        return True  # 날짜 정보 없으면 통과
    pub_dt  = datetime(*parsed[:6], tzinfo=timezone.utc)
    cutoff  = datetime.now(timezone.utc) - timedelta(hours=hours)
    return pub_dt >= cutoff


def crawl_naver_news(corp_name: str, max_articles: int = 10) -> list[dict]:
    """Google News RSS로 종목 뉴스 수집. 허용 출처 + 24시간 이내 기사만 반환."""
    feed = feedparser.parse(_RSS_URL.format(query=corp_name))
    articles = []
    for e in feed.entries:
        if not _is_within_hours(e, hours=24):
            continue
        source = e.get("source", {}).get("title", "")
        if source not in _ALLOWED_SOURCES:
            continue
        articles.append({
            "title":        e.get("title", ""),
            "url":          e.get("link", ""),
            "published_at": e.get("published", ""),
            "source":       source,
        })
        if len(articles) >= max_articles:
            break
    return articles
