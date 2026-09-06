from __future__ import annotations

import pandas as pd

FEATURE_LABELS = {
    "operating_margin_z": "영업이익률(섹터 대비)",
    "net_margin_z": "순이익률(섹터 대비)",
    "roe_z": "ROE(섹터 대비)",
    "debt_to_equity_z": "부채비율(섹터 대비)",
    "fcf_margin_z": "잉여현금흐름률(섹터 대비)",
    "revenue_growth_yoy_z": "매출 성장률(섹터 대비)",
    "net_income_growth_yoy_z": "순이익 성장률(섹터 대비)",
    "treasury_10y": "10년물 국채금리",
    "cpi": "소비자물가지수",
    "unemployment_rate": "실업률",
    "industrial_production": "산업생산지수",
}


def _feature_label(name: str) -> str:
    if name in FEATURE_LABELS:
        return FEATURE_LABELS[name]
    if name.startswith("text_emb_"):
        return "공시문 텍스트 패턴"
    return name


def format_quality_reason(contributions: list[tuple[str, float]]) -> str:
    """Turn model.explain_quality_scores()'s per-row output into a sentence.

    Each contribution's sign already says whether it pushed the "top tier"
    probability up or down — no need to separately hardcode which direction
    of each ratio counts as "good", since the model already learned that.
    """
    if not contributions:
        return "설명 불가"
    parts = [f"{_feature_label(name)} {'↑' if value > 0 else '↓'}" for name, value in contributions]
    return ", ".join(parts)


def format_cheapness_reason(row: pd.Series) -> str:
    """Which valuation multiple(s) drove the cheapness percentile.

    Reads straight off valuation.add_cheapness_percentile's own output —
    no model needed, this is just restating the numbers in words.
    """
    parts = []
    pe_pct = row.get("pe_ratio_pct")
    pb_pct = row.get("pb_ratio_pct")
    if pd.notna(pe_pct):
        parts.append(f"P/E 섹터 내 하위 {pe_pct * 100:.0f}%")
    if pd.notna(pb_pct):
        parts.append(f"P/B 섹터 내 하위 {pb_pct * 100:.0f}%")
    return ", ".join(parts) if parts else "밸류에이션 데이터 부족"


def build_reason_column(
    quality_contributions_by_ticker: dict[str, list[tuple[str, float]]], result: pd.DataFrame
) -> pd.Series:
    """One "품질: ... | 저평가: ..." sentence per row of the final result table.

    Looks contributions up by ticker rather than row position, since
    `result` and the model-scored frame it came from aren't guaranteed to
    share row order after their merges.
    """
    reasons = [
        f"품질: {format_quality_reason(quality_contributions_by_ticker.get(row['ticker'], []))} | "
        f"저평가: {format_cheapness_reason(row)}"
        for _, row in result.iterrows()
    ]
    return pd.Series(reasons, index=result.index, name="reason")
