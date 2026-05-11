"""
네이버 금융 + 한국경제에서 시장 헤드라인과 관련 종목코드를 수집한다.
관련 종목코드는 run_screen.py에서 최종 픽 태깅에 사용된다.
"""
import re
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

_NAVER_NEWS_URL = "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"
_HANKYUNG_URL = "https://www.hankyung.com/finance"


def _extract_stock_codes(html: str) -> list[str]:
    """HTML에서 네이버 금융 종목코드 패턴 추출 (?code=XXXXXX 형태)."""
    return list(set(re.findall(r"[?&]code=(\d{6})", html)))


def _fetch_article_codes(url: str) -> list[str]:
    """기사 본문 URL에서 관련 종목코드 추출."""
    try:
        res = requests.get(url, headers=_HEADERS, timeout=10)
        res.encoding = "euc-kr"
        return _extract_stock_codes(res.text)
    except Exception:
        return []


def crawl_naver_finance_news(article_limit: int = 10) -> tuple[list[str], list[str]]:
    """
    네이버 금융 기업뉴스 섹션 헤드라인과 관련 종목코드 반환.
    article_limit: 본문 방문할 기사 수 (종목코드 추출 목적).
    """
    headlines: list[str] = []
    codes: set[str] = set()

    try:
        res = requests.get(_NAVER_NEWS_URL, headers=_HEADERS, timeout=10)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")

        # 목록 페이지 자체에서 종목코드 1차 추출
        codes.update(_extract_stock_codes(res.text))

        # 기사 링크 수집 (selector 순서대로 시도)
        article_links: list[tuple[str, str]] = []
        for selector in [
            "dl.articleSubjectList dd a",
            ".articleSubject a",
            "ul.newsList li a",
        ]:
            tags = soup.select(selector)
            if tags:
                for a in tags:
                    title = a.get_text(strip=True)
                    href = a.get("href", "")
                    if title and href:
                        full = "https://finance.naver.com" + href if href.startswith("/") else href
                        article_links.append((title, full))
                break

        headlines = [t for t, _ in article_links if t]

        # 상위 article_limit개 기사 본문 방문 → 종목코드 2차 추출
        for _, url in article_links[:article_limit]:
            codes.update(_fetch_article_codes(url))
            time.sleep(0.2)

    except Exception as e:
        print(f"  [네이버 금융 뉴스 오류]: {e}")

    return headlines, list(codes)


def crawl_hankyung_news(limit: int = 20) -> list[str]:
    """한국경제 금융 섹션 헤드라인 수집."""
    headlines: list[str] = []
    try:
        res = requests.get(_HANKYUNG_URL, headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        for selector in [
            "h3.news-tit a",
            "h4.news-tit a",
            ".article-list__title a",
            "strong.news-tit a",
        ]:
            tags = soup.select(selector)
            if tags:
                headlines = [t.get_text(strip=True) for t in tags[:limit] if t.get_text(strip=True)]
                if headlines:
                    break

    except Exception as e:
        print(f"  [한국경제 뉴스 오류]: {e}")

    return headlines


def get_market_news() -> tuple[list[str], list[str]]:
    """
    네이버 금융 + 한국경제 헤드라인과 관련 종목코드 반환.
    Returns: (all_headlines, stock_codes)
    """
    naver_headlines, naver_codes = crawl_naver_finance_news()
    hankyung_headlines = crawl_hankyung_news()

    all_headlines = naver_headlines + hankyung_headlines
    return all_headlines, naver_codes
