"""원청 후보 회사와 평가 결과를 담는 데이터 구조."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Candidate:
    name: str
    business_description: str
    distance_km: Optional[float] = None
    revenue_growth_pct: Optional[float] = None
    source: str = "unknown"


@dataclass
class ScoredCandidate:
    candidate: Candidate
    fit_score: int
    conflicts_with_excluded_client: bool
    reasoning: str
