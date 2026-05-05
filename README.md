# auto-trader

DART 전자공시 API 기반 주식 종목 스크리닝 시스템. 재무제표 데이터를 활용해 펀더멘털 조건에 맞는 종목을 필터링합니다.

## 기능

- DART API를 통한 기업 재무제표 조회 (사업보고서 / 반기 / 분기)
- 영업이익률, 부채비율 기준 종목 스크리닝
- KIS API 연동 예정 (실제 매매 실행)

## 기술 스택

- Python 3.14
- [opendartreader](https://github.com/FinanceData/OpenDartReader) — DART Open API 클라이언트
- pandas — 재무 데이터 처리

## 시작하기

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

```bash
cp .env.example .env
```

`.env`에 DART API 키 입력 (발급: https://opendart.fss.or.kr)

```
DART_API_KEY=your_api_key_here
```

### 3. 실행

```bash
python main.py
```

## 프로젝트 구조

```
auto-trader/
├── main.py                  # 진입점
├── src/
│   ├── dart/
│   │   └── client.py        # DART API 래퍼
│   ├── screening/
│   │   └── fundamental.py   # 펀더멘털 스크리너
│   └── broker/              # KIS API 연동 예정
└── tests/
```

## 스크리닝 조건 (기본값)

| 지표 | 조건 |
|------|------|
| 영업이익률 | 10% 이상 |
| 부채비율 | 200% 이하 |

`src/screening/fundamental.py`의 `screen()` 파라미터로 조건 변경 가능.

## 로드맵

- [x] DART API 연동 및 재무제표 조회
- [x] 펀더멘털 스크리닝 (영업이익률 / 부채비율)
- [ ] KIS API 연동 (시세 조회 / 매매 실행)
- [ ] 스크리닝 전략 다양화 (PER, PBR, ROE 등)
- [ ] 종목 알림 기능
