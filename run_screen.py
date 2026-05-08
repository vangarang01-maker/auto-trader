"""매일 07:30 1회 실행 — 종목 스크리닝 후 picks.json 저장"""
import json
from datetime import datetime, date
from pathlib import Path

import exchange_calendars as xcals

from src.screening.fundamental import FundamentalScreener
from src.portfolio.manager import PortfolioManager
from src.notify.telegram import send_message
from src.notify.ai_summary import summarize_pick

YEAR     = str(datetime.now().year - 1) if datetime.now().month >= 4 else str(datetime.now().year - 2)
MARKET   = "KOSPI"
PICKS_FILE = "picks.json"


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"[{ts}] 스크리닝 시작 (기준연도: {YEAR})")
    print(f"{'='*50}\n")

    if not xcals.get_calendar("XKRX").is_session(str(date.today())):
        print("  오늘은 KRX 휴장일입니다. 스크리닝을 건너뜁니다.")
        return

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

    # 텔레그램 알림 (DART 컨텍스트 + Gemini 요약 포함)
    print("\n[AI 요약] DART 공시 수집 및 Gemini 분석 중...")
    DIV = "─" * 28
    lines = [f"[{ts}] 자동매매 후보 종목 {len(picks)}개"]
    for i, p in enumerate(picks, 1):
        up_str = f"{p['upside_capture']:.1f}%" if isinstance(p.get('upside_capture'), float) else "-"
        dn_str = f"{p['downside_capture']:.1f}%" if isinstance(p.get('downside_capture'), float) else "-"
        sector = p.get('sector') or ""
        sector_str = f"  |  {sector}" if sector else ""

        context = ""
        if p.get("corp_code"):
            try:
                context = screener.client.get_company_context(p["corp_code"])
            except Exception as e:
                print(f"  [컨텍스트 오류] {p['corp_name']}: {e}")

        summary = summarize_pick(p, context)

        lines.append(f"\n{DIV}")
        lines.append(f"{i}. {p['corp_name']} ({p['stock_code']}){sector_str}")
        lines.append(f"   PEG {p['peg']}  |  현재가 {p['current_price']:,.0f}원")
        lines.append(f"   상승포착 {up_str}  |  하락포착 {dn_str}")
        if summary:
            lines.append("")
            for line in summary.splitlines():
                lines.append(f"   {line}")

    lines.append(f"\n{DIV}")
    lines.append("관심종목 추가 후 RSI 신호 대기")
    send_message("\n".join(lines))

    # stock_code, corp_name, peg 만 저장 (current_price는 trade 시점에 재조회)
    save_data = [{"stock_code": p["stock_code"], "corp_name": p["corp_name"], "peg": p["peg"]} for p in picks]
    Path(PICKS_FILE).write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
    print(f"\n→ {PICKS_FILE} 저장 완료.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        send_message(f"[{ts}] 스크리닝 실패\n\n{type(e).__name__}: {e}")
        raise
