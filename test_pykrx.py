"""pykrx 배당수익률 연동 검증 스크립트 (로그인 불필요)

pykrx==1.0.51 사용. KRX 계정 없이 동작.
장 마감 후(16:00 KST 이후) 또는 다음날 조회 시 당일 데이터가 반영됨.

실행:
  python test_pykrx.py
"""
import sys
from src.screening.strategy_v2 import ValueDividendScreener

print("pykrx 배당수익률 조회 중 (로그인 불필요)...\n")

div_map = ValueDividendScreener.fetch_div_map("KOSPI")

if not div_map:
    print("❌ 배당수익률 조회 실패")
    print("   - 장중 또는 휴장일: alternative=True로 직전 영업일 데이터 반환 시도")
    print("   - pykrx==1.0.51 설치 확인: pip install pykrx==1.0.51")
    sys.exit(1)

print(f"✅ {len(div_map)}개 종목 배당수익률 수신\n")

top = sorted(div_map.items(), key=lambda x: x[1], reverse=True)[:10]
print("배당수익률 상위 10개:")
for code, div in top:
    print(f"  {code}  {div:.2f}%")

samples = {"005490": "POSCO홀딩스", "033780": "KT&G", "017670": "SK텔레콤"}
print("\n주요 종목 배당수익률:")
for code, name in samples.items():
    div = div_map.get(code)
    print(f"  {name}({code}): {f'{div:.2f}%' if div else '데이터 없음'}")

print("\n✅ pykrx 연동 정상 동작 확인 완료")
