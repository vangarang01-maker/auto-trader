"""매 시간 실행 — picks_v2.json 읽어서 RSI 신호 기반 매매"""
import json
from datetime import datetime
from pathlib import Path

from src.portfolio.manager import PortfolioManager

PICKS_FILE = "picks_v2.json"


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"[{ts}] RSI 매매 신호 점검")
    print(f"{'='*50}\n")

    if not Path(PICKS_FILE).exists():
        print(f"  {PICKS_FILE} 없음. run_screen.py를 먼저 실행하세요.")
        return

    picks_raw = json.loads(Path(PICKS_FILE).read_text())
    if not picks_raw:
        print("  선정 종목 없음. 종료.")
        return

    # 현재가 재조회
    pm = PortfolioManager(dry_run=False)
    picks = []
    for p in picks_raw:
        try:
            price = pm.kis.get_current_price(p["stock_code"])
            picks.append({**p, "current_price": price})
        except Exception as e:
            print(f"  [오류] {p['corp_name']}({p['stock_code']}) 현재가 조회 실패: {e}")

    if not picks:
        print("  현재가 조회 가능한 종목 없음. 종료.")
        return

    print(f"  후보 {len(picks)}개 현재가 조회 완료")
    pm.rebalance(picks)
    print("\n완료.")


if __name__ == "__main__":
    main()
