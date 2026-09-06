# S&P500 장기 저평가 스크리너 (1단계 베이스라인)

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
features.py            비율/성장률 계산 → 섹터 내 z-score → 거시지표 병합
labels.py               미래 N일 상대수익률 → 분위(tier) 라벨
model.py                LightGBM 분류기 (기간 기준 train/test 분리)
valuation.py            품질점수 + 섹터 내 저평가 percentile → 분할매수 티어
pipeline.py              위 전체를 엮는 오케스트레이션
cli.py                  커맨드라인 실행 진입점
```

## 사용법

```bash
pip install -r stock_valuation/requirements.txt
python -m stock_valuation.cli --limit 30 --start 2015-01-01 --output out.csv
```

- `--limit`: 대상 종목 수 (테스트할 땐 작게, 실전은 500까지)
- 결과 CSV에는 `ticker`, `quality_score`(품질점수), `cheapness_percentile`
  (섹터 내 저평가 순위, 낮을수록 쌈), `buy_tier`(분할매수 단계) 컬럼이 담깁니다.

> 참고: 이 저장소를 실행 중인 샌드박스 환경은 외부 네트워크(Wikipedia, Yahoo
> Finance, FRED)가 정책상 차단되어 있어 실제 데이터로 전체 파이프라인을
> 여기서 실행해보진 못했습니다. 로직 자체는 `tests/test_stock_valuation.py`의
> 합성 데이터 테스트 9개로 검증했으니, 실제 인터넷이 되는 본인 컴퓨터에서
> 위 명령을 그대로 실행하면 됩니다.

## 알려진 한계 (다음 단계에서 다룰 것)

- 여전히 과거 데이터 기반 통계 모델일 뿐, 미래 수익을 보장하지 않습니다.
  이 프로젝트의 목적은 "투자 조언 생성"이 아니라 "원본 데이터 기반 정량
  스크리닝 연습"입니다.
- 상장폐지/편출 종목을 반영하지 않아 생존편향(survivorship bias)이 있습니다.
  실전 백테스트를 하려면 과거 시점의 실제 지수 구성종목 이력이 필요합니다.
- 다음 단계: 공시 원문(10-K/10-Q Risk Factors, MD&A) 텍스트 임베딩을 추가해
  Transformer 기반 멀티모달 모델로 확장 예정.
