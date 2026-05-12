"""네이버 금융 배당수익률 스크래핑 검증 (로그인 불필요)

실행:
  python test_div_naver.py
"""
import sys
from src.screening.strategy_v2 import ValueDividendScreener

SAMPLES = {
    "005490": "POSCO홀딩스",
    "033780": "KT&G",
    "017670": "SK텔레콤",
    "000660": "SK하이닉스",
    "030200": "KT",
}

print("네이버 금융 배당수익률 조회 중...\n")
div_map = ValueDividendScreener.fetch_div_naver(list(SAMPLES.keys()))

if not div_map:
    print("❌ 배당수익률 조회 실패")
    sys.exit(1)

print(f"✅ {len(div_map)}개 종목 배당수익률 수신\n")
print("주요 종목 배당수익률:")
for code, name in SAMPLES.items():
    div = div_map.get(code)
    print(f"  {name}({code}): {f'{div:.2f}%' if div else '데이터 없음'}")
