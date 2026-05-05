from src.screening.fundamental import FundamentalScreener


def main():
    screener = FundamentalScreener()

    # 예시: 삼성전자, SK하이닉스, NAVER
    corp_codes = ["00126380", "00164779", "00266961"]
    year = "2023"

    print(f"[스크리닝] {year}년 사업보고서 기준")
    print(f"  조건: 영업이익률 10% 이상, 부채비율 200% 이하\n")

    result = screener.screen(corp_codes, year)
    if result.empty:
        print("조건을 만족하는 종목이 없습니다.")
    else:
        print(result.to_string(index=False))


if __name__ == "__main__":
    main()
