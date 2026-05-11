# auto-trader

DART 전자공시 + KIS Open API 기반 자동 매매 시스템.
뉴스 크롤링 → 피터 린치 재무 스크리닝 → PEG 필터 → KOSPI 초과성과 필터 → 기업 건강검진 점수 순으로 후보 5종목을 선정하고, RSI 신호로 매매한다.

---

## 전체 흐름

```
[07:25 KST] run_news.py
  네이버 금융 + 한국경제 헤드라인 크롤링
  → Gemini: 오늘의 주도 섹터/테마 분석
  → DB(market_news) 저장

[07:30 KST] run_screen.py
  0단계  DB에서 뉴스 로드 (없으면 live 크롤링 fallback)
  1단계  DART 재무 스크리닝 (피터 린치)          KOSPI 전체 → ~30개
  2단계  KIS PEG 필터                            → ~25개
  3단계  KOSPI 초과성과 필터                      → ~10개
  건강검진  7개 지표 점수 산출 (DB 7일 캐시)
  최종  건강검진 점수 상위 5개 → picks.json + 텔레그램 알림

[09:00~15:00 KST, 매 시간] run_trade.py
  picks.json 읽기 → 현재가 재조회 → RSI-14 계산 → 매수/매도
```

---

## 종목 선정 전략

### 1단계 — DART 재무 스크리닝 (피터 린치 기준)

KOSPI 전 종목의 직전 사업보고서를 기준으로 아래 5개 조건을 모두 통과해야 한다.

| 지표 | 조건 | 의미 |
|------|------|------|
| 당기순이익 | > 0 | 흑자 기업 |
| 순이익 성장률 | ≥ 20% | 고성장 |
| 매출 성장률 | ≥ 10% | 외형 성장 동반 |
| 부채비율 | ≤ 50% | 재무 건전성 |
| 영업현금흐름 | > 0 | 이익의 실질성 (데이터 없으면 통과) |

> 사업보고서 기준 연도: 4월 이후는 전년도, 4월 이전은 전전년도 (공시 일정 반영)

### 2단계 — KIS PEG 필터

```
PEG = PER(KIS 실시간) / 순이익성장률(%)
```

- PEG ≤ 1.0만 통과 (성장성 대비 저평가)
- KIS 조회 실패 시 통과 처리

### 3단계 — KOSPI 초과성과 필터

최근 3개월(약 60 거래일) 기준으로 KOSPI 대비 우위에 있는 종목만 통과한다.

```
상승포착률 = mean(종목 등락률 | KOSPI 상승일) / mean(KOSPI 등락률 | KOSPI 상승일) × 100
하락포착률 = mean(종목 등락률 | KOSPI 하락일) / mean(KOSPI 등락률 | KOSPI 하락일) × 100
```

통과 조건: **상승포착률 > 하락포착률**
(예: 상승포착 130%, 하락포착 80% → KOSPI 오를 때 1.3배, 내릴 때 0.8배)

### 건강검진 — 7개 지표 점수화 (0~100점)

3단계 통과 종목(~10개)에 대해 7개 재무 지표를 절대 기준으로 점수화해 상위 5개를 선정한다.
점수는 Supabase `company_health` 테이블에 7일간 캐시된다.

| 구분 | 지표 | 만점 | 최적 기준 |
|------|------|:----:|---------|
| 가치 | PER | 10 | ≤ 10배 |
| 가치 | PBR | 20 | ≤ 1.0배 |
| 성장 | ROE | 10 | ≥ 30% |
| 성장 | ROIC | 10 | ≥ 20% |
| 성장 | 영업이익률 | 10 | ≥ 30% |
| 건전성 | 부채비율 | 20 | ≤ 30% |
| 건전성 | 배당수익률 | 20 | ≥ 4% |

---

## 매매 전략 (RSI-14)

### 매수 조건
- 후보 종목에 포함되어 있고
- RSI-14 < 35 (과매도 구간)

### 매도 조건
- 후보 종목에서 제외되었거나
- RSI-14 ≥ 75 (과매수 구간), 또는
- 수익률 +15% 이상 (익절), 또는
- 수익률 -7% 이하 (손절)

### 포지션 관리
- 최대 보유: 5종목
- 종목당 투자금: 총 1,000만 원 ÷ 5 = 200만 원 균등 배분
- 주문 방식: 시장가

---

## 텔레그램 알림

스크리닝 완료 후 아래 내용을 텔레그램으로 전송한다.

- 오늘의 시장 테마 (Gemini 분석)
- 선정 종목별 PEG·현재가·건강검진 점수·상승/하락포착률
- 종목별 DART 공시 + 뉴스 기반 Gemini AI 투자포인트·리스크 요약
- 뉴스에서 언급된 종목은 `[뉴스]` 태그 표시

---

## 프로젝트 구조

```
auto-trader/
├── run_news.py                # 07:25 뉴스 크롤링 → DB 저장
├── run_screen.py              # 07:30 스크리닝 → picks.json 저장
├── run_trade.py               # 매 시간 RSI 매매
├── picks.json                 # 당일 선정 종목
├── src/
│   ├── dart/client.py         # DART API 클라이언트
│   ├── screening/
│   │   ├── fundamental.py     # Lynch·PEG·KOSPI 필터
│   │   └── health_check.py    # 건강검진 점수 산출 + DB 캐시
│   ├── broker/kis_client.py   # KIS API 클라이언트
│   ├── portfolio/manager.py   # 종목 선정·RSI·매매 실행
│   ├── indicators/rsi.py      # RSI-14 (Wilder 방식)
│   ├── notify/
│   │   ├── telegram.py        # 텔레그램 전송
│   │   └── ai_summary.py      # Gemini 종목 요약·시장 테마 분석
│   ├── db/client.py           # Supabase 클라이언트
│   └── news/
│       ├── crawler.py         # 종목별 뉴스 크롤러
│       └── market_news.py     # 시장 뉴스 크롤러 (네이버·한국경제)
└── .github/workflows/
    ├── news.yml               # 평일 07:25 KST
    ├── screen.yml             # 평일 07:30 KST
    └── trade.yml              # 평일 09:00~15:00 KST (매 시간)
```

---

## 실전 전환 방법

현재 KIS 모의투자 모드로 동작한다. 실전 전환 시 아래 2곳을 수정한다.

**`src/portfolio/manager.py`**
```python
self.kis = KISClient(virtual=False)
```

**`src/screening/fundamental.py`**
```python
virtual = False
```

GitHub Secrets의 `KIS_ACCOUNT`를 실계좌번호로 교체하고, KIS HTS에서 실서버 API를 별도 신청한다.

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

## 기술 스택

- **언어**: Python 3.11+
- **데이터**: DART Open API, KIS Open API, FinanceDataReader
- **AI**: Google Gemini (종목 요약, 시장 테마 분석)
- **알림**: Telegram Bot API
- **DB**: Supabase (PostgreSQL) — 뉴스 캐시, 스크리닝 이력, 건강검진 캐시
- **자동화**: GitHub Actions (cron)
