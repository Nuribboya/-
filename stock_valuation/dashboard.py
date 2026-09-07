from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from stock_valuation.pipeline import run_pipeline
from stock_valuation.portfolio import build_steady_growth_portfolio

st.set_page_config(page_title="S&P500 장기 저평가 스크리너", layout="wide")
st.title("S&P500 장기 저평가 스크리너")

DEFAULT_OUTPUT = "valuation_signal.csv"

with st.sidebar:
    st.header("스캔 설정")
    limit = st.number_input("종목 수", min_value=5, max_value=500, value=500, step=5)
    with_text = st.checkbox("공시 텍스트 임베딩 사용 (느림, 별도 설치 필요)", value=False)
    with_rl = st.checkbox("RL 분할매수 추천 포함", value=False)
    with_revenue_consistency = st.checkbox(
        "연간 매출 일관성 체크 포함 (느림 — 대형주+매출안정형 포트폴리오에 필요)", value=False
    )
    output_path = st.text_input("결과 파일 경로", value=DEFAULT_OUTPUT)
    run_now = st.button("지금 다시 스캔 실행", type="primary")

    st.divider()
    st.caption("화면 자동 새로고침 — 예약 작업(스케줄러)이 결과 파일을 갱신했을 때 반영용")
    auto_refresh_min = st.number_input("자동 새로고침 주기(분, 0=끄기)", min_value=0, max_value=180, value=0)

if run_now:
    with st.spinner(f"{limit}개 종목 스캔 중... (몇 분 걸릴 수 있어요)"):
        result, metrics = run_pipeline(
            tickers_limit=limit,
            use_filing_text=with_text,
            use_rl=with_rl,
            use_revenue_consistency=with_revenue_consistency,
        )
        result.to_csv(output_path, index=False)
    accuracy = metrics.get("accuracy")
    msg = f"스캔 완료! (train/test rows: {metrics.get('n_train')}/{metrics.get('n_test')}"
    msg += f", holdout accuracy: {accuracy:.3f})" if accuracy is not None else ")"
    st.success(msg)

path = Path(output_path)
if not path.exists():
    st.info("아직 결과 파일이 없어요. 왼쪽에서 '지금 다시 스캔 실행'을 눌러보세요.")
    st.stop()

df = pd.read_csv(path)
last_modified = pd.Timestamp.fromtimestamp(path.stat().st_mtime)
st.caption(f"마지막 갱신: {last_modified:%Y-%m-%d %H:%M:%S}")

col1, col2 = st.columns(2)
with col1:
    sectors = st.multiselect("섹터 필터", sorted(df["sector"].dropna().unique()) if "sector" in df else [])
with col2:
    min_quality = st.slider("최소 품질점수", 0.0, 1.0, 0.0, 0.01)

filtered = df
if "quality_score" in filtered:
    filtered = filtered[filtered["quality_score"] >= min_quality]
if sectors:
    filtered = filtered[filtered["sector"].isin(sectors)]

st.dataframe(filtered, width="stretch", height=650)
st.caption(f"{len(filtered)} / {len(df)} 종목 표시 중")

if auto_refresh_min > 0:
    st_autorefresh(interval=auto_refresh_min * 60 * 1000, key="dashboard_auto_refresh")

st.divider()
with st.expander("대형주 + 매출 안정형 포트폴리오 (실제 매매 전 반드시 직접 검토하세요)"):
    st.caption(
        "저평가 신호(buy_tier)와 무관하게, 최근 몇 년간 매출이 감소 구간 없이 꾸준히 늘고 "
        "부채비율도 안정적인 종목만 골라서 시가총액이 큰 순서로 상위 N개를 시총 비중으로 "
        "배분합니다. 스캔할 때 '연간 매출 일관성 체크' 옵션을 켜야 이 섹션이 동작해요. "
        "`entry_zone`은 현재가가 그 종목 자신의 52주 저점/이동평균선 대비 어디쯤 있는지 "
        "보여주는 참고용 지표입니다 (미래를 예측하는 게 아니라 지금 위치를 설명하는 것)."
    )
    scol1, scol2, scol3 = st.columns(3)
    with scol1:
        steady_max_positions = st.number_input("최대 종목 수 ", min_value=1, max_value=50, value=15)
    with scol2:
        steady_max_weight_per_stock = st.slider("종목당 최대 비중 ", 0.02, 1.0, 0.15, 0.01)
    with scol3:
        steady_max_weight_per_sector = st.slider("섹터당 최대 비중 ", 0.05, 1.0, 0.30, 0.01)
    scol4, scol5 = st.columns(2)
    with scol4:
        steady_min_volume = st.number_input(
            "최소 평균 거래량 (최근 20일) ", min_value=0, value=0, step=10_000
        )
    with scol5:
        require_debt_health = st.checkbox("부채비율 안정적인 종목만", value=True)

    if require_debt_health and "debt_health_ok" not in df:
        st.warning("이 결과 파일엔 debt_health_ok 컬럼이 없어서 부채비율 필터를 못 써요 — 다시 스캔해보세요.")
        require_debt_health = False

    if "revenue_consistency_ok" not in df or "market_cap" not in df:
        st.info(
            "이 결과 파일엔 market_cap/revenue_consistency_ok 컬럼이 없어요 — 왼쪽에서 "
            "'연간 매출 일관성 체크 포함'을 켜고 다시 스캔해보세요."
        )
    else:
        steady_portfolio = build_steady_growth_portfolio(
            df,
            max_positions=steady_max_positions,
            max_weight_per_stock=steady_max_weight_per_stock,
            max_weight_per_sector=steady_max_weight_per_sector,
            min_avg_volume=steady_min_volume if steady_min_volume > 0 else None,
            require_debt_health=require_debt_health,
        )
        if steady_portfolio.empty:
            st.info("매출이 꾸준히 늘어난 종목이 없어요 (기준을 완화하거나 --limit을 늘려보세요).")
        else:
            st.dataframe(steady_portfolio, width="stretch")
            st.bar_chart(steady_portfolio.set_index("ticker")["weight"])
