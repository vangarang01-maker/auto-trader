"""
기업 건강검진 — 7개 지표 점수 산출 + DB 7일 캐시.

지표:   PER, PBR (가치) | ROE, ROIC, 영업이익률 (성장) | 부채비율, 배당수익률 (건전성)
캐시:   company_health 테이블, 7일 단위 lazy refresh
점수:   각 지표를 절대 기준값으로 0~max_points 선형 보간 후 합산
"""
from __future__ import annotations
from datetime import datetime, timezone


# ── 지표별 점수 설정 ────────────────────────────────────────────────
# (만점, 최적값, 최악값, 높을수록_좋음)
_METRIC_CONFIG: dict[str, tuple[float, float, float, bool]] = {
    "per":        (10, 10.0,  50.0,  False),
    "pbr":        (20,  1.0,   5.0,  False),
    "roe":        (10, 30.0,   0.0,  True),
    "roic":       (10, 20.0,   0.0,  True),
    "op_margin":  (10, 30.0,   0.0,  True),
    "debt_ratio": (20, 30.0, 200.0,  False),
    "div_yield":  (20,  4.0,   0.0,  True),
}


def _clamp_score(value: float | None, max_pts: float, best: float, worst: float, higher_better: bool) -> float:
    """지표 값을 0~max_pts 사이 점수로 변환."""
    if value is None:
        return 0.0
    if higher_better:
        ratio = (value - worst) / (best - worst) if best != worst else 0.0
    else:
        ratio = (worst - value) / (worst - best) if best != worst else 0.0
    return round(max(0.0, min(max_pts, ratio * max_pts)), 2)


def score_health(metrics: dict, weights: dict | None = None) -> float:
    """
    7개 지표를 합산해 0~100점 반환.
    weights: 기본값 _METRIC_CONFIG 사용. 커스텀 시 {지표명: 만점} dict 전달.
    """
    cfg = weights or _METRIC_CONFIG
    total = 0.0
    for key, config in cfg.items():
        max_pts, best, worst, higher = config
        total += _clamp_score(metrics.get(key), max_pts, best, worst, higher)
    return round(total, 1)


def _compute_roic(dart_metrics: dict) -> float | None:
    op = dart_metrics.get("operating_profit", 0)
    assets = dart_metrics.get("total_assets", 0)
    cur_liab = dart_metrics.get("current_liab", 0)
    invested = assets - cur_liab
    if invested <= 0:
        return None
    nopat = op * 0.78  # 법인세율 22% 근사
    return round(nopat / invested * 100, 1)


def get_or_fetch_health(
    stock_code: str,
    corp_name: str,
    dart_metrics: dict,
    kis,
    year: str,
) -> dict:
    """
    DB 캐시(7일) 우선 반환. 만료 시 DART + KIS 데이터로 재계산 후 저장.

    dart_metrics: FundamentalScreener.get_key_metrics() 결과
    kis: KISClient 인스턴스
    """
    from src.db.client import get_company_health, save_company_health

    cached = get_company_health(stock_code)
    if cached:
        return cached

    # KIS — PBR·배당수익률
    try:
        val = kis.get_stock_valuation(stock_code)
        per       = val.get("per")
        pbr       = val.get("pbr")
        div_yield = val.get("div_yield")
    except Exception as e:
        print(f"  [건강검진 KIS 오류] {stock_code}: {e}")
        per = pbr = div_yield = None

    equity = dart_metrics.get("equity", 0)
    net_income = dart_metrics.get("net_income", 0)
    op_profit  = dart_metrics.get("operating_profit", 0)
    revenue    = dart_metrics.get("revenue", 0)
    total_liab = dart_metrics.get("total_liabilities", 0)

    metrics = {
        "stock_code":  stock_code,
        "corp_name":   corp_name,
        "fiscal_year": year,
        "per":         per,
        "pbr":         pbr,
        "roe":         round(net_income / equity * 100, 1) if equity else None,
        "roic":        _compute_roic(dart_metrics),
        "op_margin":   round(op_profit / revenue * 100, 1) if revenue else None,
        "debt_ratio":  round(total_liab / equity * 100, 1) if equity else None,
        "div_yield":   div_yield,
        "fetched_at":  datetime.now(timezone.utc).isoformat(),
    }

    save_company_health(metrics)
    return metrics
