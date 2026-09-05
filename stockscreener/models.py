"""데이터 모델 정의.

모든 값은 기업의 재무제표/시세에서 얻은 절댓값(숫자)만 다루며, 애널리스트의
투자의견·목표주가 같은 주관적 지표는 어디에도 포함하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class YearlyFinancials:
    """한 회계연도의 재무제표 핵심 항목. 값을 구할 수 없으면 None."""

    fiscal_year: int
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    eps: Optional[float] = None
    book_value_per_share: Optional[float] = None
    total_current_assets: Optional[float] = None
    total_current_liabilities: Optional[float] = None
    total_liabilities: Optional[float] = None
    long_term_debt: Optional[float] = None
    dividend_per_share: Optional[float] = None

    @property
    def working_capital(self) -> Optional[float]:
        if self.total_current_assets is None or self.total_current_liabilities is None:
            return None
        return self.total_current_assets - self.total_current_liabilities

    @property
    def current_ratio(self) -> Optional[float]:
        if not self.total_current_liabilities:
            return None
        if self.total_current_assets is None:
            return None
        return self.total_current_assets / self.total_current_liabilities


@dataclass(frozen=True)
class PriceSnapshot:
    """조회 시점의 시세 스냅샷."""

    ticker: str
    price: Optional[float]
    currency: str = "USD"
    shares_outstanding: Optional[float] = None
    as_of: Optional[datetime] = None


@dataclass(frozen=True)
class NewsItem:
    """시장 뉴스 한 건. 요약/제목만 담고 투자 판단은 포함하지 않는다."""

    title: str
    link: str
    source: str
    published: Optional[datetime] = None
    summary: Optional[str] = None


@dataclass(frozen=True)
class GrahamCriterion:
    """벤저민 그레이엄의 방어적 투자자용 개별 기준 평가 결과."""

    key: str
    label: str
    passed: Optional[bool]  # 데이터 부족 시 None (실패로 단정하지 않음)
    detail: str


@dataclass(frozen=True)
class GrahamAnalysis:
    ticker: str
    graham_number: Optional[float]
    criteria: tuple[GrahamCriterion, ...]

    @property
    def evaluable_count(self) -> int:
        return sum(1 for c in self.criteria if c.passed is not None)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.criteria if c.passed is True)


@dataclass(frozen=True)
class FinancialTrend:
    """5~10년 재무제표 추세 분석 결과."""

    years_available: int
    fiscal_year_range: Optional[tuple[int, int]]
    revenue_cagr: Optional[float]
    net_income_cagr: Optional[float]
    eps_growth_pct: Optional[float]
    loss_years: int
    is_stable: bool
    insufficient_data: bool


@dataclass(frozen=True)
class ValuationResult:
    """절댓값 기반 내재가치 계산 결과."""

    ticker: str
    price: Optional[float]
    eps_used: Optional[float]
    growth_rate_pct_used: Optional[float]
    intrinsic_value: Optional[float]
    graham_number: Optional[float]
    margin_of_safety: Optional[float]
    price_below_graham_number: Optional[bool]
    is_undervalued: bool


@dataclass(frozen=True)
class StockReport:
    """종목 1개에 대한 최종 분석 결과 묶음."""

    ticker: str
    price: PriceSnapshot
    trend: Optional[FinancialTrend]
    graham: Optional[GrahamAnalysis]
    valuation: Optional[ValuationResult]
    news: tuple[NewsItem, ...] = field(default_factory=tuple)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None
