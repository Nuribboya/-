from __future__ import annotations

import pandas as pd

# Thresholds on "% above the trailing 52-week low" — a purely descriptive
# read of where the current price sits in its own recent range, not a
# prediction of where it's headed next.
FAVORABLE_MAX_PCT_ABOVE_LOW = 0.10
STRETCHED_MIN_PCT_ABOVE_LOW = 0.30

ZONE_FAVORABLE = "매수 유리 구간"
ZONE_STRETCHED = "고점권 (눌림목 대기 권장)"
ZONE_NEUTRAL = "중립 구간"
ZONE_UNKNOWN = "판단 불가"


def compute_entry_zone_metrics(price_history: pd.DataFrame) -> dict:
    """From one ticker's own daily price history (columns: date, close),
    compute where the latest price sits in its recent range: trailing
    52-week low/high, 50-day and 200-day moving averages.

    Uses whatever history is actually available (via `.tail()`) rather than
    requiring a full year/200 days to exist — a recently-listed or
    thinly-covered ticker just gets a shorter window instead of nothing.
    """
    h = price_history.dropna(subset=["close"]).sort_values("date")
    if h.empty:
        return {}

    window_52w = h.tail(252)
    current_price = float(h["close"].iloc[-1])
    low_52w = float(window_52w["close"].min())
    high_52w = float(window_52w["close"].max())
    ma_50 = float(h["close"].tail(50).mean())
    ma_200 = float(h["close"].tail(200).mean())
    pct_above_low = (current_price / low_52w - 1) if low_52w > 0 else float("nan")

    return {
        "current_price": current_price,
        "low_52w": low_52w,
        "high_52w": high_52w,
        "ma_50": ma_50,
        "ma_200": ma_200,
        "pct_above_low": pct_above_low,
    }


def classify_entry_zone(metrics: dict) -> tuple[str, str]:
    """Turn compute_entry_zone_metrics()'s output into a (zone, detail) read.

    "매수 유리 구간" means the price is close to its own trailing low or
    below its 200-day average — cheap relative to its *own* recent range,
    not a signal that it will bounce. This is a technical-analysis-style
    descriptive read, not a forecast; combine with the fundamentals-based
    columns rather than using it alone.
    """
    pct_above_low = metrics.get("pct_above_low")
    if not metrics or pct_above_low is None or pd.isna(pct_above_low):
        return ZONE_UNKNOWN, "가격 데이터 부족"

    current_price = metrics["current_price"]
    ma_200 = metrics.get("ma_200")
    below_ma200 = ma_200 is not None and not pd.isna(ma_200) and current_price < ma_200
    detail = f"52주 저점 대비 +{pct_above_low * 100:.0f}%" + (", 200일선 아래" if below_ma200 else "")

    if pct_above_low <= FAVORABLE_MAX_PCT_ABOVE_LOW or below_ma200:
        return ZONE_FAVORABLE, detail
    if pct_above_low >= STRETCHED_MIN_PCT_ABOVE_LOW:
        return ZONE_STRETCHED, detail
    return ZONE_NEUTRAL, detail
