from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# Sale methods. Draws (raffles) give everyone the same odds and usually mean a
# smaller allocation; first-come-first-served drops tend to be bigger runs.
DRAW = "draw"
FCFS = "fcfs"
UNKNOWN = "unknown"


@dataclass
class Release:
    source: str
    source_id: str
    name: str
    retail_price: int | None = None  # KRW
    release_at: datetime | None = None
    entry_start: datetime | None = None
    entry_end: datetime | None = None
    method: str = UNKNOWN
    retailer_count: int | None = None
    is_restock: bool = False
    market_price: int | None = None  # KRW, known resale price (e.g. typed in from KREAM)
    url: str = ""
    brand: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class Score:
    level: str  # S / A / B / C
    verdict: str  # 추천 / 애매 / 패스
    collaborators: list[str] = field(default_factory=list)
    est_price: int | None = None
    est_profit: int | None = None
    profit_ratio: float | None = None
    price_is_estimate: bool = True
    reasons: list[str] = field(default_factory=list)


def fmt_kst(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.astimezone(KST).strftime("%m/%d %H:%M")
