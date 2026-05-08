import time
import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def crawl_naver_news(stock_code: str, pages: int = 2) -> list[dict]:
    """네이버 금융 종목 뉴스 크롤링. 최근 pages 페이지 분량 반환."""
    articles = []
    for page in range(1, pages + 1):
        url = (
            f"https://finance.naver.com/item/news_news.naver"
            f"?code={stock_code}&page={page}"
        )
        try:
            res = requests.get(url, headers=_HEADERS, timeout=10)
            res.encoding = "euc-kr"
            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.select("table.type5 tbody tr")
            for row in rows:
                a_tag = row.select_one("td.title a")
                date_td = row.select_one("td.date")
                press_td = row.select_one("td.press")
                if not a_tag:
                    continue
                href = a_tag.get("href", "")
                if href.startswith("/"):
                    href = "https://finance.naver.com" + href
                articles.append({
                    "title": a_tag.get_text(strip=True),
                    "url": href,
                    "published_at": date_td.get_text(strip=True) if date_td else None,
                    "source": press_td.get_text(strip=True) if press_td else None,
                })
            time.sleep(0.3)
        except Exception as e:
            print(f"  [뉴스 크롤링 오류] {stock_code} page={page}: {e}")
            break
    return articles
