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
dashboard.py            Streamlit 웹 대시보드 (결과 조회 + 그 자리에서 재스캔)
notify.py               ntfy.sh로 3차 매수 신호 폰 알림 (CLI --notify-topic)
portfolio.py            매수 신호 종목을 비중 배분한 후보 포트폴리오로 구성
                        (+ 대형주/매출안정형 대안 포트폴리오)
growth_consistency.py  연간 매출 일관성 + 분기 부채비율/영업이익률 안정성 판정
entry_timing.py        52주 저점/이동평균 대비 현재가 위치(매수 유리/중립/고점권) 판정
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

### 웹 대시보드로 보기

터미널 표 대신 브라우저에서 정렬/필터 가능한 표로 보고 싶으면:

```bash
pip install -r stock_valuation/requirements-dashboard.txt
streamlit run stock_valuation/dashboard.py
```

- 왼쪽 사이드바에서 종목 수/옵션 정하고 **"지금 다시 스캔 실행"** 누르면
  그 자리에서 새로 스캔해서 표가 갱신됩니다 (CLI 따로 안 돌려도 됨).
- 섹터/매수단계/최소 품질점수로 필터링 가능.
- "마지막 갱신" 시각이 파일 수정 시각 기준으로 표시됩니다.

**완전 자동 갱신(예약 실행)까지 원하면**: 대시보드 자체 새로고침은 "결과
파일이 바뀌었으면 화면에 반영"하는 것뿐이고, 실제로 최신 데이터를 다시
스캔해서 파일을 갱신하는 건 별도로 예약해야 합니다. 예를 들어 매일 아침
자동으로 스캔하려면 배치 파일 하나 만들고:

```bat
:: run_daily_scan.bat
@echo off
cd /d "C:\Users\윤여명\dl-stock-project"
python -m stock_valuation.cli --limit 30 --output valuation_signal.csv
```

Windows 작업 스케줄러에 등록:

```powershell
schtasks /create /tn "StockScreenerDaily" /tr "C:\Users\윤여명\dl-stock-project\run_daily_scan.bat" /sc daily /st 07:00
```

이렇게 해두면 매일 아침 7시에 자동으로 재스캔되고, 대시보드의 "자동
새로고침" 주기를 켜두면 브라우저를 열어놓기만 해도 갱신된 결과가 자동
반영됩니다.

### 3차 매수 신호 뜨면 폰으로 알림 받기

[ntfy](https://ntfy.sh) 무료 푸시 알림 서비스를 씁니다 — 가입/API 키 없이
앱 설치 + 토픽(비밀 채널 이름) 하나 정하면 끝입니다.

1. 폰에 **ntfy** 앱 설치 (iOS/Android 둘 다 있음)
2. 앱에서 아무도 안 쓸 법한 임의의 토픽 이름 하나 정해서 구독 (예:
   `yeomyeong-stock-alert-8421` 처럼 남들이 못 맞출 만한 문자열 추천 — 같은
   토픽 이름을 아는 사람은 누구나 그 알림을 볼 수 있는 구조라서)
3. CLI에 `--notify-topic` 옵션으로 그 토픽을 넘기면, 이번 스캔 결과에
   "3차 매수(강한 저평가)" 종목이 하나라도 있을 때만 폰으로 알림이 갑니다.

```bash
python -m stock_valuation.cli --limit 500 --notify-topic yeomyeong-stock-alert-8421
```

매일 자동 스캔에도 그대로 넣으면 됩니다:

```bat
:: run_daily_scan.bat
@echo off
cd /d "C:\Users\윤여명\dl-stock-project"
python -m stock_valuation.cli --limit 500 --output valuation_signal.csv --notify-topic yeomyeong-stock-alert-8421
```

이렇게 해두면 평소엔 신경 안 쓰고 있다가, 강한 저평가 종목이 새로 뜰 때만
폰에 알림이 옵니다. (지금은 대시보드가 아니라 CLI 실행에만 연결돼 있어서,
대시보드의 "지금 다시 스캔 실행" 버튼에서는 알림이 안 갑니다 — 자동
스케줄 스캔용으로 만든 기능입니다.)

### 판단 근거 (`reason` 컬럼)

각 종목이 왜 그 점수/단계를 받았는지 사람이 읽을 수 있는 문장으로 같이 나옵니다.

- **품질 부분**: LightGBM의 `pred_contrib`(SHAP 방식과 동일한 원리, 별도
  라이브러리 없이 LightGBM 자체 기능)로 이번 예측에 가장 크게 기여한 피처
  top 3를 뽑아서 `ROE(섹터 대비) ↑, 부채비율(섹터 대비) ↓` 처럼 보여줍니다.
  화살표는 그 피처가 품질점수를 올렸는지(↑) 내렸는지(↓)를 의미합니다.
- **저평가 부분**: P/E, P/B가 섹터 내에서 각각 하위 몇 %인지 그대로 풀어씀
  (`model.py`가 아니라 `valuation.py`가 이미 계산해둔 percentile을 문장으로
  바꾸는 것뿐이라 별도 모델이 필요 없습니다).

### 단순 성장 둔화 vs 밸류트랩 (`undervaluation_cause` 컬럼)

싸다고 무조건 좋은 게 아니라, "일시적으로 성장이 둔해져서 싼 건지" vs
"펀더멘털 자체가 나빠지고 있어서 싼(=밸류트랩) 건지"를 구분해줍니다.

- 그 종목 자체의 **가장 최근 두 분기**만 비교합니다 (`explanations.classify_undervaluation_cause`).
  매출 감소·순이익 적자 전환·영업이익률 5%p 이상 하락·부채비율 30% 이상
  급증, 이 네 가지 중 **2개 이상 동시에 나쁘면** "⚠ 펀더멘털 악화 신호 —
  밸류트랩 주의", **1개만 나쁘면** "단순 성장 둔화로 보임", **다 괜찮으면**
  "펀더멘털은 안정적 — 시장이 과매도했을 가능성"으로 분류합니다.
- 애널리스트 의견이나 뉴스 없이 재무제표 숫자만 보고 판단하는 거라
  완벽하진 않습니다 — 참고용 1차 필터로 보시고, 정말 중요한 종목은 직접
  최근 실적 발표나 공시를 확인하세요.

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

### 포트폴리오 구성 (진단/실험용)

개별 종목 신호 목록이 아니라, 실제로 몇 %씩 담을지까지 배분해줍니다.

```bash
python -m stock_valuation.cli --limit 500 --portfolio
```

- 매수 신호(1차/2차/3차, `관망` 제외)를 받은 종목만 후보로 삼고, 강한
  신호 → 품질점수 순으로 정렬해서 상위 N개(`--portfolio-max-positions`,
  기본 15)만 남깁니다.
- 품질점수 비중으로 1차 배분한 뒤, **종목당 최대 비중**
  (`--portfolio-max-weight-per-stock`, 기본 15%)과 **섹터당 최대 비중**
  (`--portfolio-max-weight-per-sector`, 기본 30%)을 넘는 만큼을 다른
  종목/섹터로 반복해서 재분배합니다.
- 대시보드에서는 결과표 아래 "포트폴리오 구성" 펼치기 메뉴에서 슬라이더로
  값 조정하면서 바로 확인할 수 있어요.

**후보를 더 좁히고 싶으면** (예: "3차 매수인데 밸류트랩 아니고 거래량도
받쳐주는 것만"):

```bash
python -m stock_valuation.cli --limit 500 --portfolio \
  --portfolio-strong-only --portfolio-oversold-only --portfolio-min-volume 500000
```

- `--portfolio-strong-only`: 3차 매수(강한 저평가)만 후보로
- `--portfolio-oversold-only`: `undervaluation_cause`가 "펀더멘털은 안정적
  — 시장이 과매도했을 가능성"인 것만 (밸류트랩/단순 성장 둔화 제외)
- `--portfolio-min-volume`: 최근 20거래일 평균 거래량이 이 값 이상인
  것만 (너무 얇게 거래되는 종목 거르는 유동성 필터)

대시보드에도 같은 필터가 체크박스/숫자입력으로 들어가 있어요.

> ⚠️ **이건 평균-분산 최적화(Markowitz) 같은 게 아니라 단순 분산 규칙입니다.**
> 종목 간 가격 상관관계나 변동성은 전혀 안 봅니다 — 그냥 "한 종목/섹터에
> 너무 몰리지 않게" 상한만 지키는 수준이에요. 그리고 종목 수가 너무 적은데
> 상한을 너무 빡빡하게 잡으면(예: 4종목에 종목당 15% 상한 = 최대 60%밖에
> 못 채움) 수학적으로 상한을 다 지킬 방법이 없는 경우도 있습니다. 밑에
> 있는 개별 종목 신호들의 불확실성(밸류트랩 가능성, in-sample RL 등)도
> 그대로 이어받으니, 결국 "이 조합으로 한번 담아볼까?"에 대한 참고용
> 스타팅포인트로만 쓰세요.

### 대안: 대형주 + 매출 안정형 포트폴리오

저평가 신호(buy_tier) 기반 대신, "시가총액 크고 매출이 몇 년간 꾸준히 늘어난
기업" 기준으로 완전히 다른 포트폴리오를 짤 수도 있습니다. 저평가/품질 점수
자체를 아예 쓰지 않는 별도 경로입니다.

```bash
python -m stock_valuation.cli --limit 500 --steady-growth-portfolio
```

- **시가총액**: 이미 갖고 있는 현재가 × 발행주식수로 계산합니다 (새로
  데이터를 받아오지 않음).
- **매출 안정성**: yfinance의 연간 재무제표(보통 최근 4개년치)를 종목마다
  추가로 조회해서, 그 기간 동안 전년 대비 2% 넘게 감소한 해가 한 번도
  없고 누적으로도 늘었으면 "안정적 성장"으로 판정합니다
  (`growth_consistency.classify_revenue_consistency`). `--steady-growth-portfolio`
  를 켜면 자동으로 이 조회까지 같이 돕니다 — 종목 수만큼 추가 요청이
  붙어서 느려집니다.
- **부채 건전성**: 매출이 늘어도 회사채 발행 등으로 빚을 늘려서 키운 걸
  수도 있으니, 이미 갖고 있는 분기별 부채비율(새로 조회하지 않음)로 별도
  체크합니다 — 부채비율 절대 수준이 너무 높거나(기본 2.0배 초과), 최근
  분기들 사이 50% 넘게 급증했으면 탈락시킵니다
  (`growth_consistency.classify_debt_health`). 기본으로 켜져 있고
  (`--steady-growth-portfolio`만 켜면 자동 적용), 끄고 싶으면 코드에서
  `require_debt_health=False`로 호출하거나 대시보드 체크박스를 끄면 됩니다.
- **지출 효율성**: 매출이 늘어도 비용이 그보다 더 빨리 늘면 남는 게 없으니,
  이미 갖고 있는 분기별 영업이익률(새로 조회하지 않음)로 별도 체크합니다 —
  현재 영업이익률이 너무 낮거나(기본 5% 미만), 확보된 분기들 사이 30% 넘게
  악화됐으면(비용 증가 속도가 매출보다 빠른 경우) 탈락시킵니다
  (`growth_consistency.classify_expense_efficiency`). 기본으로 켜져 있고,
  끄고 싶으면 코드에서 `require_expense_efficiency=False`로 호출하거나
  대시보드 체크박스를 끄면 됩니다.
- 매출 안정성 + 부채 건전성 + 지출 효율성을 모두 통과한 종목만 후보로 남기고, 시가총액이
  큰 순서로 상위 N개(`--steady-growth-max-positions`, 기본 15)를 뽑아
  시가총액 비중으로 배분합니다. 종목당/섹터당 상한
  (`--steady-growth-max-weight-per-stock`, `--steady-growth-max-weight-per-sector`)
  과 최소 거래량(`--steady-growth-min-volume`)도 위 포트폴리오와 동일하게
  지원합니다.
- 대시보드에도 별도 "대형주 + 매출 안정형 포트폴리오" 펼치기 메뉴가 있어요
  (사이드바에서 "연간 매출 일관성 체크 포함" 켜고 스캔해야 동작함).
- **매수 타이밍 참고 (`entry_zone`, `entry_zone_detail`)**: 포트폴리오에 든
  종목이라도 언제 사는 게 나을지는 별개 문제라, 이미 갖고 있는 가격
  히스토리(새로 조회 안 함)로 "지금 가격이 이 종목 자신의 최근 레인지
  어디쯤 있는지"를 계산합니다 — 52주 저점 대비 몇 % 위인지, 200일
  이동평균선 위/아래인지. 저점 대비 +10% 이내거나 200일선 아래면
  "매수 유리 구간", +30% 이상이면 "고점권 (눌림목 대기 권장)", 그 사이면
  "중립 구간"으로 표시됩니다 (`entry_timing.classify_entry_zone`). 이건
  항상 계산되는 컬럼이라 `--steady-growth-portfolio` 없이도 나옵니다.

> ⚠️ 연간 재무제표 데이터가 보통 4개년치뿐이라 "몇 년"의 기준이 넉넉하진
> 않습니다. 또 "매출이 꾸준했다"는 과거 얘기지 미래를 보장하지 않고요.
> 여기도 위 포트폴리오와 마찬가지로 종목/섹터 상관관계는 전혀 안 보는
> 단순 분산 규칙입니다. `entry_zone`도 마찬가지로 "가격이 어디 있는지"를
> 설명하는 것뿐, "여기서 사면 오른다"는 예측이 아닙니다 — 기술적 분석
> 특유의 근거 약한 방법론이라는 점을 감안하고 참고용으로만 쓰세요.

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
