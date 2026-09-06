# S&P500 장기 저평가 스크리너

애널리스트 의견(목표주가, 투자의견, 컨센서스 추정치)을 배제하고, 기업이 직접
공시한 재무제표 원본 수치 + 거시경제 지표만으로 "장기적으로 동종업종 대비
우상향할 가능성이 높은 종목"을 스코어링하고, 현재 가격이 그 펀더멘털 대비
얼마나 싼지를 비교해 단계별 분할매수 신호를 만드는 프로젝트입니다.

## 설계 원칙

- **입력에서 가격 제외**: "품질(quality) 점수" 모델은 재무제표+거시지표만 보고
  학습합니다. 현재 가격을 피처로 넣으면 순환논리에 빠지기 때문입니다.
- **애널리스트 데이터 배제**: `data/fundamentals.py`, `data/prices.py`는 목표주가,
  투자의견, 컨센서스 추정치 등 주관이 섞인 필드를 의도적으로 읽지 않습니다.
- **저평가는 동종업종 대비 상대값**: 모든 비율/밸류에이션은 같은 시점, 같은
  GICS 섹터 내에서 z-score/percentile로 계산합니다 (은행과 소프트웨어 기업의
  부채비율을 그대로 비교하는 건 의미가 없으므로).
- **선행편향(look-ahead) 방지**: 라벨(미래 상대수익률)은 항상 `.asof()`로
  해당 시점에 실제로 관측 가능했던 가격만 사용합니다.

## 파이프라인 구조

```
config.py              S&P500 유니버스 (티커, GICS 섹터)
data/fundamentals.py   분기 재무제표 원본 항목 (매출/영업이익/부채 등)
data/macro.py          FRED 거시지표 (금리, 물가, 실업률, 산업생산)
data/prices.py         가격/거래량 + P/E, P/B 계산용 EPS·BPS
data/filings.py        SEC EDGAR 10-K/10-Q 원문 (Risk Factors, MD&A) 조회
features.py            비율/성장률 계산 → 섹터 내 z-score → 거시지표 병합
text_features.py       (ticker, period)별 시점 기준(point-in-time) 공시 매칭
embeddings.py           공시 텍스트 → 문장 임베딩 → PCA 차원축소
labels.py               미래 N일 상대수익률 → 분위(tier) 라벨
model.py                LightGBM 분류기 (기간 기준 train/test 분리) + 판단 근거 추출
explanations.py         모델 기여도/밸류에이션 percentile → 사람이 읽는 문장
valuation.py            품질점수 + 섹터 내 저평가 percentile → 분할매수 티어
                        + 시점별(point-in-time) 과거 P/E·P/B 재구성 (RL용)
rl_env.py               분할매수 타이밍을 다루는 강화학습 환경 (상태/행동/보상)
rl_agent.py             테이블 기반 Q-learning 학습/추천
pipeline.py              위 전체를 엮는 오케스트레이션
cli.py                  커맨드라인 실행 진입점
```

## 사용법

```bash
pip install -r stock_valuation/requirements.txt
python -m stock_valuation.cli --limit 30 --start 2015-01-01 --output out.csv
```

- `--limit`: 대상 종목 수 (테스트할 땐 작게, 실전은 500까지, 생략하면 전체)
- 결과 CSV에는 `ticker`, `quality_score`(품질점수), `cheapness_percentile`
  (섹터 내 저평가 순위, 낮을수록 쌈), `buy_tier`(분할매수 단계), `reason`
  (아래 설명) 컬럼이 담깁니다.
- 실행 로그에 각 단계별 행 개수(`fundamentals=`, `dataset=` 등)와 holdout
  accuracy가 찍히니, 결과가 이상하면 그걸로 어느 단계가 문제인지 먼저 확인하세요.

### 판단 근거 (`reason` 컬럼)

각 종목이 왜 그 점수/단계를 받았는지 사람이 읽을 수 있는 문장으로 같이 나옵니다.

- **품질 부분**: LightGBM의 `pred_contrib`(SHAP 방식과 동일한 원리, 별도
  라이브러리 없이 LightGBM 자체 기능)로 이번 예측에 가장 크게 기여한 피처
  top 3를 뽑아서 `ROE(섹터 대비) ↑, 부채비율(섹터 대비) ↓` 처럼 보여줍니다.
  화살표는 그 피처가 품질점수를 올렸는지(↑) 내렸는지(↓)를 의미합니다.
- **저평가 부분**: P/E, P/B가 섹터 내에서 각각 하위 몇 %인지 그대로 풀어씀
  (`model.py`가 아니라 `valuation.py`가 이미 계산해둔 percentile을 문장으로
  바꾸는 것뿐이라 별도 모델이 필요 없습니다).

실제로 200~500개 종목 규모에서 정상 동작 확인했습니다 (holdout accuracy가
3분류 랜덤 기준 0.33보다 유의미하게 높게 나옴).

### 공시 텍스트 임베딩 (선택 기능)

애널리스트 의견 대신, 기업이 직접 낸 10-K/10-Q의 "Risk Factors"·"MD&A" 섹션
원문을 문장 임베딩으로 변환해 재무비율 피처에 추가하는 기능입니다. 무거운
의존성(`sentence-transformers`, `torch`)이 필요해서 기본 설치에는 안
들어있고, 켜고 싶을 때만 별도로 설치합니다.

```bash
pip install -r stock_valuation/requirements-text.txt
python -m stock_valuation.cli --limit 30 --with-text
```

- GPU(CUDA)가 잡혀 있으면 `sentence-transformers`가 자동으로 사용합니다.
- 각 (종목, 분기)는 그 시점에 실제로 공시돼 있던 가장 최근 10-K/10-Q에만
  매칭됩니다(`text_features.select_point_in_time_filing`) — 나중에 나온
  공시가 과거 라벨 학습에 새어 들어가는(look-ahead) 걸 막기 위함입니다.
- 임베딩(384차원)은 PCA로 `--text-components`(기본 4)차원까지 축소하는데,
  이 PCA는 학습 구간 데이터에만 fit하고 검증/최신 시점 데이터는 transform만
  합니다.
- 종목 수만큼 SEC EDGAR에 개별 요청을 보내는 구조라 `--limit`이 크면 상당히
  오래 걸립니다 (테스트는 작은 `--limit`으로 먼저 해보세요).
- **`--text-components`는 학습 행 수 대비 작게 유지하세요.** `--limit 30`
  (학습 행 ~90개) 기준으로 16차원까지 올렸더니 `reason` 컬럼의 설명이 거의
  전부 "공시문 텍스트 패턴"으로만 나오고 재무비율은 하나도 안 보이는 현상이
  실제로 발생했습니다 — 매칭률은 100%였는데도(진짜 데이터였는데도) 피처 수
  (텍스트 16 + 재무/거시 11 = 27개)가 학습 행 수(89개)에 비해 너무 많아서
  생긴 과적합으로 보입니다. 기본값 4로는 재무비율/텍스트가 고르게 섞여서
  나오는 걸 확인했습니다. `--limit`을 크게 올려 학습 행이 충분히 많아지면
  `--text-components`도 같이 올려보세요.

### 분할매수 타이밍 강화학습 (실험 기능)

"갭 -10%면 1차매수, -20%면 2차매수" 같은 사람이 정한 규칙 대신, 과거
(품질점수, 저평가 percentile, 가격) 흐름을 replay하며 "언제 얼마나 살지"를
직접 학습하는 테이블 기반 Q-learning 에이전트입니다.

```bash
python -m stock_valuation.cli --limit 30 --with-rl
```

- **환경**: 종목 하나의 분기별 시퀀스가 한 에피소드. 상태 = (품질점수 구간,
  저평가 percentile 구간, 남은 현금 비중 구간), 행동 = 이번 분기에 남은
  현금 중 몇 %를 투입할지(0/25/50/100%), 보상 = 그 분기의 포트폴리오 가치
  변화율.
- **왜 딥러닝이 아니라 테이블 Q-learning인가**: 종목당 학습 가능한 분기
  수가 많아야 수십 개 수준이라, PPO 같은 딥RL을 쓰면 데이터보다 파라미터가
  훨씬 많아져서 의미 없는 학습이 됩니다. 상태를 몇 개 구간으로 나눈
  테이블이 지금 데이터 규모에 맞는 선택입니다.
- **과거 P/E·P/B를 어떻게 시점별로 구했나**: 새로 데이터를 받아오지 않고,
  이미 갖고 있는 재무제표(`net_income`, `shares_outstanding`, `total_equity`)
  로 각 분기 시점의 TTM(직전 4분기 합산) EPS·주당순자산을 역산하고, 그
  시점 가격은 `.asof()`로만 가져와서(`valuation.compute_point_in_time_multiples`)
  미래 데이터가 과거 학습에 새어 들어가지 않게 했습니다.
- **결과 컬럼**: `rl_action` — 지금 이 종목에 대해 "현금 100%에서 시작한다면"
  에이전트가 추천하는 행동 (`관망`/`25% 매수`/`50% 매수`/`전액 매수`).

> ⚠️ **이건 투자 전략 검증이 아니라 RL 실습에 가깝습니다.** 학습에 쓴
> 과거 궤적을 그대로 replay해서 학습(in-sample)하기 때문에, 실제로는
> "과거 데이터에 맞춘 패턴"을 외운 것일 가능성이 높습니다. 별도의 미래
> 구간에서 진짜로 검증(out-of-sample backtest)하기 전까지는 `rl_action`을
> 실제 매매 판단에 쓰지 마세요.

## 알려진 한계 (다음 단계에서 다룰 것)

- 여전히 과거 데이터 기반 통계 모델일 뿐, 미래 수익을 보장하지 않습니다.
  이 프로젝트의 목적은 "투자 조언 생성"이 아니라 "원본 데이터 기반 정량
  스크리닝 연습"입니다.
- 상장폐지/편출 종목을 반영하지 않아 생존편향(survivorship bias)이 있습니다.
  실전 백테스트를 하려면 과거 시점의 실제 지수 구성종목 이력이 필요합니다.
- `yfinance` 무료 API는 분기 재무제표를 최근 4~5개 분기치만 돌려줍니다. 그래서
  전년 동기 대비 성장률(YoY, 4분기 전 값 필요) 피처는 데이터가 부족하면 자동으로
  학습에서 제외됩니다 (`pipeline.py`가 실행 로그에 어떤 컬럼을 뺐는지 출력함).
  더 긴 히스토리가 필요하면 `yfinance`의 연간 재무제표(`.financials`)를 추가로
  붙이거나 유료 데이터 소스로 교체해야 합니다.
- 공시 텍스트 피처는 여전히 LightGBM에 숫자 피처로 얹는 방식입니다 (표+텍스트를
  한 네트워크로 같이 학습하는 진짜 Transformer 멀티모달 구조는 아직 아님).
- RL 에이전트는 in-sample 학습이라 백테스트상 좋아 보이는 것과 실전에서
  통하는 것 사이에 거리가 있습니다. out-of-sample 검증 없이는 실험 단계.
