from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from stock_valuation.pipeline import run_pipeline

st.set_page_config(page_title="S&P500 장기 저평가 스크리너", layout="wide")
st.title("S&P500 장기 저평가 스크리너")

DEFAULT_OUTPUT = "valuation_signal.csv"

with st.sidebar:
    st.header("스캔 설정")
    limit = st.number_input("종목 수", min_value=5, max_value=500, value=30, step=5)
    with_text = st.checkbox("공시 텍스트 임베딩 사용 (느림, 별도 설치 필요)", value=False)
    with_rl = st.checkbox("RL 분할매수 추천 포함", value=False)
    output_path = st.text_input("결과 파일 경로", value=DEFAULT_OUTPUT)
    run_now = st.button("지금 다시 스캔 실행", type="primary")

    st.divider()
    st.caption("화면 자동 새로고침 — 예약 작업(스케줄러)이 결과 파일을 갱신했을 때 반영용")
    auto_refresh_min = st.number_input("자동 새로고침 주기(분, 0=끄기)", min_value=0, max_value=180, value=0)

if run_now:
    with st.spinner(f"{limit}개 종목 스캔 중... (몇 분 걸릴 수 있어요)"):
        result, metrics = run_pipeline(tickers_limit=limit, use_filing_text=with_text, use_rl=with_rl)
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

col1, col2, col3 = st.columns(3)
with col1:
    sectors = st.multiselect("섹터 필터", sorted(df["sector"].dropna().unique()) if "sector" in df else [])
with col2:
    tiers = st.multiselect("매수 단계 필터", sorted(df["buy_tier"].dropna().unique()) if "buy_tier" in df else [])
with col3:
    min_quality = st.slider("최소 품질점수", 0.0, 1.0, 0.0, 0.01)

filtered = df
if "quality_score" in filtered:
    filtered = filtered[filtered["quality_score"] >= min_quality]
if sectors:
    filtered = filtered[filtered["sector"].isin(sectors)]
if tiers:
    filtered = filtered[filtered["buy_tier"].isin(tiers)]

st.dataframe(filtered, width="stretch", height=650)
st.caption(f"{len(filtered)} / {len(df)} 종목 표시 중")

if auto_refresh_min > 0:
    st_autorefresh(interval=auto_refresh_min * 60 * 1000, key="dashboard_auto_refresh")
