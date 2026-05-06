"""매일 7:30 자동 실행 진입점"""
import sys
from datetime import datetime

from src.screening.fundamental import FundamentalScreener
from src.portfolio.manager import PortfolioManager

YEAR   = "2025"
MARKET = "KOSPI"


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"[{ts}] 피터 린치 자동 스크리닝 + 리밸런싱 시작")
    print(f"{'='*50}\n")

    # 1단계: 스크리닝
    screener = FundamentalScreener()
    print("[1단계] DART 재무 스크리닝...")
    dart_picks = screener.screen_all(year=YEAR, market=MARKET)
    if dart_picks.empty:
        print("  통과 종목 없음. 종료.")
        return

    print(f"  1단계 통과: {len(dart_picks)}개\n")

    print("[2단계] KIS PEG 필터...")
    result = screener.apply_peg_filter(dart_picks)

    # 2단계: 종목 선정
    pm = PortfolioManager()
    picks = pm.select_picks(result)

    if not picks:
        print("  PEG 계산된 종목 없음. 종료.")
        return

    print(f"\n[선정 종목] PEG 기준 상위 {len(picks)}개")
    for p in picks:
        print(f"  {p['corp_name']}({p['stock_code']})  PEG={p['peg']}  현재가={p['current_price']:,.0f}원")

    # 3단계: 리밸런싱
    if not pm.needs_rebalance(picks):
        print("\n[리밸런싱] 보유 종목 변동 없음. 스킵.")
        return

    print("\n[리밸런싱] 종목 변경 감지 → 매매 실행")
    pm.rebalance(picks)
    print("\n완료.")


if __name__ == "__main__":
    main()
