import json
import math
import time
from datetime import datetime, date
from pathlib import Path

import pandas as pd

from src.broker.kis_client import KISClient
from src.indicators.rsi import calc_rsi

STATE_FILE         = "portfolio.json"
TOTAL              = 10_000_000  # 총 투자금액
MAX_HOLD           = 5           # 보유 종목 수
TAKE_PROFIT        = 0.15        # ATR 데이터 없을 때 기본 익절 (+15%)
STOP_LOSS          = 0.07        # ATR 데이터 없을 때 기본 손절 (-7%)
MAX_HOLD_DAYS      = 30          # 횡보 제한: 30일 내 최소 5% 미달성 시 청산
STAGNATION_GOAL    = 0.05        # 횡보 제한 기준 수익률
VOL_SURGE_RATIO    = 1.5         # 거래량 급증 기준 (20일 평균 대비)


class PortfolioManager:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.kis = KISClient(virtual=True)
        self._state = self._load_state()

    # ── 상태 파일 ──────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            state = json.loads(Path(STATE_FILE).read_text())
            state.setdefault("buy_dates", {})
            return state
        except (FileNotFoundError, json.JSONDecodeError):
            return {"last_picks": [], "last_run": None, "buy_dates": {}}

    def _save_state(self):
        Path(STATE_FILE).write_text(json.dumps(self._state, ensure_ascii=False, indent=2))

    # ── 종목 선정 ──────────────────────────────────────────

    def select_picks(self, df: pd.DataFrame) -> list[dict]:
        """
        건강검진 점수(health_score) 있으면 내림차순, 없으면 PEG 오름차순.
        NaN PEG 제외.
        """
        valid = df[df["peg"].notna()].copy()
        if "health_score" in valid.columns and valid["health_score"].notna().any():
            valid = valid.sort_values("health_score", ascending=False)
        else:
            valid = valid.sort_values("peg")
        cols = ["corp_code", "stock_code", "corp_name", "sector", "peg", "current_price",
                "net_income_growth", "revenue_growth", "debt_ratio",
                "upside_capture", "downside_capture", "health_score"]
        available = [c for c in cols if c in valid.columns]
        return valid[available].head(MAX_HOLD).to_dict("records")

    def needs_rebalance(self, picks: list[dict]) -> bool:
        new_codes = {p["stock_code"] for p in picks}
        old_codes = set(self._state.get("last_picks", []))
        return new_codes != old_codes

    # ── 시장 데이터 조회 ────────────────────────────────────

    def _fetch_market_data(self, codes: set) -> tuple[dict, dict, dict, dict]:
        """RSI, 마지막 종가, ATR, 거래량 반환.

        Returns:
            rsi_map   : {code: float}
            price_map : {code: float}  — 최근 종가
            atr_map   : {code: float}  — ATR-14
            vol_map   : {code: (today_vol, avg_20d_vol)}
        """
        from src.indicators.atr import calc_atr
        rsi_map   = {}
        price_map = {}
        atr_map   = {}
        vol_map   = {}
        for code in codes:
            try:
                ohlcv = self.kis.get_daily_ohlcv(code, count=60)
                prices = [d["close"] for d in ohlcv]
                rsi_map[code]   = calc_rsi(prices)
                price_map[code] = prices[-1] if prices else 0.0
                atr_map[code]   = calc_atr(ohlcv)
                if len(ohlcv) >= 21:
                    avg_20 = sum(d["volume"] for d in ohlcv[-21:-1]) / 20
                    vol_map[code] = (ohlcv[-1]["volume"], avg_20)
            except Exception as e:
                print(f"  [시장데이터 오류] {code}: {e}")
                rsi_map[code] = float("nan")
                atr_map[code] = float("nan")
        return rsi_map, price_map, atr_map, vol_map

    # ── 리밸런싱 ───────────────────────────────────────────

    def rebalance(self, picks: list[dict], bear_market: bool = False):
        """매도: 후보 제외 | RSI ≥ 75 | 익절(ATR기반) | 손절(ATR기반) | 횡보 제한(30일)
        매수: RSI < 35 AND 거래량 급증(1.5x) | bear_market=True 시 전면 차단
        """
        new_codes      = {p["stock_code"] for p in picks}
        pick_price_map = {p["stock_code"]: p["current_price"] for p in picks}
        name_map       = {p["stock_code"]: p["corp_name"] for p in picks}

        if self.dry_run:
            holdings   = {}
            avg_prices = {}
            print("  [DRY-RUN] 잔고 조회 생략 (보유 없음으로 가정)")
        else:
            try:
                raw        = self.kis.get_holdings()
                holdings   = {h["stock_code"]: h["qty"]       for h in raw}
                avg_prices = {h["stock_code"]: h["avg_price"] for h in raw}
            except Exception as e:
                print(f"  [오류] 잔고 조회 실패: {e}")
                return

        print("  시장 데이터(OHLCV·RSI·ATR) 수집 중...")
        rsi_map, last_price_map, atr_map, vol_map = self._fetch_market_data(
            set(holdings.keys()) | new_codes
        )

        full_price_map = {**last_price_map, **pick_price_map}
        buy_dates      = self._state.setdefault("buy_dates", {})

        def _atr_targets(avg_p: float, code: str) -> tuple[float, float]:
            """ATR 기반 익절/손절 비율. 데이터 없으면 기본값."""
            atr = atr_map.get(code, float("nan"))
            if atr == atr and atr > 0 and avg_p > 0:
                atr_pct = atr / avg_p
                tp = max(TAKE_PROFIT, min(0.30, 2.5 * atr_pct))
                sl = max(0.04,        min(STOP_LOSS, 1.5 * atr_pct))
                return round(tp, 4), round(sl, 4)
            return TAKE_PROFIT, STOP_LOSS

        # ── 매도 ──────────────────────────────────────────
        for code, qty in holdings.items():
            rsi     = rsi_map.get(code, float("nan"))
            rsi_str = f"{rsi:.1f}" if rsi == rsi else "N/A"
            avg_p   = avg_prices.get(code, 0)
            cur_p   = full_price_map.get(code, 0)
            name    = name_map.get(code, code)
            tp, sl  = _atr_targets(avg_p, code)

            if code not in new_codes:
                reason = "후보 제외"
            elif rsi == rsi and rsi >= 75:
                reason = f"RSI={rsi_str} ≥ 75"
            elif avg_p > 0 and cur_p > 0 and cur_p >= avg_p * (1 + tp):
                reason = f"익절 +{(cur_p/avg_p-1)*100:.1f}%  (ATR목표 +{tp*100:.1f}%)"
            elif avg_p > 0 and cur_p > 0 and cur_p <= avg_p * (1 - sl):
                reason = f"손절 {(cur_p/avg_p-1)*100:.1f}%  (ATR손절 -{sl*100:.1f}%)"
            else:
                # 횡보 제한 체크
                buy_date_str = buy_dates.get(code)
                stagnant = False
                if buy_date_str and avg_p > 0 and cur_p > 0:
                    days_held = (date.today() - date.fromisoformat(buy_date_str)).days
                    progress  = cur_p / avg_p - 1
                    if days_held > MAX_HOLD_DAYS and progress < STAGNATION_GOAL:
                        reason   = f"횡보 제한 ({days_held}일, {progress*100:+.1f}%)"
                        stagnant = True
                if not stagnant:
                    tp_price = f"{int(avg_p*(1+tp)):,}" if avg_p else "-"
                    sl_price = f"{int(avg_p*(1-sl)):,}"  if avg_p else "-"
                    days_str = f"  보유={( (date.today()-date.fromisoformat(buy_dates[code])).days if buy_dates.get(code) else '?')}일" if buy_dates.get(code) else ""
                    print(f"  [홀드] {name}({code}) RSI={rsi_str}  익절={tp_price}원  손절={sl_price}원{days_str}")
                    continue

            if self.dry_run:
                print(f"  [DRY-RUN 매도] {name}({code}) {qty}주  ({reason})")
            else:
                print(f"  [매도] {name}({code}) {qty}주  ({reason})")
                try:
                    self.kis.place_order(code, "sell", qty)
                    buy_dates.pop(code, None)
                except Exception as e:
                    print(f"  [오류] 매도 실패 ({code}): {e}")
                time.sleep(0.5)

        # ── 매수 ──────────────────────────────────────────
        if bear_market:
            print("  [약세장] 신규 매수 전면 차단 (KOSPI < 200MA)")
            self._state["last_picks"] = list(new_codes)
            self._state["last_run"]   = datetime.now().isoformat()
            self._save_state()
            return

        # 건강검진 점수 비례 예산 배분 (최소: 균등의 50%, 최대: 균등의 150%)
        scores     = [max(1.0, p.get("health_score") or 50.0) for p in picks]
        total_sc   = sum(scores)
        base_per   = TOTAL // MAX_HOLD
        budgets    = {
            p["stock_code"]: int(
                max(base_per * 0.5, min(base_per * 1.5, TOTAL * sc / total_sc))
            )
            for p, sc in zip(picks, scores)
        }

        for p in picks:
            code = p["stock_code"]
            if code in holdings:
                continue
            rsi     = rsi_map.get(code, float("nan"))
            rsi_str = f"{rsi:.1f}" if rsi == rsi else "N/A"

            if rsi != rsi or rsi >= 35:
                print(f"  [대기] {p['corp_name']}({code}) RSI={rsi_str}  (매수 신호 없음, 기준 < 35)")
                continue

            # 거래량 급증 확인 (전일 거래량 > 20일 평균 × 1.5)
            today_v, avg_v = vol_map.get(code, (0, 0))
            if avg_v > 0 and today_v < avg_v * VOL_SURGE_RATIO:
                vol_ratio = today_v / avg_v
                print(f"  [대기] {p['corp_name']}({code}) RSI={rsi_str}  거래량={vol_ratio:.1f}x  (기준 {VOL_SURGE_RATIO}x 미달)")
                continue

            price = pick_price_map.get(code)
            if not price or price <= 0:
                print(f"  [스킵] {p['corp_name']}({code}) 가격 없음")
                continue
            qty = math.floor(budgets[code] / price)
            if qty <= 0:
                print(f"  [스킵] {p['corp_name']}({code}) 예산 부족 (주가 {price:,}원)")
                continue

            tp, sl   = _atr_targets(price, code)
            tp_price = int(price * (1 + tp))
            sl_price = int(price * (1 - sl))
            vol_str  = f"  거래량={today_v/avg_v:.1f}x" if avg_v > 0 else ""
            bgt_str  = f"{budgets[code]//10000:.0f}만원"

            if self.dry_run:
                print(f"  [DRY-RUN 매수] {p['corp_name']}({code}) RSI={rsi_str}{vol_str}  예산={bgt_str}  {qty}주 × {price:,}원"
                      f"  → 익절 {tp_price:,}원(+{tp*100:.0f}%)  손절 {sl_price:,}원(-{sl*100:.0f}%)")
            else:
                print(f"  [매수] {p['corp_name']}({code}) RSI={rsi_str}{vol_str}  예산={bgt_str}  {qty}주 × {price:,}원"
                      f"  → 익절 {tp_price:,}원(+{tp*100:.0f}%)  손절 {sl_price:,}원(-{sl*100:.0f}%)")
                try:
                    self.kis.place_order(code, "buy", qty)
                    buy_dates[code] = date.today().isoformat()
                except Exception as e:
                    print(f"  [오류] 매수 실패 ({code}): {e}")
                time.sleep(0.5)

        self._state["last_picks"] = list(new_codes)
        self._state["last_run"]   = datetime.now().isoformat()
        self._save_state()
