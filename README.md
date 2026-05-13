# auto-trader

DART 전자공시 + KIS Open API 기반 자동 매매 시스템.  
3가지 독립 전략(V1·V2·V3)이 병렬로 종목을 선정하고, RSI 신호로 매매한다.

---

## 전체 흐름

```
[07:25 KST] run_news.py
  네이버 금융 + 한국경제 헤드라인 크롤링
  KOSPI 시총 상위 100 + 당일 언급 종목 뉴스 수집 (Google News RSS)
  → Gemini: 시장 테마 분석 + 종목별 감성 분석(호재/악재/혼조)
  → DB(market_news, news, news_sentiment) 저장

[07:30 KST] run_screen.py   ← V1 피터 린치
  1단계  DART 재무 스크리닝 (피터 린치 5조건)   KOSPI 전체 → ~30개
  2단계  KIS PEG 필터 (PEG ≤ 1.0)              → ~25개
  3단계  KOSPI 초과성과 필터                     → ~10개
  건강검진 + 뉴스 감성 가중치 → 상위 5개 → picks_v1.json

[07:30 KST] run_screen_v2.py   ← V2 고배당-저PBR-고ROE  (V1과 병렬 실행)
  1단계  DART 재무 필터 (ROE≥10% / 부채비율≤150% / 이자보상배율≥3)
  2단계  네이버 금융 배당수익률 + KIS PBR 필터 (PBR 0.3~1.2 / 배당≥2.5%)
  3단계  6개월 모멘텀 상위 20%
  건강검진 + 뉴스 감성 가중치 → 상위 5개 → picks_v2.json

[07:35 KST] run_screen_v3.py   ← V3 섹터 주도주
  0단계  네이버 금융 업종 시세 → 당일 상승 상위 3개 섹터 선정
  1단계  해당 섹터 종목 코드 수집 (네이버 업종 상세)
  2단계  종목별 5일 수익률·거래량 배율 병렬 계산
  3단계  복합 점수 (모멘텀 35% + 거래량 25% + 건강검진 30% + 뉴스감성 10%)
  → 상위 5개 → picks_v3.json

[09:00~15:00 KST, 매 시간] run_trade.py
  picks_v1.json + picks_v2.json 로드
  공통 종목 → CROSS_BONUS +10점
  adjusted_score 상위 5개 → RSI-14 매매
```

---

## 전략별 설명

### V1 — 피터 린치 (`run_screen.py`)

#### 1단계: DART 재무 스크리닝

| 지표 | 조건 | 의미 |
|------|------|------|
| 당기순이익 | > 0 | 흑자 |
| 순이익 성장률 | ≥ 20% | 고성장 |
| 매출 성장률 | ≥ 10% | 외형 성장 동반 |
| 부채비율 | ≤ 50% | 재무 건전성 |
| 영업현금흐름 | > 0 | 이익의 실질성 |

#### 2단계: KIS PEG 필터

```
PEG = PER(KIS 실시간) / 순이익성장률(%)   →   PEG ≤ 1.0 통과
```

#### 3단계: KOSPI 초과성과 필터

최근 3개월(약 60 거래일) 기준으로 KOSPI 대비 우위 종목만 통과.

```
상승포착률 = mean(종목 등락률 | KOSPI 상승일) / mean(KOSPI 등락률 | KOSPI 상승일) × 100
하락포착률 = mean(종목 등락률 | KOSPI 하락일) / mean(KOSPI 등락률 | KOSPI 하락일) × 100
통과: 상승포착률 > 하락포착률
```

---

### V2 — 고배당-저PBR-고ROE (`run_screen_v2.py`)

#### 1단계: DART 재무 필터

| 지표 | 조건 |
|------|------|
| 당기순이익 | > 0 |
| ROE | ≥ 10% |
| 부채비율 | ≤ 150% |
| 이자보상배율 | ≥ 3 (데이터 있을 때만) |

#### 2단계: 배당수익률 + PBR 필터

- 배당수익률: **네이버 금융** per-stock 스크래핑 (로그인 불필요, workers=5)
- PBR: KIS 실시간 조회
- 통과 조건: **PBR 0.3~1.2** / **배당수익률 ≥ 2.5%** (데이터 없으면 통과)

#### 3단계: 6개월 모멘텀 필터

최근 6개월 절대 수익률 상위 20% (최소 5개 보장)

---

### V3 — 섹터 주도주 (`run_screen_v3.py`)

오늘의 시장 주도 섹터를 먼저 선별하고, 그 안에서 모멘텀·거래량이 강한 주도주를 찾는다.

#### 0단계: 주도 섹터 선정

- 네이버 금융 업종 시세(`sise_group.naver?type=upjong`)에서 당일 등락률 상위 3개 섹터 선정
- DART 호출 없음 → IP 차단 위험 없음

#### 1단계: 섹터 종목 수집

- 네이버 금융 업종 상세(`sise_group_detail.naver`)에서 종목 코드 수집
- 시총 3,000억 미만 제외

#### 2단계: 가격·거래량 계산

- FDR으로 최근 25일 종가·거래량 병렬 조회 (workers=8, ~1분 30초)
- 5일 수익률, 20일 평균 대비 거래량 배율 계산

#### 3단계: 복합 점수

```
momentum_score  = 5일수익률 백분위 × 100          (35%)
volume_score    = min(거래량배율 / 5 × 100, 100)  (25%)
health_score    = 건강검진 DB 캐시 (없으면 50점)   (30%)
news_bonus      = +15(호재) / -10(악재) / 0       (10%)

total = momentum×0.35 + volume×0.25 + health×0.30 + news×0.10
```

---

### 공통: 건강검진 — 7개 지표 점수화 (0~100점)

V1·V2·V3 공통으로 최종 종목 선정 시 사용. Supabase `company_health` 테이블에 7일간 캐시.

| 지표 | 만점 | 최적 기준 |
|------|:----:|---------|
| PER | 10 | ≤ 10배 |
| PBR | 20 | ≤ 1.0배 |
| ROE | 10 | ≥ 30% |
| ROIC | 10 | ≥ 20% |
| 영업이익률 | 10 | ≥ 30% |
| 부채비율 | 20 | ≤ 30% |
| 배당수익률 | 20 | ≥ 4% |

**뉴스 감성 가중치**: 호재 +10점 / 악재 -10점 (0~100 clamp)

---

### 통합 매매 (V1 + V2)

```python
CROSS_BONUS = 10   # 두 전략 공통 종목 보너스
adjusted_score = max(v1_health, v2_health) + (CROSS_BONUS if 공통 else 0)
```

adjusted_score 내림차순 상위 5개 → RSI-14 매매

---

## 매매 전략 (RSI-14)

| 구분 | 조건 |
|------|------|
| 매수 | picks에 포함 AND RSI < 35 |
| 매도 | picks에서 제외, 또는 RSI ≥ 75, 또는 수익률 +15%(익절), 또는 -7%(손절) |
| 포지션 | 최대 5종목 / 종목당 200만 원 균등 배분 / 시장가 주문 |

---

## 프로젝트 구조

```
auto-trader/
├── run_news.py                # 07:25 뉴스 크롤링 + 감성 분석 → DB 저장
├── run_screen.py              # 07:30 V1 스크리닝 → picks_v1.json
├── run_screen_v2.py           # 07:30 V2 스크리닝 → picks_v2.json
├── run_screen_v3.py           # 07:35 V3 스크리닝 → picks_v3.json
├── run_trade.py               # 09:00~15:00 RSI 매매 (V1+V2 통합)
├── picks_v1.json              # V1 당일 선정 종목
├── picks_v2.json              # V2 당일 선정 종목
├── picks_v3.json              # V3 당일 선정 종목
├── src/
│   ├── dart/client.py         # DART API 클라이언트
│   ├── screening/
│   │   ├── fundamental.py     # V1: Lynch·PEG·KOSPI 필터
│   │   ├── strategy_v2.py     # V2: ROE·PBR·배당·모멘텀 필터
│   │   ├── health_check.py    # 건강검진 점수 산출 + DB 캐시
│   │   └── sector_momentum.py # 섹터 모멘텀 유틸
│   ├── broker/kis_client.py   # KIS API 클라이언트
│   ├── portfolio/manager.py   # 종목 선정·RSI·매매 실행
│   ├── indicators/
│   │   ├── rsi.py             # RSI-14 (Wilder 방식)
│   │   ├── atr.py             # ATR 계산
│   │   ├── foreign_flow.py    # 외국인 수급
│   │   └── market_regime.py   # 시장 국면 판단
│   ├── notify/
│   │   ├── telegram.py        # 텔레그램 전송
│   │   └── ai_summary.py      # Gemini 종목 요약·감성 분석
│   ├── db/client.py           # Supabase 클라이언트
│   └── news/
│       ├── crawler.py         # 종목별 뉴스 (Google News RSS, 24시간 필터)
│       └── market_news.py     # 시장 뉴스 (네이버 금융 + 한국경제)
└── .github/workflows/
    ├── news.yml               # 평일 07:25 KST
    ├── screen.yml             # 평일 07:30 KST (V1)
    ├── screen_v2.yml          # 평일 07:30 KST (V2, 병렬)
    ├── screen_v3.yml          # 평일 07:35 KST (V3)
    └── trade.yml              # 평일 09:00~15:00 KST (매 시간)
```

---

## 환경 변수

| 변수명 | 설명 |
|--------|------|
| `DART_API_KEY` | DART 전자공시 API 키 |
| `KIS_APP_KEY` | KIS 실서버 앱키 |
| `KIS_APP_SECRET` | KIS 실서버 시크릿 |
| `KIS_VIRTUAL_APP_KEY` | KIS 모의투자 앱키 |
| `KIS_VIRTUAL_APP_SECRET` | KIS 모의투자 시크릿 |
| `KIS_ACCOUNT` | 계좌번호 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 수신 채팅 ID |
| `GEMINI_API_KEY` | Google Gemini API 키 |
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_KEY` | Supabase Secret 키 |

---

## 실전 전환 방법

현재 KIS 모의투자 모드로 동작한다. 실전 전환 시:

1. `src/portfolio/manager.py` → `KISClient(virtual=False)`
2. `src/screening/fundamental.py` → `virtual = False`
3. GitHub Secrets의 `KIS_ACCOUNT`를 실계좌번호로 교체
4. KIS HTS에서 실서버 API 별도 신청

---

## 기술 스택

| 구분 | 기술 |
|------|------|
| 언어 | Python 3.11+ |
| 데이터 | DART Open API, KIS Open API, FinanceDataReader, 네이버 금융 |
| AI | Google Gemini (종목 요약, 시장 테마·감성 분석) |
| 알림 | Telegram Bot API |
| DB | Supabase (PostgreSQL) |
| 자동화 | GitHub Actions (cron) |
