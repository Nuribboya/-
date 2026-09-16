"""기본 설정값.

CLI 인자로 얼마든지 덮어쓸 수 있으며, 여기 값은 참고용 기본값이다.
"""
from __future__ import annotations

# 별도로 --tickers / --tickers-file 을 지정하지 않았을 때 스크리닝할 기본 종목군.
# 시가총액 상위 우량주 위주 예시 목록이며, 실제 사용 시 원하는 유니버스로 교체하는 것을 권장한다.
DEFAULT_TICKERS: list[str] = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "JNJ",
    "PG",
    "KO",
    "PEP",
    "005930.KS",  # 삼성전자
    "000660.KS",  # SK하이닉스
    "005380.KS",  # 현대차
]

MAX_FINANCIAL_YEARS = 10
DEFAULT_AAA_BOND_YIELD_PCT = 4.4
DEFAULT_MIN_MARGIN_OF_SAFETY = 0.25
DEFAULT_NEWS_PER_TICKER = 5
