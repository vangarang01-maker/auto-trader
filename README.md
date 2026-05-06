# auto-trader

DART 전자공시 + KIS Open API 기반 자동 매매 시스템.
피터 린치 펀더멘털 스크리닝 → PEG 필터 → RSI 매매 신호의 3단계 파이프라인으로 동작한다.

---

## 매매 전략

### 전체 흐름

```
[07:30 KST] DART 재무 스크리닝 → KIS PEG 필터 → 후보 5종목 저장 (picks.json)
[09:00~15:00 KST, 매 시간] picks.json 읽기 → 현재가 재조회 → RSI 계산 → 매수/매도 실행
```

### 매수 조건
- 후보 종목에 포함되어 있고
- RSI-14 < 35 (과매도 구간 진입)

### 매도 조건
- 후보 종목에서 제외되었거나 (스크리닝 탈락)
- RSI-14 ≥ 75 (과매수 구간)

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

### 3단계 — 상위 5개 선정

PEG 오름차순 정렬 후 상위 5개 선정.  
PEG가 낮을수록 성장 대비 주가가 저렴한 종목.

---

## 향후 스크리닝 전략 (예정)

### KOSPI 지수 초과 수익 전략

현재 전략은 절대적 재무 기준으로만 필터링한다. 향후에는 KOSPI 지수 대비 상대 수익률 관점의 조건을 추가할 예정이다.

| 항목 | 내용 |
|------|------|
| 모멘텀 필터 | 최근 3~6개월 수익률이 KOSPI 수익률을 초과한 종목 우선 |
| ROE 기준 강화 | ROE ≥ 15% (자기자본 효율성) |
| PBR 조건 추가 | PBR ≤ 1.5 (자산 대비 저평가) |
| 섹터 분산 | 동일 섹터 최대 2종목으로 집중 리스크 제한 |
| 백테스팅 | KOSPI 지수 대비 초과 수익 검증 후 전략 편입 |

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
│   ├── screening/
│   │   └── fundamental.py    # DART 재무 스크리닝 + PEG 필터
│   ├── broker/
│   │   └── kis_client.py     # KIS API 클라이언트 (시세·잔고·주문)
│   ├── portfolio/
│   │   └── manager.py        # 종목 선정, RSI 계산, 매매 실행
│   └── indicators/
│       └── rsi.py            # RSI-14 계산 (Wilder 방식)
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

---

## 기술 스택

- **언어**: Python 3.11+
- **데이터**: DART Open API, KIS Open API
- **라이브러리**: pandas, requests, opendartreader, python-dotenv
- **자동화**: GitHub Actions (cron)
