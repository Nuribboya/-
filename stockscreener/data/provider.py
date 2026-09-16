"""재무제표/시세 데이터 공급자 인터페이스.

실제 데이터 소스(야후 파이낸스 등)를 교체할 수 있도록 프로토콜로 분리한다.
테스트에서는 네트워크 호출 없는 가짜(FakeDataProvider) 구현을 사용한다.
"""
from __future__ import annotations

from typing import Protocol

from stockscreener.models import PriceSnapshot, YearlyFinancials


class DataUnavailableError(Exception):
    """해당 종목의 데이터를 가져올 수 없을 때 발생 (네트워크 오류, 상장폐지 등)."""


class DataProvider(Protocol):
    def get_price_snapshot(self, ticker: str) -> PriceSnapshot:
        ...

    def get_annual_financials(self, ticker: str, max_years: int = 10) -> list[YearlyFinancials]:
        """최신 연도가 마지막 원소가 되도록 오름차순 정렬된 연간 재무제표 목록.

        데이터 소스가 max_years 만큼 제공하지 못하면 가능한 만큼만 반환한다
        (빈 리스트를 반환할지언정 예외를 던지지 않는다 — 상위 로직이 데이터
        부족을 안전하게 처리한다).
        """
        ...
