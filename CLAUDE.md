# CLAUDE.md — auto-trader

DART 전자공시 + KIS Open API 기반 자동 매매 시스템.
Python 3.11+, GitHub Actions cron으로 동작.

---

## 실행 흐름

```
[07:25 KST] run_news.py
  네이버 금융 + 한국경제 크롤링
  → Gemini 테마 분석 → DB(market_news) + news.json 저장
  → Gemini 감성 분석 → DB(news_sentiment) 저장

[07:30 KST] run_screen.py   (V1 — 피터 린치)
  0단계 DB 뉴스 + 감성맵 로드
  1단계 DART 재무 스크리닝 (피터 린치 5조건)
  2단계 KIS PEG 필터 (PEG ≤ 1.0)
  3단계 KOSPI 초과성과 필터
  건강검진 + 뉴스 감성 가중치(호재+10/악재-10) → 상위 5개 → picks_v1.json

[07:30 KST] run_screen_v2.py   (V2 — 고배당-저PBR-고ROE, 병렬 실행)
  0단계 DB 뉴스 + 감성맵 로드
  1단계 DART 필터 (ROE≥10 / 부채비율≤150 / 이자보상배율≥3)
  2단계 KIS 밸류에이션 (PBR 0.8~1.2 / 배당수익률≥3.5%)
  3단계 FDR 6개월 모멘텀 (상위 20%)
  건강검진 + 뉴스 감성 가중치(호재+10/악재-10) → 상위 5개 → picks_v2.json

[09:00~15:00 KST, 매 시간] run_trade.py   (V1+V2 통합 매매)
  picks_v1.json + picks_v2.json 로드
  공통 종목 → health_score + CROSS_BONUS(10점)
  adjusted_score 내림차순 상위 5개 → RSI 매매
```

- `run_news.py`: 뉴스 크롤링 + Gemini 테마·감성 분석 → DB 저장
- `run_screen.py`: V1 스크리닝 → picks_v1.json (health_score 포함)
- `run_screen_v2.py`: V2 스크리닝 → picks_v2.json (health_score 포함). screen.yml과 동시 실행
- `run_trade.py`: picks_v1.json + picks_v2.json 통합, CROSS_BONUS=10, adjusted_score 상위 5개 매매
- `main.py`: 수동 실행용 (스크리닝+매매 통합, 개발/테스트용)

---

## 프로젝트 구조

```
auto-trader/
├── run_news.py                # 07:25 뉴스 크롤링 + 테마·감성 분석 → DB 저장
├── run_screen.py              # 07:30 V1 스크리닝 → picks.json
├── run_screen_v2.py           # 07:30 V2 스크리닝 → picks_v2.json (병렬 실행)
├── run_trade.py               # 매 시간 RSI 매매 (picks.json 소비)
├── main.py                    # 수동 실행용
├── picks_v1.json              # V1 당일 선정 종목 (stock_code, corp_name, peg, health_score)
├── picks_v2.json              # V2 당일 선정 종목 (stock_code, corp_name, div_yield, health_score)
├── portfolio.json             # 보유 종목 상태
├── docs/
│   ├── strategy_v1.md         # V1 전략 설계 문서
│   └── strategy_v2.md         # V2 전략 설계 문서 (미구현 항목 포함)
├── src/
│   ├── dart/client.py         # DartClient: OpenDartReader 래퍼
│   ├── screening/
│   │   ├── fundamental.py     # FundamentalScreener: V1 (피터 린치·PEG·KOSPI 필터)
│   │   ├── strategy_v2.py     # ValueDividendScreener: V2 (ROE·PBR·배당·모멘텀)
│   │   └── health_check.py    # 건강검진 7개 지표 점수화 + DB 7일 캐시 (공통)
│   ├── broker/kis_client.py   # KISClient: 시세·잔고·주문·밸류에이션
│   ├── portfolio/manager.py   # PortfolioManager: RSI 계산·매매 실행
│   ├── indicators/rsi.py      # calc_rsi(): Wilder 방식 RSI-14
│   ├── notify/
│   │   ├── telegram.py        # 텔레그램 전송
│   │   └── ai_summary.py      # Gemini 종목 요약·시장 테마·뉴스 감성 분석
│   ├── db/client.py           # Supabase (news, market_news, company_health, news_sentiment, screening_history)
│   └── news/
│       ├── crawler.py         # 종목별 뉴스 크롤러
│       └── market_news.py     # 시장 뉴스 크롤러 (네이버 금융 + 한국경제)
└── .github/workflows/
    ├── news.yml               # cron: "25 22 * * 0-4" = KST 07:25 평일
    ├── screen.yml             # cron: "30 22 * * 0-4" = KST 07:30 평일 (V1)
    ├── screen_v2.yml          # cron: "30 22 * * 0-4" = KST 07:30 평일 (V2)
    └── trade.yml              # cron: "0 0-6 * * 1-5" = KST 09:00~15:00 평일
```

---

## 5단계 매매 파이프라인

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

최근 **3개월(약 60 거래일)** 일봉 기준으로 KOSPI 지수 대비 초과성과 종목만 통과.

**계산 방식 — 1일 단위 등락률 비교:**

60 거래일을 두 그룹으로 분류한 뒤, 각 그룹에서 평균 등락률 비율을 계산한다.

```
상승포착률 = mean(종목 일별 등락률 | KOSPI 상승일) / mean(KOSPI 일별 등락률 | KOSPI 상승일) × 100
하락포착률 = mean(종목 일별 등락률 | KOSPI 하락일) / mean(KOSPI 일별 등락률 | KOSPI 하락일) × 100
```

예) 상승포착 130%, 하락포착 80% → KOSPI 오를 때 1.3배 따라 오르고, 내릴 때는 0.8배만 따라 내림.

통과 조건: **상승포착률 > 하락포착률**

- 데이터 소스: `FinanceDataReader` (`KS11` = KOSPI 지수, 종목별 일봉)
- 공통 거래일 20일 미만 또는 상승/하락일 각 5일 미만이면 제외
- FDR 조회 실패 시 제외 (PEG 필터와 달리 통과 처리 없음)
- ThreadPoolExecutor(workers=4)로 병렬 조회
- 통과 종목에 `upside_capture`, `downside_capture` 컬럼 추가

### 전략 V1 vs V2 비교

| 항목 | V1 피터 린치 | V2 고배당-저PBR-ROE |
|------|------------|-------------------|
| 스크리너 | `FundamentalScreener` | `ValueDividendScreener` |
| 부채비율 | ≤ 50% | ≤ 150% |
| 성장 지표 | 순이익성장률 ≥ 20% | ROE ≥ 10% |
| 밸류에이션 | PEG ≤ 1.0 | PBR 0.8~1.2 |
| 배당 역할 | 건강검진 가중치만 | 핵심 필터 (≥ 3.5%) |
| 모멘텀 | KOSPI 대비 초과성과 | 6개월 절대 수익률 상위 20% |
| 출력 | picks_v1.json | picks_v2.json |
| 운영 | 병행 (통합 매매) | 병행 (통합 매매, 주력) |

V2 미구현(추후): 밸류업 공시, 외국인 수급 추세, 선행 EPS, 거래대금 급증, 주도 업종 가중치

**통합 매매 (run_trade.py):**
- `CROSS_BONUS = 10`: 두 전략 공통 종목 보너스
- `adjusted_score = max(v1_health, v2_health) + (CROSS_BONUS if 공통 else 0)`
- 로그: `[V1+V2] ★` / `[V2]` / `[V1]`

**뉴스 감성 가중치:**
- `SENTIMENT_BONUS = 10` (각 screen 파일 상단)
- 건강검진 직후 적용: 호재 +10, 악재 -10, 혼조 0 (0~100 clamp)

### 뉴스 감성 분석 (`ai_summary.py` → `analyze_news_sentiment()`)

`run_news.py` 실행 시 테마 분석 직후 호출.
헤드라인 목록 + 관련 종목코드 목록을 Gemini에 넘겨 (헤드라인, 종목) 쌍별로 판단.

- 반환: `[{"headline", "stock_code", "corp_name", "label", "reason"}]`
- label: `"호재"` / `"악재"` / `"혼조"`
- 관련 없는 헤드라인은 건너뜀 (Gemini가 판단)
- DB `news_sentiment` 테이블에 날짜 기준 전체 교체(delete→insert)
- `run_screen.py`에서 `sentiment_map = {stock_code: [records]}` 형태로 로드
- 텔레그램 섹션1: 종목명 옆 `✅/❌/⚠️` 이모지 (호재+악재 혼재 → 혼조)
- 텔레그램 섹션3: `[뉴스 감성] ✅ 호재 / • 근거한줄` 블록 추가

**Supabase `news_sentiment` 테이블:**
```sql
CREATE TABLE news_sentiment (
  id          BIGSERIAL PRIMARY KEY,
  crawled_at  DATE        NOT NULL,
  headline    TEXT        NOT NULL,
  stock_code  TEXT        NOT NULL,
  corp_name   TEXT,
  label       TEXT        NOT NULL CHECK (label IN ('호재', '악재', '혼조')),
  reason      TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON news_sentiment (crawled_at, stock_code);
```

### 건강검진 — 7개 지표 점수화 (`health_check.py`)

3단계 통과 종목에 대해 절대 기준으로 0~100점 산출. DB `company_health` 테이블에 7일 캐시.

```python
_METRIC_CONFIG = {
    "per":        (10, 10.0,  50.0,  False),  # (만점, 최적, 최악, 높을수록_좋음)
    "pbr":        (20,  1.0,   5.0,  False),
    "roe":        (10, 30.0,   0.0,  True),
    "roic":       (10, 20.0,   0.0,  True),
    "op_margin":  (10, 30.0,   0.0,  True),
    "debt_ratio": (20, 30.0, 200.0,  False),
    "div_yield":  (20,  4.0,   0.0,  True),
}
```

- KIS `get_stock_valuation()`으로 PER/PBR/배당수익률 조회
- ROIC = NOPAT(영업이익×0.78) / (총자산 - 유동부채)
- `select_picks`가 health_score 내림차순 정렬 → PEG 오름차순 fallback

### 5단계 — RSI 매매 (`PortfolioManager.rebalance`)

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

> ⚠️ **DART API 할당량 초과 시 IP 차단**: 단순 rate-limit이 아니라 IP 자체를 차단한다.
> 로컬에서 `screen_all(workers=32)` 반복 실행 시 빠르게 차단될 수 있음.
> - 로컬 테스트 시 `workers=4` 이하로 제한하고, 동일 스크립트를 연속 실행하지 않는다.
> - 실제 스크리닝은 GitHub Actions(1일 1회)에서만 실행하는 것을 원칙으로 한다.
> - 차단 해제는 DART 고객센터 문의 또는 자연 해제 대기 필요.

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
- `select_picks`: health_score 내림차순 우선, 없으면 PEG 오름차순. NaN PEG 제외
- 익절/손절 조건 추가: +15% 익절(`TAKE_PROFIT=0.15`), -7% 손절(`STOP_LOSS=0.07`)

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
| `GEMINI_API_KEY` | Google Gemini API 키 (종목 요약, 시장 테마 분석) |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 수신 채팅 ID |
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_KEY` | Supabase Secret 키 |

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
exchange-calendars>=4.5
google-generativeai>=0.8.0
supabase>=2.3.0
beautifulsoup4>=4.12.0
```

---

## GitHub Actions

| 워크플로우 | cron (UTC) | KST | 실행 스크립트 | 커밋 파일 |
|-----------|------------|-----|--------------|---------|
| news.yml      | `25 22 * * 0-4` | 평일 07:25 | `run_news.py`      | `news.json` |
| screen.yml    | `30 22 * * 0-4` | 평일 07:30 | `run_screen.py`    | `picks.json` |
| screen_v2.yml | `30 22 * * 0-4` | 평일 07:30 | `run_screen_v2.py` | `picks_v2.json` |
| trade.yml     | `0 0-6 * * 1-5` | 평일 09:00~15:00 | `run_trade.py` | 없음 |

screen.yml·screen_v2.yml은 같은 시각에 독립 실행 (병렬 Actions job).
추후 A 방식(통합)으로 전환 시 `run_screen.py`에서 두 전략 결과를 병합.
