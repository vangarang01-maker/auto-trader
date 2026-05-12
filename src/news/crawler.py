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


def crawl_naver_news(corp_name: str, max_articles: int = 10) -> list[dict]:
    """Google News RSS로 종목 뉴스 수집. 허용 출처만 반환."""
    feed = feedparser.parse(_RSS_URL.format(query=corp_name))
    articles = []
    for e in feed.entries:
        source = e.get("source", {}).get("title", "")
        if source not in _ALLOWED_SOURCES:
            continue
        articles.append({
            "title": e.get("title", ""),
            "url": e.get("link", ""),
            "published_at": e.get("published", ""),
            "source": source,
        })
        if len(articles) >= max_articles:
            break
    return articles
