# 수학 채점 & 해설 시스템

수학 문제(초중고 수준의 산술식/방정식부터 대학 수학인 미적분학, 선형대수,
미분방정식, 이산수학/조합론까지)를 입력하면 정답을 계산하고, 학생 답안을
채점하며, 핵심 단계 위주의 풀이 과정(해설)을 생성해 주는 시스템입니다.
`sympy` 기반 기호 계산으로 동작하며, 외부 API 없이 자체적으로 채점/해설을 만듭니다.

## 지원하는 문제 유형

### 기초 (등호 포함 자유 입력)

| 유형 | 예시 |
| --- | --- |
| 산술식 | `3 + 4 * 2 - 1` |
| 일차방정식 | `2x + 3 = 11`, `3x + 2 = x + 10` |
| 이차방정식 | `x^2 - 5x + 6 = 0` |
| 식 정리(전개/동류항) | `3x + 5 - x + 2`, `2*(x + 3)` |

학생 답안은 `4`, `x = 4`, `x=2 또는 x=3`, `2, 3` 처럼 자유롭게 입력할 수 있습니다.

### 대학 수학 (`키워드: 인자, 인자` 형식)

기초 유형과 구분하기 위해, 고급 주제는 맨 앞에 키워드와 콜론(`:`)을 붙여 입력합니다.
인자는 콤마(`,`)로 구분하며, 변수를 생략하면 식에 포함된 유일한 변수를 자동으로 사용합니다.

| 분야 | 키워드 | 형식 | 예시 |
| --- | --- | --- | --- |
| 미적분학 | `미분` | `식, 변수[, 차수]` | `미분: x^3 + 2*x, x` |
| | `적분` | `식, 변수[, 하한, 상한]` | `적분: x^2, x, 0, 1` |
| | `극한` | `식, 변수, 극한값` | `극한: sin(x)/x, x, 0` |
| | `테일러` | `식, 변수, 중심점, 차수` | `테일러: sin(x), x, 0, 5` |
| | `급수합` | `식, 변수, 시작, 끝` | `급수합: 1/n^2, n, 1, oo` |
| 선형대수 | `행렬식` | `[[..],[..]]` | `행렬식: [[1,2],[3,4]]` |
| | `역행렬` | `[[..],[..]]` | `역행렬: [[1,2],[3,4]]` |
| | `고유값` | `[[..],[..]]` | `고유값: [[2,0],[0,3]]` |
| | `연립방정식` | `식=식; 식=식` | `연립방정식: x+y=3; 2x-y=0` |
| 미분방정식 | `미분방정식` | `y', y'' 포함 방정식` | `미분방정식: y' + y = 0` |
| 이산수학 | `순열` | `n=.., r=..` | `순열: n=5, r=2` |
| | `조합` | `n=.., r=..` | `조합: n=5, r=2` |

편미분은 별도 키워드 없이 `미분` 에 다변수 식을 넣으면 자동으로 편미분으로 처리됩니다
(예: `미분: x^2*y + y^3, x`). 미분방정식의 학생 답안은 `y = C1*exp(-x)` 처럼 적으면
sympy의 `checkodesol` 로 원래 방정식에 대입해 만족하는지 직접 검증합니다(상수 이름이
달라도 채점 가능).

> 대학 수학 해설은 "핵심 단계 위주"로 제공됩니다 — 적용한 공식/정리(연쇄법칙, 근의
> 공식, 판별식, 코팩터 전개, RREF 등)와 중간 결과를 보여주지만, 산술식처럼 모든
> 사칙연산을 한 줄씩 풀어 쓰지는 않습니다.

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

### 웹으로 실행

```bash
uvicorn web.main:app --reload
```

브라우저에서 `http://localhost:8000` 접속 후 문제와 학생 답안을 입력하면
정답 여부와 단계별 풀이가 표시됩니다.

### API로 사용

```bash
curl -X POST http://localhost:8000/api/grade \
  -H "Content-Type: application/json" \
  -d '{"problem": "2x + 3 = 11", "student_answer": "x = 4"}'

curl -X POST http://localhost:8000/api/grade \
  -H "Content-Type: application/json" \
  -d '{"problem": "적분: x^2, x, 0, 1", "student_answer": "1/3"}'
```

### CLI로 사용

```bash
python -m mathgrader.cli "2x + 3 = 11" "x = 4"
python -m mathgrader.cli "역행렬: [[1,2],[3,4]]" "[[-2,1],[3/2,-1/2]]"
```

## 테스트

```bash
pytest
```

## 구조

- `mathgrader/classify.py` — 문제 유형 분류 (기초 방정식/산술 + `키워드:` 기반 대학 수학 유형)
- `mathgrader/solvers/` — 유형별 단계별 풀이 로직
  - `arithmetic.py`, `linear.py`, `quadratic.py`, `simplify.py`, `generic.py` — 기초
  - `calculus.py` — 미분/적분/극한/테일러급수/급수합
  - `linear_algebra.py` — 행렬식/역행렬/고유값/연립방정식
  - `diffeq.py` — 미분방정식 (분류 + `dsolve` + `checkodesol` 대입 검증)
  - `combinatorics.py` — 순열/조합
- `mathgrader/safe_arith.py` — `eval`/`exec` 없이 숫자 리터럴+사칙연산만 허용하는
  화이트리스트 ast 평가기 (산술식 단계별 계산, 행렬 리터럴 파싱에서 공유)
- `mathgrader/parsing.py` — 수식/답안 문자열 파싱, 답안 비교
- `mathgrader/grader.py` — 분류 → 풀이 → 채점을 총괄하는 진입점
- `web/` — FastAPI 기반 웹 UI 및 API

---

# 그레이엄 지표 기반 주식 스크리너 (`stockscreener/`)

벤저민 그레이엄의 방어적 투자자 기준과 내재가치 공식을 이용해, **애널리스트의
투자의견·목표주가 같은 주관적 지표를 전혀 사용하지 않고** 재무제표 절댓값만으로
저평가 종목을 찾아주는 프로그램입니다. 종목별 최근 5~10개년 재무제표 추세,
그레이엄의 7가지 정량 기준, 그레이엄 넘버·내재가치·안전마진을 계산하고,
증시에 영향을 주는 뉴스를 호출할 때마다 실시간으로 새로 가져옵니다.

## 주요 기능

- **그레이엄 지표**: 그레이엄 넘버(`sqrt(22.5 x EPS x BVPS)`), 유동비율,
  장기부채 대비 순운전자본, PER/PBR, EPS 성장률, 이익 안정성, 배당 기록 등
  `The Intelligent Investor`의 방어적 투자자 7원칙을 자동 평가합니다.
  데이터가 부족한 항목은 "실패"가 아니라 "판단 불가(N/A)"로 별도 표시합니다.
- **5~10개년 재무제표 분석**: 매출/순이익 CAGR, EPS 성장률(시작·종료 구간
  3개년 평균 비교), 적자 연도 수를 계산해 추세의 안정성을 판단합니다.
- **절댓값 기반 내재가치 계산**: 그레이엄의 1962년 개정 공식
  `V = EPS x (8.5 + 2g) x 4.4 / Y` (g=과거 실적으로 추정한 성장률, Y=채권
  수익률)로 내재가치와 안전마진을 계산하며, 애널리스트 추정치는 입력값으로
  사용하지 않습니다.
- **실시간 뉴스**: 구글 뉴스 검색 RSS를 이용해 증시 전반에 영향을 주는 뉴스와
  종목별 뉴스를 실행할 때마다 새로 조회합니다(캐시 없음). 개별 피드가
  실패해도 나머지 결과에는 영향을 주지 않습니다.
- **저평가 종목 목록**: 안전마진(내재가치 대비)과 그레이엄 넘버 대비 주가,
  두 가지 절댓값 신호가 모두 저평가를 가리키는 종목만 안전마진이 높은 순으로
  정렬해 보여줍니다.
- **오류 격리**: 종목 하나의 데이터 조회 실패(상장폐지, 네트워크 오류 등)가
  전체 스크리닝을 중단시키지 않습니다. 실패한 종목은 사유와 함께 목록에서
  제외되고 나머지 종목 분석은 계속 진행됩니다.

## 사용법

```bash
pip install -r requirements.txt

# 기본 예시 종목군으로 실행
python -m stockscreener.cli

# 원하는 종목 지정 (한국 종목은 .KS/.KQ 접미사 사용)
python -m stockscreener.cli --tickers AAPL,MSFT,005930.KS,000660.KS

# 파일에서 티커 목록 읽기 (한 줄에 하나씩, #으로 주석 처리 가능)
python -m stockscreener.cli --tickers-file watchlist.txt

# S&P 500 전체(약 500종목, 레포에 번들된 스냅샷) 스크리닝 — 종목이 많으므로
# 종목별 뉴스 조회는 꺼서 속도를 높이는 것을 권장
python -m stockscreener.cli --sp500 --no-ticker-news --quiet --json sp500_result.json

# 결과를 JSON으로도 저장
python -m stockscreener.cli --tickers AAPL --json result.json

# 안전마진 기준을 더 보수적으로(예: 40%) 조정
python -m stockscreener.cli --min-margin-of-safety 0.4
```

라이브러리로 직접 사용할 수도 있습니다.

```python
from stockscreener.screener import Screener

screener = Screener()
reports = screener.analyze(["AAPL", "MSFT", "005930.KS"])
undervalued = screener.undervalued(reports)
market_news = screener.get_market_news()
```

## S&P 500 전체 스크리닝

`stockscreener/data/universe/sp500.txt` 에 S&P 500 구성종목 티커 스냅샷을
번들해두었다(`stockscreener.universe.load_sp500_tickers()`로 로드). `--sp500`
옵션으로 500종목 전체를 한 번에 스크리닝할 수 있다. 지수 구성종목은 주기적으로
바뀌므로, 최신 목록이 필요하면 해당 파일을 갱신하면 된다. 500종목 조회는
시간이 걸리므로(각 종목마다 시세+재무제표+선택적으로 뉴스 조회) `--no-ticker-news`,
`--quiet` 옵션과 함께 쓰는 것을 권장한다.

## 웹 대시보드 (자동 갱신)

CLI로 매번 실행 → JSON 저장 → 업로드하는 대신, 로컬에서 계속 실행해두면
백그라운드에서 주기적으로 알아서 다시 계산해 화면이 자동 갱신되는 웹 대시보드도
있습니다.

```bash
uvicorn stockscreener.web.main:app --reload
```

브라우저에서 `http://localhost:8000` 을 열어두면 됩니다. 서버가 켜져 있는 동안
백그라운드에서 주기적으로 시세·재무제표·뉴스를 다시 가져와 재계산하고, 페이지는
그 결과를 주기적으로 폴링해서 자동으로 화면을 갱신합니다 (브라우저가 외부
사이트에 직접 접속하는 게 아니라, 이 로컬 서버가 대신 가져와 줍니다). "지금
갱신" 버튼으로 즉시 재계산을 요청할 수도 있습니다.

환경변수로 동작을 조정할 수 있습니다.

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `STOCKSCREENER_TICKERS` | (없음) | 쉼표로 구분한 티커 목록. 지정하면 최우선 적용 |
| `STOCKSCREENER_SP500` | (없음) | `1`로 설정하면 번들된 S&P 500 전체(약 500종목) 사용 |
| `STOCKSCREENER_REFRESH_SECONDS` | `1800` (30분) | 자동 갱신 주기(초). `--sp500` 사용 시 한 주기가 오래 걸리므로 `7200` 이상 권장 |
| `STOCKSCREENER_NO_TICKER_NEWS` | `1` (건너뜀) | `0`으로 설정하면 종목별 뉴스도 매 주기마다 조회 (느려짐) |

예시 (S&P 500 전체를 2시간마다 자동 갱신):

```bash
STOCKSCREENER_SP500=1 STOCKSCREENER_REFRESH_SECONDS=7200 uvicorn stockscreener.web.main:app
```

## 데이터 소스와 한계

- 시세/재무제표는 기본적으로 야후 파이낸스(`yfinance`, 무료, API 키 불필요)를
  사용합니다. 야후 파이낸스 무료 API는 연간 재무제표를 보통 **최근 4~5개년치**
  까지만 제공합니다. 10개년 전체 이력이 꼭 필요하다면
  `stockscreener/data/provider.py`의 `DataProvider` 프로토콜에 맞춰 유료
  데이터 공급자(예: Financial Modeling Prep)를 구현해
  `Screener(data_provider=...)` 에 주입하면 됩니다.
- 뉴스는 구글 뉴스 검색 RSS(`news.google.com/rss/search`)를 사용합니다.
  API 키가 필요 없고 검색어 기반이라 특정 언론사 RSS가 개편되어도 계속
  동작합니다.
- **이 프로그램은 투자 자문이 아니며, 어떤 매수·매도도 권유하지 않습니다.**
  모든 지표는 과거 재무제표 수치로 계산한 참고용 정량 지표입니다.

## 구조

- `stockscreener/models.py` — 재무제표/시세/뉴스/분석 결과 데이터 모델
- `stockscreener/data/` — 시세·재무제표 공급자 (`yfinance_provider.py` 기본
  구현, `provider.py`에 교체 가능한 인터페이스 정의)
- `stockscreener/news/rss_provider.py` — 실시간 뉴스 조회 (캐시 없음, 피드별
  오류 격리)
- `stockscreener/analysis/graham.py` — 그레이엄 넘버, 방어적 투자자 7원칙
- `stockscreener/analysis/financial_trend.py` — 5~10개년 매출/순이익/EPS 추세
- `stockscreener/analysis/valuation.py` — 내재가치, 안전마진, 저평가 판정
- `stockscreener/screener.py` — 데이터 조회 → 분석 → 뉴스를 종목별로 조립하고
  오류를 격리하는 오케스트레이터
- `stockscreener/report.py` — 콘솔 출력 및 JSON 직렬화
- `stockscreener/universe.py`, `stockscreener/data/universe/sp500.txt` — S&P 500
  종목 유니버스 스냅샷과 로더
- `stockscreener/cli.py` — CLI 진입점 (1회 실행 후 JSON 저장)
- `stockscreener/web/main.py` — 백그라운드에서 주기적으로 재계산하는 FastAPI
  웹 대시보드 (자동 갱신)
