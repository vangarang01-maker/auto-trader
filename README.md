# auto-trader

DART 전자공시 + KIS Open API 기반 자동 매매 시스템.
피터 린치 펀더멘털 스크리닝 → PEG 필터 → KOSPI 초과성과 필터 → RSI 매매 신호의 4단계 파이프라인으로 동작한다.

---

## 매매 전략

### 전체 흐름

```
[07:30 KST] DART 재무 스크리닝 → KIS PEG 필터 → KOSPI 초과성과 필터 → 후보 5종목 저장 (picks.json)
                                                                              ↓ 텔레그램 알림 (Gemini AI 요약 포함)
[09:00~15:00 KST, 매 시간] picks.json 읽기 → 현재가 재조회 → RSI 계산 → 매수/매도 실행
```

### 매수 조건
- 후보 종목에 포함되어 있고
- RSI-14 < 35 (과매도 구간 진입)

### 매도 조건
- 후보 종목에서 제외되었거나 (스크리닝 탈락)
- RSI-14 ≥ 75 (과매수 구간), 또는
- 수익률 +15% 이상 (익절), 또는
- 수익률 -7% 이하 (손절)

### 포지션 관리
- 최대 보유 종목: 5개
- 종목당 투자금액: 총 투자금(1,000만 원) ÷ 5 = 200만 원 균등 배분
- 주문 방식: 시장가

---

## 종목 선정 전략

### 1단계 — DART 재무 스크리닝 (피터 린치 기준)

KOSPI 전 종목의 직전 사업보고서를 기준으로 아래 5개 조건을 모두 통과한 종목만 선별한다.

| 지표 | 조건 | 의미 |
|------|------|------|
| 당기순이익 | > 0 | 흑자 기업 |
| 순이익 성장률 | ≥ 20% | 고성장 |
| 매출 성장률 | ≥ 10% | 외형 성장 동반 |
| 부채비율 | ≤ 50% | 재무 건전성 |
| 영업현금흐름 | > 0 | 이익의 실질성 확인 (데이터 없으면 통과) |

> 사업보고서 기준 연도: 4월 이후는 전년도, 4월 이전은 전전년도 (공시 일정 반영)

### 2단계 — KIS PEG 필터

1단계 통과 종목에 대해 KIS 실시간 시세로 PEG를 계산한다.

```
PEG = PER(현재 시장가 기준) / 순이익성장률(%)
```

- PEG ≤ 1.0인 종목만 통과 (성장성 대비 저평가)
- KIS 조회 실패 시 통과 처리

### 3단계 — KOSPI 초과성과 필터

최근 **3개월(약 60 거래일)** 일봉 기준으로 KOSPI 지수 대비 초과성과 종목만 통과한다.

**계산 방식 — 1일 단위 등락률 비교:**

60 거래일을 두 그룹으로 분류한 뒤, 각 그룹에서 평균 등락률 비율을 계산한다.

```
상승포착률 = mean(종목 일별 등락률 | KOSPI 상승일) / mean(KOSPI 일별 등락률 | KOSPI 상승일) × 100
하락포착률 = mean(종목 일별 등락률 | KOSPI 하락일) / mean(KOSPI 일별 등락률 | KOSPI 하락일) × 100
```

예) 상승포착 130%, 하락포착 80% → KOSPI 오를 때 1.3배 따라 오르고, 내릴 때는 0.8배만 따라 내림.

통과 조건: **상승포착률 > 하락포착률**

### 4단계 — 최종 5개 선정

PEG 오름차순 정렬 후 상위 5개 선정.
PEG가 낮을수록 성장 대비 주가가 저렴한 종목.

---

## 텔레그램 알림 & AI 요약

스크리닝 완료 후 텔레그램으로 후보 종목 리포트를 전송한다.  
각 종목마다 DART 공시 + 네이버 금융 뉴스를 수집해 Gemini AI가 투자포인트와 리스크를 요약한다.

- Gemini 모델: `gemini-3.1-flash-lite` 우선, 실패 시 `gemini-3-flash-preview` 폴백
- 뉴스는 Supabase DB에 저장해 중복 크롤링을 방지한다 (7일 내 5건 이상이면 재크롤링 생략)

---

## 실전 전환 방법

현재는 KIS 모의투자(virtual) 모드로 동작한다. 실전으로 전환 시 아래 2곳을 수정한다.

**`src/portfolio/manager.py`**
```python
# 변경 전
self.kis = KISClient(virtual=True)
# 변경 후
self.kis = KISClient(virtual=False)
```

**`src/screening/fundamental.py`**
```python
# 변경 전
virtual = not os.getenv("KIS_APP_KEY")
# 변경 후
virtual = False
```

**GitHub Secrets 업데이트**
- `KIS_ACCOUNT` → 실계좌번호로 교체

> KIS 실서버 API는 HTS에서 별도 신청 필요

---

## 프로젝트 구조

```
auto-trader/
├── run_screen.py              # 07:30 1회 실행 — 종목 스크리닝 → picks.json 저장
├── run_trade.py               # 매 시간 실행 — RSI 신호 기반 매매
├── picks.json                 # 당일 선정 종목 (run_screen이 갱신)
├── portfolio.json             # 보유 종목 상태
├── src/
│   ├── dart/
│   │   └── client.py         # DART API 클라이언트 (재무제표, 공시, 기업 컨텍스트)
│   ├── screening/
│   │   └── fundamental.py    # DART 재무 스크리닝 + PEG 필터 + KOSPI 초과성과 필터
│   ├── broker/
│   │   └── kis_client.py     # KIS API 클라이언트 (시세·잔고·주문)
│   ├── portfolio/
│   │   └── manager.py        # 종목 선정, RSI 계산, 매매 실행 (익절/손절 포함)
│   ├── indicators/
│   │   └── rsi.py            # RSI-14 계산 (Wilder 방식)
│   ├── notify/
│   │   ├── telegram.py       # 텔레그램 메시지 전송
│   │   └── ai_summary.py     # Gemini AI 종목 요약
│   ├── db/
│   │   └── client.py         # Supabase 클라이언트 (뉴스·스크리닝 결과 저장)
│   └── news/
│       └── crawler.py        # 네이버 금융 뉴스 크롤러
└── .github/workflows/
    ├── screen.yml            # 평일 07:30 KST 스크리닝
    └── trade.yml             # 평일 09:00~15:00 KST 매 시간 매매
```

---

## 환경 변수

`.env` 또는 GitHub Secrets에 설정:

| 변수명 | 설명 |
|--------|------|
| `DART_API_KEY` | DART 전자공시 API 키 |
| `KIS_APP_KEY` | KIS 실서버 앱키 (시세 조회용) |
| `KIS_APP_SECRET` | KIS 실서버 시크릿 |
| `KIS_VIRTUAL_APP_KEY` | KIS 모의투자 앱키 |
| `KIS_VIRTUAL_APP_SECRET` | KIS 모의투자 시크릿 |
| `KIS_ACCOUNT` | 계좌번호 (모의: 모의계좌, 실전: 실계좌) |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 수신 채팅 ID |
| `GEMINI_API_KEY` | Google Gemini API 키 |
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_KEY` | Supabase Secret 키 |

---

## 기술 스택

- **언어**: Python 3.11+
- **데이터**: DART Open API, KIS Open API, FinanceDataReader
- **AI**: Google Gemini (종목 요약)
- **알림**: Telegram Bot API
- **DB**: Supabase (PostgreSQL) — 뉴스 캐시, 스크리닝 이력
- **라이브러리**: pandas, requests, opendartreader, python-dotenv, exchange-calendars, beautifulsoup4
- **자동화**: GitHub Actions (cron)
