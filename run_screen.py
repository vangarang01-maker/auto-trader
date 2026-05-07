"""매일 07:30 1회 실행 — 종목 스크리닝 후 picks.json 저장"""
import json
from datetime import datetime
from pathlib import Path

from src.screening.fundamental import FundamentalScreener
from src.portfolio.manager import PortfolioManager
from src.notify.telegram import send_message

YEAR     = str(datetime.now().year - 1) if datetime.now().month >= 4 else str(datetime.now().year - 2)
MARKET   = "KOSPI"
PICKS_FILE = "picks.json"


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"[{ts}] 스크리닝 시작 (기준연도: {YEAR})")
    print(f"{'='*50}\n")

    screener = FundamentalScreener()

    print("[1단계] DART 재무 스크리닝...")
    dart_picks = screener.screen_all(year=YEAR, market=MARKET, workers=32)
    if dart_picks.empty:
        print("  통과 종목 없음. 종료.")
        return
    print(f"  1단계 통과: {len(dart_picks)}개\n")

    print("[2단계] KIS PEG 필터...")
    result = screener.apply_peg_filter(dart_picks)
    if result.empty:
        print("  PEG 통과 종목 없음. 종료.")
        return

    print(f"  2단계 통과: {len(result)}개\n")

    print("[3단계] KOSPI 초과성과 필터...")
    result = screener.apply_kospi_outperformance_filter(result)
    if result.empty:
        print("  KOSPI 초과성과 종목 없음. 종료.")
        send_message(f"[{ts}] 오늘의 자동매매 후보 종목\n\n3단계(KOSPI 초과성과) 통과 종목이 없습니다.")
        return

    print(f"  3단계 통과: {len(result)}개\n")

    pm = PortfolioManager(dry_run=True)
    picks = pm.select_picks(result)
    if not picks:
        print("  PEG 계산된 종목 없음. 종료.")
        send_message(f"[{ts}] 오늘의 자동매매 후보 종목\n\nPEG 계산 가능한 종목이 없습니다.")
        return

    print(f"\n[선정 종목] PEG 기준 상위 {len(picks)}개")
    for p in picks:
        print(f"  {p['corp_name']}({p['stock_code']})  PEG={p['peg']}  현재가={p['current_price']:,.0f}원")

    # 텔레그램 알림
    lines = [f"[{ts}] 오늘의 자동매매 후보 종목 ({len(picks)}개)\n"]
    for i, p in enumerate(picks, 1):
        up  = p.get("upside_capture", "-")
        dn  = p.get("downside_capture", "-")
        up_str = f"{up}%" if isinstance(up, float) else up
        dn_str = f"{dn}%" if isinstance(dn, float) else dn
        lines.append(
            f"{i}. {p['corp_name']} ({p['stock_code']})\n"
            f"   PEG={p['peg']}  현재가={p['current_price']:,.0f}원\n"
            f"   상승포착={up_str}  하락포착={dn_str}"
        )
    lines.append("\n관심종목에 추가 후 RSI 신호를 기다리세요.")
    send_message("\n".join(lines))

    # stock_code, corp_name, peg 만 저장 (current_price는 trade 시점에 재조회)
    save_data = [{"stock_code": p["stock_code"], "corp_name": p["corp_name"], "peg": p["peg"]} for p in picks]
    Path(PICKS_FILE).write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
    print(f"\n→ {PICKS_FILE} 저장 완료.")


if __name__ == "__main__":
    main()
