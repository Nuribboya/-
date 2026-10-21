from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from resell_radar.tiers import GRADES, find_collaborators, find_hot_silhouette

# Hand-kept log of resale prices for items that already dropped (look them up
# on KREAM). Every row teaches the estimator how far the tier table is off.
PRICE_LOG_COLUMNS = ["상품명", "정가", "시세", "기록일", "메모"]
PRICE_LOG_EXAMPLE = {
    "상품명": "(예시) Union LA x Air Jordan 1 Low",
    "정가": "199000",
    "시세": "430000",
    "기록일": "2026-10-21",
    "메모": "크림 최근 체결가",
}

# How many observations a group needs before it outweighs its parent.
# Small on purpose: with 3 records the learned value and the prior count equally.
PRIOR_WEIGHT = 3.0
MIN_MULT, MAX_MULT = 0.5, 5.0


@dataclass
class Observation:
    name: str
    level: str  # grade the tier rules gave this item
    retail: int
    market: int


@dataclass
class Estimate:
    multiplier: float
    basis: str
    count: int  # observations behind the group that was used


def write_price_log_template(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PRICE_LOG_COLUMNS)
        writer.writeheader()
        writer.writerow(PRICE_LOG_EXAMPLE)


def _int(value: str | None) -> int | None:
    value = (value or "").replace(",", "").replace("원", "").strip()
    return int(value) if value.isdigit() else None


def load_price_log(path: Path, grade_of) -> list[Observation]:
    """Read prices.csv. grade_of(name) -> tier grade, so rows get the same prior as live items."""
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("상품명") or "").strip()
            retail, market = _int(row.get("정가")), _int(row.get("시세"))
            if not name or name.startswith("(예시)") or not retail or not market:
                continue
            rows.append(Observation(name, grade_of(name), retail, market))
    return rows


def _group(name: str, tiers: dict) -> str:
    collabs = find_collaborators(name, tiers)
    if collabs:
        return max(collabs, key=lambda c: GRADES.index(c[1]))[0]
    return find_hot_silhouette(name, tiers) or "일반 발매"


class Estimator:
    """Learns a correction on top of the tier table's price multipliers.

    For every recorded item, log(market / tier-estimate) says how wrong the
    tier table was. Corrections are pooled per group (main collaborator, else
    hot silhouette, else general release) and shrunk toward the market-wide
    correction, which is itself shrunk toward zero — so sparse groups lean on
    the overall market and an empty history reproduces the tier table exactly.
    """

    def __init__(self, observations: list[Observation], tiers: dict):
        self.tiers = tiers
        mults = tiers["price_multiplier"]
        self.residuals: dict[str, list[float]] = {}
        all_res = []
        for obs in observations:
            res = math.log(obs.market / (obs.retail * mults[obs.level]))
            self.residuals.setdefault(_group(obs.name, tiers), []).append(res)
            all_res.append(res)
        self.total = len(all_res)
        self.global_corr = sum(all_res) / (PRIOR_WEIGHT + len(all_res))

    def group_corr(self, group: str) -> tuple[float, int]:
        res = self.residuals.get(group, [])
        return (PRIOR_WEIGHT * self.global_corr + sum(res)) / (PRIOR_WEIGHT + len(res)), len(res)

    def estimate(self, name: str, level: str) -> Estimate:
        prior = self.tiers["price_multiplier"][level]
        group = _group(name, self.tiers)
        corr, n = self.group_corr(group)
        mult = min(MAX_MULT, max(MIN_MULT, prior * math.exp(corr)))
        if n:
            basis = f"{group} 기록 {n}건 반영"
        elif self.total:
            basis = f"전체 기록 {self.total}건 반영"
        else:
            basis = "등급표 추정"
        return Estimate(mult, basis, n)

    def summary(self) -> list[tuple[str, int, float]]:
        """[(group, records, correction factor)] — 1.20 means the tier table was 20% too low."""
        rows = [(g, len(r), math.exp(self.group_corr(g)[0])) for g, r in self.residuals.items()]
        return sorted(rows, key=lambda r: -r[1])
