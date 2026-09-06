from __future__ import annotations

import pandas as pd

from stock_valuation.valuation import BUY_TIERS, NO_SIGNAL

PORTFOLIO_COLUMNS = ["ticker", "sector", "buy_tier", "quality_score", "weight"]
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


def build_portfolio(
    result: pd.DataFrame,
    max_positions: int = 15,
    max_weight_per_stock: float = 0.15,
    max_weight_per_sector: float = 0.30,
) -> pd.DataFrame:
    """Turn the scored/ranked screener output into a weighted candidate
    portfolio — diversification rules, not mean-variance optimization
    (this project has no return-covariance model to optimize against).

    Only names that already cleared a buy signal (not 관망/NO_SIGNAL) are
    eligible. Weighted by quality_score, ranked by buy tier strength first,
    then capped per-stock and per-sector by iteratively capping and
    redistributing the excess to everyone still under their cap.

    The per-stock cap alone requires max_positions * max_weight_per_stock
    >= 1.0 to be satisfiable at all (e.g. 15 positions need >= ~6.7% cap
    each) — with too few eligible candidates or too tight a cap, weights
    will still sum to 1.0 but individual caps may not all hold, since
    there's no feasible allocation that respects them.
    """
    candidates = result[result["buy_tier"] != NO_SIGNAL].copy()
    candidates = candidates.dropna(subset=["quality_score", "sector"])
    candidates = candidates[candidates["quality_score"] > 0]
    if candidates.empty:
        return pd.DataFrame(columns=PORTFOLIO_COLUMNS)

    tier_rank = {tier: i for i, (_, tier) in enumerate(BUY_TIERS)}  # 0 = strongest tier
    candidates["_tier_rank"] = candidates["buy_tier"].map(tier_rank).fillna(len(BUY_TIERS))
    candidates = candidates.sort_values(["_tier_rank", "quality_score"], ascending=[True, False])
    candidates = candidates.head(max_positions).copy().reset_index(drop=True)

    candidates["weight"] = candidates["quality_score"] / candidates["quality_score"].sum()

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
    return candidates[PORTFOLIO_COLUMNS].reset_index(drop=True)
