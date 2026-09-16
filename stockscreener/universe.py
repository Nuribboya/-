"""번들로 제공하는 종목 유니버스(예: S&P 500) 로더.

레포에 포함된 스냅샷 파일을 읽어 티커 목록을 반환한다. 지수 구성종목은
주기적으로 바뀌므로, 최신 목록이 필요하면 `stockscreener/data/universe/`
안의 파일을 갱신하면 된다.
"""
from __future__ import annotations

from importlib import resources


def load_sp500_tickers() -> list[str]:
    """번들된 S&P 500 스냅샷에서 티커 목록을 읽어온다 (약 500개)."""
    text = resources.files("stockscreener.data.universe").joinpath("sp500.txt").read_text(
        encoding="utf-8"
    )
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
