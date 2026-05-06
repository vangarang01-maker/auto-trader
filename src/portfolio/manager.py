import json
import math
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.broker.kis_client import KISClient

STATE_FILE = "portfolio.json"
TOTAL       = 10_000_000  # 총 투자금액
MAX_HOLD    = 5           # 보유 종목 수


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
        return valid[["stock_code", "corp_name", "peg", "current_price"]].to_dict("records")

    def needs_rebalance(self, picks: list[dict]) -> bool:
        new_codes = {p["stock_code"] for p in picks}
        old_codes = set(self._state.get("last_picks", []))
        return new_codes != old_codes

    # ── 리밸런싱 ───────────────────────────────────────────

    def rebalance(self, picks: list[dict]):
        new_codes  = {p["stock_code"] for p in picks}
        price_map  = {p["stock_code"]: p["current_price"] for p in picks}
        name_map   = {p["stock_code"]: p["corp_name"] for p in picks}

        if self.dry_run:
            holdings = {}
            print("  [DRY-RUN] 잔고 조회 생략 (보유 없음으로 가정)")
        else:
            try:
                holdings = {h["stock_code"]: h["qty"] for h in self.kis.get_holdings()}
            except Exception as e:
                print(f"  [오류] 잔고 조회 실패: {e}")
                return

        to_sell = [c for c in holdings if c not in new_codes]
        to_buy  = [c for c in new_codes if c not in holdings]

        # 매도 먼저
        for code in to_sell:
            qty = holdings[code]
            if self.dry_run:
                print(f"  [DRY-RUN 매도] {code} {qty}주")
            else:
                print(f"  [매도] {code} {qty}주")
                try:
                    self.kis.place_order(code, "sell", qty)
                except Exception as e:
                    print(f"  [오류] 매도 실패 ({code}): {e}")
                time.sleep(0.5)

        # 균등 매수 (총금액 / 종목 수)
        budget_per = TOTAL // MAX_HOLD
        for code in to_buy:
            price = price_map.get(code)
            if not price or price <= 0:
                print(f"  [스킵] {name_map.get(code, code)} 가격 없음")
                continue
            qty = math.floor(budget_per / price)
            if qty <= 0:
                print(f"  [스킵] {name_map.get(code, code)} 예산 부족 (주가 {price:,}원)")
                continue
            if self.dry_run:
                print(f"  [DRY-RUN 매수] {name_map.get(code, code)}({code}) {qty}주 × {price:,}원 = {qty*price:,.0f}원")
            else:
                print(f"  [매수] {name_map.get(code, code)}({code}) {qty}주 × {price:,}원")
                try:
                    self.kis.place_order(code, "buy", qty)
                except Exception as e:
                    print(f"  [오류] 매수 실패 ({code}): {e}")
                time.sleep(0.5)

        self._state["last_picks"] = list(new_codes)
        self._state["last_run"]   = datetime.now().isoformat()
        self._save_state()
