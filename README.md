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

## 원청 레이더 (leadradar)

신규 원청(고객사) 후보를 실시간으로 평가하는 도구입니다. 후보 회사 데이터를
LLM(Claude)에게 매번 넣어서 그 자리에서 적합도를 추론하는 방식이라, 별도로
모델을 학습시킬 필요 없이 후보가 추가될 때마다 바로 점수를 매길 수 있습니다.
또한 설정에 등록한 기존 원청과 사업영역이 겹치는 후보는 `conflicts_with_excluded_client`
플래그로 표시해 관계상 문제가 될 만한 후보를 걸러냅니다.

### 설정

```bash
cp leadradar/config.example.yaml leadradar/config.yaml
# leadradar/config.yaml 을 열어 own_company / excluded_client 를 실제 값으로 채우기
export ANTHROPIC_API_KEY="..."
```

### 실행 (샘플 데이터)

```bash
python -m leadradar.cli \
  --config leadradar/config.yaml \
  --candidates leadradar/fixtures/sample_candidates.json \
  --out candidates_scored.csv
```

`leadradar/fixtures/sample_candidates.json` 은 테스트용 샘플입니다.

### DART 공개데이터에서 후보 자동 발굴

전체 기업(10만 개 이상)을 다 LLM으로 채점하면 비용/시간이 너무 크기 때문에,
회사명 키워드로 먼저 후보를 좁히고 그 후보들만 재무정보를 붙여서 내보내는
단계를 거칩니다. `DART_API_KEY` 환경변수(https://opendart.fss.or.kr 에서 발급)가
필요합니다.

```bash
export DART_API_KEY="..."
python -m leadradar.discover_cli --keywords 반도체 자동화 제어반 검사장비 --out discovered.json
python -m leadradar.cli --config leadradar/config.yaml --candidates discovered.json --out candidates_scored.csv
```

첫 실행 시 전체 기업 고유번호 목록을 내려받아 `.dart_corpcodes_cache.json` 에
캐싱해두고 재사용합니다(자주 안 바뀌는 데이터라 매번 새로 받지 않음). 회사명에
키워드가 포함되면 상장/비상장 상관없이 후보로 포함하며(비상장사는 재무제표
조회가 실패할 수 있어 그 경우 `revenue_growth_pct`가 비어있습니다), 키워드는
원하는 대로 늘리거나 좁힐 수 있습니다. 키워드가 좁으면 후보가 너무 적게
나올 수 있으니(회사명에 그 글자가 그대로 들어간 경우만 잡힘) 관련 업종
용어를 넉넉히 넣는 걸 권장합니다.

`KAKAO_API_KEY`(https://developers.kakao.com 에서 발급) 환경변수를 추가로
설정하면, 회사 주소를 위경도로 변환해 `--origin`(기본값: 안성) 기준 직선거리
(km)도 함께 채웁니다. 키가 없으면 거리 계산은 건너뜁니다.

```bash
export KAKAO_API_KEY="..."
python -m leadradar.discover_cli --keywords 반도체 자동화 제어반 --origin "경기도 안성시" --out discovered.json
```

나라장터 입찰공고(`leadradar/sources/g2b.py`, `G2B_API_KEY` 필요)는 별도
후보 소스로 아직 discover_cli에는 연결되어 있지 않고, 클라이언트 함수만
제공합니다.

### 구조

- `leadradar/config.py` — 우리 회사 프로필 + 사업영역이 겹치면 안 되는 기존 원청 설정
- `leadradar/models.py` — 후보 회사(`Candidate`), 채점 결과(`ScoredCandidate`)
- `leadradar/scoring.py` — Claude에게 후보를 실시간으로 채점시키는 핵심 로직
- `leadradar/pipeline.py` — 후보 로딩 → 채점 → 정렬 → CSV 출력
- `leadradar/discovery.py` — DART 공개데이터에서 키워드로 후보를 좁혀 `Candidate` 리스트로 만듦
- `leadradar/sources/dart.py`, `leadradar/sources/g2b.py` — 공개 데이터 수집 클라이언트
- `leadradar/sources/geocode.py` — 카카오 로컬 API로 주소 → 위경도 변환, 두 좌표 간 거리(km) 계산
- `leadradar/cli.py` — 채점 커맨드라인 진입점
- `leadradar/discover_cli.py` — DART 후보 발굴 커맨드라인 진입점
