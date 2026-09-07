from __future__ import annotations

import pandas as pd

from stock_valuation.valuation import BUY_TIERS, NO_SIGNAL

PORTFOLIO_COLUMNS = ["ticker", "sector", "buy_tier", "quality_score", "weight"]
STEADY_GROWTH_COLUMNS = [
    "ticker",
    "sector",
    "market_cap",
    "revenue_consistency_reason",
    "debt_health_reason",
    "expense_efficiency_reason",
    "entry_zone",
    "entry_zone_detail",
    "rally_support",
    "rally_support_reason",
    "weight",
]
_MAX_ITERATIONS = 50


def _redistribute(weights: pd.Series, capped_mask: pd.Series, excess: float) -> pd.Series:
    """Add `excess` to the uncapped rows, proportional to their current
    weight — or split evenly if they're all at zero (nothing to be
    proportional to yet)."""
    under = ~capped_mask
    if not under.any() or excess <= 0:
        return weights
    under_total = weights[under].sum()
    if under_total > 0:
        weights.loc[under] += excess * (weights.loc[under] / under_total)
    else:
        weights.loc[under] += excess / under.sum()
    return weights


def _apply_weight_caps(
    candidates: pd.DataFrame, max_weight_per_stock: float, max_weight_per_sector: float
) -> pd.DataFrame:
    """Iteratively cap per-stock and per-sector weight, redistributing the
    excess to whoever's still under their cap, until stable (or
    _MAX_ITERATIONS is hit — see build_portfolio's feasibility note).
    Expects `candidates` to already have a "weight" column summing to 1.0.
    """
    candidates = candidates.copy()
    for _ in range(_MAX_ITERATIONS):
        changed = False

        over_stock = candidates["weight"] > max_weight_per_stock + 1e-9
        if over_stock.any():
            excess = (candidates.loc[over_stock, "weight"] - max_weight_per_stock).sum()
            candidates.loc[over_stock, "weight"] = max_weight_per_stock
            candidates["weight"] = _redistribute(candidates["weight"], over_stock, excess)
            changed = True

        sector_totals = candidates.groupby("sector")["weight"].transform("sum")
        over_sector = sector_totals > max_weight_per_sector + 1e-9
        if over_sector.any():
            scale = max_weight_per_sector / sector_totals[over_sector]
            excess = (candidates.loc[over_sector, "weight"] * (1 - scale)).sum()
            candidates.loc[over_sector, "weight"] *= scale
            candidates["weight"] = _redistribute(candidates["weight"], over_sector, excess)
            changed = True

        if not changed:
            break

    candidates["weight"] = candidates["weight"] / candidates["weight"].sum()
    return candidates


def build_portfolio(
    result: pd.DataFrame,
    max_positions: int = 15,
    max_weight_per_stock: float = 0.15,
    max_weight_per_sector: float = 0.30,
    tiers: list[str] | None = None,
    causes: list[str] | None = None,
    min_avg_volume: float | None = None,
) -> pd.DataFrame:
    """Turn the scored/ranked screener output into a weighted candidate
    portfolio — diversification rules, not mean-variance optimization
    (this project has no return-covariance model to optimize against).

    Only names that already cleared a buy signal (not 관망/NO_SIGNAL) are
    eligible by default. Weighted by quality_score, ranked by buy tier
    strength first, then capped per-stock and per-sector by iteratively
    capping and redistributing the excess to everyone still under their cap.

    Optional narrowing filters:
    - `tiers`: keep only these buy_tier values (e.g. just the strongest
      tier) instead of any non-관망 tier.
    - `causes`: keep only these undervaluation_cause values (e.g. only
      explanations.CAUSE_LIKELY_OVERSOLD, excluding value traps and simple
      growth deceleration). Requires `result` to have that column.
    - `min_avg_volume`: drop names below this average daily volume, as a
      basic liquidity floor. Requires `result` to have an avg_volume column.

    The per-stock cap alone requires max_positions * max_weight_per_stock
    >= 1.0 to be satisfiable at all (e.g. 15 positions need >= ~6.7% cap
    each) — with too few eligible candidates or too tight a cap, weights
    will still sum to 1.0 but individual caps may not all hold, since
    there's no feasible allocation that respects them.
    """
    candidates = result[result["buy_tier"] != NO_SIGNAL].copy()
    if tiers is not None:
        candidates = candidates[candidates["buy_tier"].isin(tiers)]
    if causes is not None:
        candidates = candidates[candidates["undervaluation_cause"].isin(causes)]
    if min_avg_volume is not None:
        candidates = candidates[candidates["avg_volume"] >= min_avg_volume]
    candidates = candidates.dropna(subset=["quality_score", "sector"])
    candidates = candidates[candidates["quality_score"] > 0]
    if candidates.empty:
        return pd.DataFrame(columns=PORTFOLIO_COLUMNS)

    tier_rank = {tier: i for i, (_, tier) in enumerate(BUY_TIERS)}  # 0 = strongest tier
    candidates["_tier_rank"] = candidates["buy_tier"].map(tier_rank).fillna(len(BUY_TIERS))
    candidates = candidates.sort_values(["_tier_rank", "quality_score"], ascending=[True, False])
    candidates = candidates.head(max_positions).copy().reset_index(drop=True)

    candidates["weight"] = candidates["quality_score"] / candidates["quality_score"].sum()
    candidates = _apply_weight_caps(candidates, max_weight_per_stock, max_weight_per_sector)
    return candidates[PORTFOLIO_COLUMNS].reset_index(drop=True)


def build_steady_growth_portfolio(
    result: pd.DataFrame,
    max_positions: int = 15,
    max_weight_per_stock: float = 0.15,
    max_weight_per_sector: float = 0.30,
    min_avg_volume: float | None = None,
    require_debt_health: bool = True,
    require_expense_efficiency: bool = True,
) -> pd.DataFrame:
    """Top-market-cap, revenue-consistent portfolio — independent of the
    buy-tier/quality-score signal system entirely. For "boring, big,
    reliably-growing" names rather than statistically-cheap ones.

    Requires `result` to have market_cap and revenue_consistency_ok columns
    (see pipeline.run_pipeline(use_revenue_consistency=True)). Selection:
    only revenue_consistency_ok == True names — and, unless turned off,
    only debt_health_ok == True (revenue can grow while a company quietly
    loads up on debt, e.g. bond issuance funding growth rather than
    operations) and expense_efficiency_ok == True (revenue can also grow
    while its own cost base eats an ever-larger share of it) — ranked by
    market_cap descending, top `max_positions`. Weighted by market_cap
    (bigger companies get proportionally more weight), then the same
    per-stock/per-sector cap-and-redistribute as build_portfolio.

    `rally_support`/`rally_support_reason` (from pipeline's rally_support
    column, when present) are informational only — not a selection filter
    — flagging whether a name's own past price rally looks backed by its
    own revenue growth or looks like it outran it (버블성 상승 우려).
    """
    candidates = result[result["revenue_consistency_ok"] == True].copy()  # noqa: E712
    if require_debt_health:
        candidates = candidates[candidates["debt_health_ok"] == True]  # noqa: E712
    if require_expense_efficiency:
        candidates = candidates[candidates["expense_efficiency_ok"] == True]  # noqa: E712
    if "debt_health_reason" not in candidates:
        candidates["debt_health_reason"] = pd.NA
    if "expense_efficiency_reason" not in candidates:
        candidates["expense_efficiency_reason"] = pd.NA
    if "entry_zone" not in candidates:
        candidates["entry_zone"] = pd.NA
    if "entry_zone_detail" not in candidates:
        candidates["entry_zone_detail"] = pd.NA
    if "rally_support" not in candidates:
        candidates["rally_support"] = pd.NA
    if "rally_support_reason" not in candidates:
        candidates["rally_support_reason"] = pd.NA
    if min_avg_volume is not None:
        candidates = candidates[candidates["avg_volume"] >= min_avg_volume]
    candidates = candidates.dropna(subset=["market_cap", "sector"])
    candidates = candidates[candidates["market_cap"] > 0]
    if candidates.empty:
        return pd.DataFrame(columns=STEADY_GROWTH_COLUMNS)

    candidates = candidates.sort_values("market_cap", ascending=False)
    candidates = candidates.head(max_positions).copy().reset_index(drop=True)

    candidates["weight"] = candidates["market_cap"] / candidates["market_cap"].sum()
    candidates = _apply_weight_caps(candidates, max_weight_per_stock, max_weight_per_sector)
    return candidates[STEADY_GROWTH_COLUMNS].reset_index(drop=True)
