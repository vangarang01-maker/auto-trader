"""pykrx 배당수익률 연동 검증 스크립트

실행 전 .env에 KRX_ID / KRX_PW 설정 필요:
  KRX_ID=your_krx_id
  KRX_PW=your_krx_password

KRX 계정 생성: https://www.krx.co.kr → 회원가입 (무료)

실행:
  python test_pykrx.py
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

KRX_ID = os.getenv("KRX_ID")
KRX_PW = os.getenv("KRX_PW")

if not KRX_ID or not KRX_PW:
    print("❌ KRX_ID / KRX_PW 가 .env에 없습니다.")
    print("   .env 파일에 다음을 추가하세요:")
    print("   KRX_ID=your_id")
    print("   KRX_PW=your_password")
    sys.exit(1)

print(f"✅ KRX 자격증명 확인 (ID: {KRX_ID[:3]}***)")
print("pykrx 배당수익률 조회 중...\n")

from src.screening.strategy_v2 import ValueDividendScreener

div_map = ValueDividendScreener.fetch_div_map("KOSPI")

if not div_map:
    print("❌ 배당수익률 조회 실패")
    print("   - KRX_ID/KRX_PW가 맞는지 확인하세요")
    print("   - 장 마감 후(16:00 KST 이후)에 조회하세요")
    sys.exit(1)

print(f"✅ {len(div_map)}개 종목 배당수익률 수신\n")

# DIV 상위 10개 출력
top = sorted(div_map.items(), key=lambda x: x[1], reverse=True)[:10]
print("배당수익률 상위 10개:")
for code, div in top:
    print(f"  {code}  {div:.2f}%")

# 주요 종목 확인
samples = {"005490": "POSCO홀딩스", "033780": "KT&G", "017670": "SK텔레콤"}
print("\n주요 종목 배당수익률:")
for code, name in samples.items():
    div = div_map.get(code)
    status = f"{div:.2f}%" if div else "데이터 없음"
    print(f"  {name}({code}): {status}")

print("\n✅ pykrx 연동 정상 동작 확인 완료")
