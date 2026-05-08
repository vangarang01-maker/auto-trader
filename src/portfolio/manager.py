import json
import math
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.broker.kis_client import KISClient
from src.indicators.rsi import calc_rsi

STATE_FILE  = "portfolio.json"
TOTAL       = 10_000_000  # 총 투자금액
MAX_HOLD    = 5           # 보유 종목 수
TAKE_PROFIT = 0.15        # 매수 평단가 대비 +15% 익절
STOP_LOSS   = 0.07        # 매수 평단가 대비 -7% 손절


class PortfolioManager:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.kis = KISClient(virtual=True)
        self._state = self._load_state()

    # ── 상태 파일 ──────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            return json.loads(Path(STATE_FILE).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"last_picks": [], "last_run": None}

    def _save_state(self):
        Path(STATE_FILE).write_text(json.dumps(self._state, ensure_ascii=False, indent=2))

    # ── 종목 선정 ──────────────────────────────────────────

    def select_picks(self, df: pd.DataFrame) -> list[dict]:
        """PEG 오름차순 상위 MAX_HOLD개. NaN 제외."""
        valid = df[df["peg"].notna()].sort_values("peg").head(MAX_HOLD)
        cols = ["corp_code", "stock_code", "corp_name", "sector", "peg", "current_price",
                "net_income_growth", "revenue_growth", "debt_ratio",
                "upside_capture", "downside_capture"]
        available = [c for c in cols if c in valid.columns]
        return valid[available].to_dict("records")

    def needs_rebalance(self, picks: list[dict]) -> bool:
        new_codes = {p["stock_code"] for p in picks}
        old_codes = set(self._state.get("last_picks", []))
        return new_codes != old_codes

    # ── RSI + 현재가 조회 ──────────────────────────────────

    def _fetch_rsi_map(self, codes: set) -> tuple[dict, dict]:
        """RSI와 마지막 종가를 함께 반환. (rsi_map, last_price_map)"""
        rsi_map = {}
        last_price_map = {}
        for code in codes:
            try:
                prices = self.kis.get_daily_prices(code)
                rsi_map[code] = calc_rsi(prices)
                if prices:
                    last_price_map[code] = prices[-1]
            except Exception as e:
                print(f"  [RSI 오류] {code}: {e}")
                rsi_map[code] = float("nan")
        return rsi_map, last_price_map

    # ── 리밸런싱 ───────────────────────────────────────────

    def rebalance(self, picks: list[dict]):
        """매도: 후보 제외 OR RSI ≥ 75 OR 익절(+15%) OR 손절(-7%)
        매수: 후보 포함 AND RSI < 35
        """
        new_codes = {p["stock_code"] for p in picks}
        pick_price_map = {p["stock_code"]: p["current_price"] for p in picks}
        name_map       = {p["stock_code"]: p["corp_name"] for p in picks}

        if self.dry_run:
            holdings  = {}
            avg_prices = {}
            print("  [DRY-RUN] 잔고 조회 생략 (보유 없음으로 가정)")
        else:
            try:
                raw = self.kis.get_holdings()
                holdings   = {h["stock_code"]: h["qty"]       for h in raw}
                avg_prices = {h["stock_code"]: h["avg_price"] for h in raw}
            except Exception as e:
                print(f"  [오류] 잔고 조회 실패: {e}")
                return

        # RSI + 마지막 종가 (보유 + 후보 전체)
        print("  RSI 계산 중...")
        rsi_map, last_price_map = self._fetch_rsi_map(set(holdings.keys()) | new_codes)

        # 현재가: picks의 실시간 가격 우선, 없으면 일봉 마지막 종가
        full_price_map = {**last_price_map, **pick_price_map}

        # ── 매도 ──────────────────────────────────────────
        for code, qty in holdings.items():
            rsi     = rsi_map.get(code, float("nan"))
            rsi_str = f"{rsi:.1f}" if rsi == rsi else "N/A"
            avg_p   = avg_prices.get(code, 0)
            cur_p   = full_price_map.get(code, 0)
            name    = name_map.get(code, code)

            if code not in new_codes:
                reason = "후보 제외"
            elif rsi == rsi and rsi >= 75:
                reason = f"RSI={rsi_str} ≥ 75"
            elif avg_p > 0 and cur_p > 0 and cur_p >= avg_p * (1 + TAKE_PROFIT):
                reason = f"익절 +{(cur_p / avg_p - 1) * 100:.1f}%"
            elif avg_p > 0 and cur_p > 0 and cur_p <= avg_p * (1 - STOP_LOSS):
                reason = f"손절 {(cur_p / avg_p - 1) * 100:.1f}%"
            else:
                tp_str = f"{int(avg_p * (1 + TAKE_PROFIT)):,}" if avg_p else "-"
                sl_str = f"{int(avg_p * (1 - STOP_LOSS)):,}"  if avg_p else "-"
                print(f"  [홀드] {name}({code}) RSI={rsi_str}  익절={tp_str}원  손절={sl_str}원")
                continue

            if self.dry_run:
                print(f"  [DRY-RUN 매도] {name}({code}) {qty}주  ({reason})")
            else:
                print(f"  [매도] {name}({code}) {qty}주  ({reason})")
                try:
                    self.kis.place_order(code, "sell", qty)
                except Exception as e:
                    print(f"  [오류] 매도 실패 ({code}): {e}")
                time.sleep(0.5)

        # ── 매수 (미보유 + RSI < 35) ──────────────────────
        budget_per = TOTAL // MAX_HOLD
        for p in picks:
            code = p["stock_code"]
            if code in holdings:
                continue
            rsi     = rsi_map.get(code, float("nan"))
            rsi_str = f"{rsi:.1f}" if rsi == rsi else "N/A"

            if rsi != rsi or rsi >= 35:
                print(f"  [대기] {p['corp_name']}({code}) RSI={rsi_str}  (매수 신호 없음, 기준 < 35)")
                continue

            price = pick_price_map.get(code)
            if not price or price <= 0:
                print(f"  [스킵] {p['corp_name']}({code}) 가격 없음")
                continue
            qty = math.floor(budget_per / price)
            if qty <= 0:
                print(f"  [스킵] {p['corp_name']}({code}) 예산 부족 (주가 {price:,}원)")
                continue

            tp_price = int(price * (1 + TAKE_PROFIT))
            sl_price = int(price * (1 - STOP_LOSS))

            if self.dry_run:
                print(f"  [DRY-RUN 매수] {p['corp_name']}({code}) RSI={rsi_str}  {qty}주 × {price:,}원"
                      f"  → 익절 {tp_price:,}원  손절 {sl_price:,}원")
            else:
                print(f"  [매수] {p['corp_name']}({code}) RSI={rsi_str}  {qty}주 × {price:,}원"
                      f"  → 익절 {tp_price:,}원  손절 {sl_price:,}원")
                try:
                    self.kis.place_order(code, "buy", qty)
                except Exception as e:
                    print(f"  [오류] 매수 실패 ({code}): {e}")
                time.sleep(0.5)

        self._state["last_picks"] = list(new_codes)
        self._state["last_run"]   = datetime.now().isoformat()
        self._save_state()
