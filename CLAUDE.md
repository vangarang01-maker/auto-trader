# CLAUDE.md — auto-trader

DART 전자공시 + KIS Open API 기반 자동 매매 시스템.
Python 3.11+, GitHub Actions cron으로 동작.

---

## 실행 흐름

```
[07:30 KST] run_screen.py  →  DART 재무 스크리닝 → PEG 필터 → picks.json 저장
                                                                  ↓ git commit & push (Actions)
[09:00~15:00 KST, 매 시간] run_trade.py  →  picks.json 읽기 → 현재가 재조회 → RSI 계산 → 주문
```

- `run_screen.py`: `PortfolioManager(dry_run=True)` — 주문 없이 종목만 선정
- `run_trade.py`: `PortfolioManager(dry_run=False)` — 실제 주문 실행
- `main.py`: 스크리닝 + 매매를 한 번에 실행하는 수동 실행용 스크립트 (개발/테스트용)

---

## 프로젝트 구조

```
auto-trader/
├── run_screen.py              # 07:30 스크리닝 진입점 → picks.json 저장
├── run_trade.py               # 매 시간 RSI 매매 진입점
├── main.py                    # 수동 실행용 (스크리닝+매매 통합)
├── picks.json                 # 당일 선정 종목 (screen이 갱신, trade가 소비)
├── portfolio.json             # 보유 종목 상태 (last_picks, last_run)
├── requirements.txt
├── src/
│   ├── dart/client.py         # DartClient: OpenDartReader 래퍼
│   ├── screening/fundamental.py  # FundamentalScreener: DART 스크리닝 + PEG 필터
│   ├── broker/kis_client.py   # KISClient: 시세·잔고·주문
│   ├── portfolio/manager.py   # PortfolioManager: 종목 선정, RSI 계산, 매매 실행
│   └── indicators/rsi.py      # calc_rsi(): Wilder 방식 RSI-14
└── .github/workflows/
    ├── screen.yml             # cron: "30 22 * * 0-4" (UTC) = KST 07:30 평일
    └── trade.yml              # cron: "0 0-6 * * 1-5"  (UTC) = KST 09:00~15:00 평일
```

---

## 4단계 매매 파이프라인

### 1단계 — DART 재무 스크리닝 (`FundamentalScreener.screen_all`)

KOSPI 전 종목을 ThreadPoolExecutor(workers=32)로 병렬 조회.
`DartClient.get_financial_statements`로 사업보고서 재무제표 파싱.
연결재무제표 우선, 없으면 별도재무제표 사용.

피터 린치 통과 조건 (`_is_lynch_pick`):
- 당기순이익 > 0
- 순이익 성장률 ≥ 20%
- 매출 성장률 ≥ 10%
- 부채비율 ≤ 50%
- 영업현금흐름 > 0 (데이터 없으면 통과)

사업보고서 기준연도: 4월 이후 → 전년도 / 4월 이전 → 전전년도.

### 2단계 — KIS PEG 필터 (`FundamentalScreener.apply_peg_filter`)

```
PEG = PER(KIS 실시간) / 순이익성장률(%)
```

- PEG ≤ 1.0만 통과
- KIS 조회 실패 시 통과 처리 (rows.append 후 continue)
- **주의**: `get_stock_quote`는 virtual 모드여도 항상 `REAL_URL` 사용 (시세는 실서버 전용)
- PEG가 NaN인 종목은 `select_picks`에서 제외됨 (`peg.notna()` 필터)

### 3단계 — KOSPI 초과성과 필터 (`FundamentalScreener.apply_kospi_outperformance_filter`)

최근 6개월 일봉 기준으로 KOSPI 지수 대비 초과성과 종목만 통과.

- **Upside Capture > 100%**: KOSPI 상승일에 평균적으로 더 많이 오른 종목
- **Downside Capture < 100%**: KOSPI 하락일에 평균적으로 덜 떨어진 종목

계산 방식:
```
upside_capture  = mean(종목 수익률 | KOSPI > 0) / mean(KOSPI 수익률 | KOSPI > 0) × 100
downside_capture = mean(종목 수익률 | KOSPI < 0) / mean(KOSPI 수익률 | KOSPI < 0) × 100
```

- 데이터 소스: `FinanceDataReader` (`KS11` = KOSPI 지수, 종목별 일봉)
- 공통 거래일 20일 미만 또는 상승/하락일 5일 미만이면 제외
- FDR 조회 실패 시도 제외 (PEG 필터와 달리 통과 처리 없음)
- ThreadPoolExecutor(workers=4)로 병렬 조회
- 통과 종목에 `upside_capture`, `downside_capture` 컬럼 추가

### 4단계 — RSI 매매 (`PortfolioManager.rebalance`)

매도 조건:
- 후보 종목에서 제외 (picks에 없음), 또는
- RSI-14 ≥ 75

매수 조건:
- 후보 종목에 포함 AND
- RSI-14 < 35 (미보유 종목만)

포지션 관리:
- 최대 보유: 5종목 (`MAX_HOLD = 5`)
- 총 투자금: 10,000,000원 (`TOTAL = 10_000_000`)
- 종목당 예산: 2,000,000원 (균등 배분, 시장가 주문)
- 주문 간 `time.sleep(0.5)` 간격

---

## 모듈별 핵심 사항

### `src/dart/client.py` — DartClient

- `OpenDartReader` 라이브러리 래퍼
- `get_financial_statements`: 최대 3회 재시도 (지수 백오프 1s, 2s)
- `get_listed_corp_codes(market)`: KOSPI/KOSDAQ 필터 시 `FinanceDataReader` 병용

### `src/broker/kis_client.py` — KISClient

- `virtual=True`: 모의투자 서버 (`openapivts.koreainvestment.com:29443`)
- `virtual=False`: 실서버 (`openapi.koreainvestment.com:9443`)
- **SSL 검증 비활성화** (`verify=False`) — 모의서버 인증서 호스트명 불일치 문제 우회
- 토큰 파일 캐싱: `.kis_token_virtual.json` / `.kis_token.json` (만료 60초 전 갱신)
- `get_daily_prices`: 항상 실서버 사용 (시세 데이터는 실서버만 가능)
- `get_holdings` / `place_order`: virtual 여부에 따라 tr_id 분기
  - 잔고: `VTTC8434R` (모의) / `TTTC8434R` (실전)
  - 매수: `VTTC0802U` / `TTTC0802U`
  - 매도: `VTTC0801U` / `TTTC0801U`

### `src/portfolio/manager.py` — PortfolioManager

- 상태 파일 `portfolio.json`: `last_picks`(종목코드 목록), `last_run`(ISO 타임스탬프)
- `dry_run=True`면 잔고 조회/주문 없이 로그만 출력
- `select_picks`: PEG 오름차순 상위 5개, NaN 제외

### `src/indicators/rsi.py` — calc_rsi

- Wilder's Smoothed Moving Average 방식
- `prices`: 오래된 순 종가 리스트, 최소 `period+1`개 필요
- 데이터 부족 시 `float("nan")` 반환

---

## 환경 변수 (`.env` 또는 GitHub Secrets)

| 변수명 | 용도 |
|--------|------|
| `DART_API_KEY` | DART Open API 키 |
| `KIS_APP_KEY` | KIS 실서버 앱키 (시세 조회용) |
| `KIS_APP_SECRET` | KIS 실서버 시크릿 |
| `KIS_VIRTUAL_APP_KEY` | KIS 모의투자 앱키 |
| `KIS_VIRTUAL_APP_SECRET` | KIS 모의투자 시크릿 |
| `KIS_ACCOUNT` | 계좌번호 (하이픈 포함/미포함 모두 가능, 앞 8자리=cano, 나머지=acnt_cd) |

`KIS_APP_KEY` 미설정 시 `apply_peg_filter`가 자동으로 virtual 모드 사용.

---

## 실전 전환 (현재: 모의투자)

1. `src/portfolio/manager.py:20` — `KISClient(virtual=True)` → `KISClient(virtual=False)`
2. `src/screening/fundamental.py:121` — `virtual = not os.getenv("KIS_APP_KEY")` → `virtual = False`
3. GitHub Secrets `KIS_ACCOUNT` → 실계좌번호로 교체
4. KIS HTS에서 실서버 API 별도 신청 필요

---

## 의존성

```
opendartreader==0.2.3
pandas==2.2.3
python-dotenv==1.0.1
requests==2.32.3
finance-datareader>=0.9.110
```

---

## GitHub Actions

| 워크플로우 | cron (UTC) | KST | 실행 스크립트 |
|-----------|------------|-----|--------------|
| screen.yml | `30 22 * * 0-4` | 평일 07:30 | `run_screen.py` |
| trade.yml | `0 0-6 * * 1-5` | 평일 09:00~15:00 | `run_trade.py` |

screen.yml은 `picks.json`을 자동 커밋(`git add picks.json → git commit → git push`).
trade.yml은 커밋 없이 실행만.
