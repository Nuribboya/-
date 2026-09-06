from __future__ import annotations

import pandas as pd
import requests

from stock_valuation.valuation import BUY_TIERS

# BUY_TIERS is ordered strongest-first; index 0 is "3차 매수 (강한 저평가)".
STRONG_BUY_TIER = BUY_TIERS[0][1]


def strong_buy_tickers(result: pd.DataFrame) -> list[str]:
    """Tickers that hit the strongest buy tier in this run's result table."""
    return result.loc[result["buy_tier"] == STRONG_BUY_TIER, "ticker"].tolist()


def format_notification_message(tickers: list[str]) -> str:
    return f"{STRONG_BUY_TIER} 신호: {', '.join(tickers)}"


def send_ntfy_notification(topic: str, message: str, title: str = "Stock Screener Alert") -> None:
    """Push a phone notification via ntfy.sh — free, no account or API key.

    Install the ntfy app (iOS/Android) and subscribe to `topic` there to
    receive this. Keep `title` ASCII: HTTP headers aren't reliably UTF-8
    safe across clients, so Korean text belongs in `message` (the request
    body), which has no such restriction.
    """
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "default"},
        timeout=10,
    )
