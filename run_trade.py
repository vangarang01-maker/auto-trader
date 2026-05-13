"""매 시간 실행 — V1·V2 picks 통합 후 RSI 신호 기반 매매

우선순위:
  공통 종목 (V1+V2 모두 선정) → health_score + CROSS_BONUS
  V2만 선정                   → health_score 그대로 (주력 전략)
  V1만 선정                   → health_score 그대로 (보조 전략)
  점수 내림차순 상위 MAX_HOLD개 → 리밸런싱
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.indicators.market_regime import get_market_regime
from src.portfolio.manager import PortfolioManager

PICKS_V1_FILE = "picks_v1.json"
PICKS_V2_FILE = "picks_v2.json"
MAX_HOLD      = 5
CROSS_BONUS   = 10  # 두 전략 모두 선정 시 추가 점수


def _load_picks(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()) or []
    except Exception:
        return []


def _merge_picks(v1: list[dict], v2: list[dict]) -> list[dict]:
    """V1·V2 후보를 통합해 조정 점수 기준 상위 MAX_HOLD개 반환."""
    v1_map = {p["stock_code"]: p for p in v1}
    v2_map = {p["stock_code"]: p for p in v2}

    merged = []
    for code in set(v1_map) | set(v2_map):
        in_v1 = code in v1_map
        in_v2 = code in v2_map
        base  = (v2_map if in_v2 else v1_map)[code].copy()

        scores = [
            p.get("health_score") or 0
            for p in [v1_map.get(code), v2_map.get(code)]
            if p is not None
        ]
        base_score = max(scores) if scores else 0
        bonus      = CROSS_BONUS if (in_v1 and in_v2) else 0

        base["adjusted_score"] = round(min(100.0, base_score + bonus), 1)
        base["strategies"]     = "V1+V2" if (in_v1 and in_v2) else ("V1" if in_v1 else "V2")
        merged.append(base)

    merged.sort(key=lambda x: x["adjusted_score"], reverse=True)
    return merged[:MAX_HOLD]


def main():
    ts = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"[{ts}] RSI 매매 신호 점검 (V1+V2 통합)")
    print(f"{'='*50}\n")

    regime_info = get_market_regime()
    regime      = regime_info["regime"]
    bear_market = (regime == "bear")

    if regime_info["kospi"]:
        regime_emoji = "🐂" if not bear_market else "🐻"
        print(f"  {regime_emoji} 시장 국면: {'강세장' if not bear_market else '약세장'}"
              f"  KOSPI={regime_info['kospi']:,.2f}"
              f"  MA200={regime_info['ma200']:,.2f}"
              f"  ({regime_info['pct_diff']:+.2f}%)")
    else:
        print(f"  [시장 국면] 데이터 없음 (unknown) — 매수 허용으로 처리")
        bear_market = False
    print()

    v1_picks = _load_picks(PICKS_V1_FILE)
    v2_picks = _load_picks(PICKS_V2_FILE)

    if not v1_picks and not v2_picks:
        print(f"  {PICKS_V1_FILE}·{PICKS_V2_FILE} 모두 없음. 스크리닝을 먼저 실행하세요.")
        return

    print(f"  V1 후보: {len(v1_picks)}개  /  V2 후보: {len(v2_picks)}개")
    picks_raw = _merge_picks(v1_picks, v2_picks)

    print("\n  [통합 후보]")
    for p in picks_raw:
        star = " ★" if p["strategies"] == "V1+V2" else ""
        print(f"    [{p['strategies']}]{star} {p['corp_name']}({p['stock_code']})  score={p['adjusted_score']}")

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

    print(f"\n  최종 후보 {len(picks)}개 → 리밸런싱 시작")
    pm.rebalance(picks, bear_market=bear_market)
    print("\n완료.")


if __name__ == "__main__":
    main()
