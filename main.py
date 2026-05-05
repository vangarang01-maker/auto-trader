from src.screening.fundamental import FundamentalScreener

OUTPUT_FILE = "screening_result.csv"
YEAR = "2025"
MARKET = "Y"  # Y=KOSPI, K=KOSDAQ, ""=전체


def main():
    screener = FundamentalScreener()

    print(f"[피터 린치 스크리닝] {YEAR}년(회계연도) / KOSPI")
    print("  1단계(DART): 순이익 흑자, 순이익 성장률 ≥20%, 매출 성장률 ≥10%, 부채비율 ≤50%, 영업CF > 0")
    print("  2단계(KIS):  PEG < 1\n")

    # 1단계: DART 재무 스크리닝
    dart_picks = screener.screen_all(year=YEAR, market=MARKET)
    if dart_picks.empty:
        print("1단계 통과 종목이 없습니다.")
        return
    print(f"\n  1단계 통과: {len(dart_picks)}개\n")

    # 2단계: KIS 현재가 기반 PEG 필터
    print("  2단계(PEG) 필터 적용 중...")
    result = screener.apply_peg_filter(dart_picks)

    if result.empty:
        print("최종 통과 종목이 없습니다.")
        return

    display_cols = ["corp_name", "peg_ratio", "pe_ratio", "net_income_growth", "revenue_growth", "debt_ratio", "current_price"]
    available_cols = [c for c in display_cols if c in result.columns]

    sort_col = "peg_ratio" if "peg_ratio" in result.columns else "net_income_growth"
    result = result.sort_values(sort_col, ascending=True).reset_index(drop=True)
    result.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print(f"\n  최종 통과: {len(result)}개 → {OUTPUT_FILE} 저장 완료\n")
    print(result[available_cols].to_string(index=False))


if __name__ == "__main__":
    main()
