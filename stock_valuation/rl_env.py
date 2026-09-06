from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Fraction of *remaining* cash to invest this period.
ACTIONS = (0.0, 0.25, 0.5, 1.0)

# Bin edges used to discretize continuous state into a small tabular state
# space — with only a few hundred historical (ticker, period) rows to learn
# from, a discretized tabular Q-table is the right scale; a deep RL agent
# would be wildly overparameterized for this much data.
QUALITY_BINS = [0.3, 0.5, 0.7]
CHEAP_BINS = [0.1, 0.25, 0.4]
CASH_BINS = [0.0, 0.34, 0.67, 1.0]


def discretize(value: float, bin_edges: list[float]) -> int:
    return int(np.digitize([value], bin_edges)[0])


def state_space_shape() -> tuple[int, int, int]:
    return (len(QUALITY_BINS) + 1, len(CHEAP_BINS) + 1, len(CASH_BINS) + 1)


@dataclass
class Episode:
    ticker: str
    quality_score: np.ndarray
    cheapness_percentile: np.ndarray
    price: np.ndarray


def build_episodes(history: pd.DataFrame) -> list[Episode]:
    """One Episode per ticker from a (ticker, period, quality_score,
    cheapness_percentile, price) history, sorted by period.

    Drops tickers with fewer than 2 usable periods (nothing to act on, since
    a step needs a "now" and a "next" price) or any NaN state/price.
    """
    episodes = []
    for ticker, group in history.groupby("ticker"):
        g = group.sort_values("period").dropna(subset=["quality_score", "cheapness_percentile", "price"])
        if len(g) < 2:
            continue
        episodes.append(
            Episode(
                ticker=ticker,
                quality_score=g["quality_score"].to_numpy(dtype=float),
                cheapness_percentile=g["cheapness_percentile"].to_numpy(dtype=float),
                price=g["price"].to_numpy(dtype=float),
            )
        )
    return episodes


class BuyTimingEnv:
    """One ticker's period sequence as an episodic staged-buy environment.

    State: (quality bucket, cheapness bucket, cash-remaining bucket).
    Action: fraction of *remaining* cash to invest this period (ACTIONS).
    Reward: change in total portfolio value (cash + position, in units of
    starting capital) over the step — so it compounds the realized price
    move on whatever fraction actually got invested, rewarding good timing
    over simply "always buy everything immediately".

    Cash itself earns no return here (no risk-free rate modeled) — this
    keeps the environment focused purely on entry timing/sizing.
    """

    def __init__(self, episode: Episode):
        self.episode = episode
        self.t = 0
        self.cash = 1.0
        self.position_value = 0.0

    def reset(self) -> tuple[int, int, int]:
        self.t = 0
        self.cash = 1.0
        self.position_value = 0.0
        return self._state()

    def _state(self) -> tuple[int, int, int]:
        q = discretize(float(self.episode.quality_score[self.t]), QUALITY_BINS)
        c = discretize(float(self.episode.cheapness_percentile[self.t]), CHEAP_BINS)
        cash_bucket = discretize(self.cash, CASH_BINS)
        return (q, c, cash_bucket)

    def step(self, action_idx: int) -> tuple[tuple[int, int, int], float, bool]:
        total_before = self.cash + self.position_value

        invest = self.cash * ACTIONS[action_idx]
        self.cash -= invest
        self.position_value += invest

        price_now = self.episode.price[self.t]
        price_next = self.episode.price[self.t + 1]
        self.position_value *= price_next / price_now

        self.t += 1
        done = self.t == len(self.episode.price) - 1
        reward = (self.cash + self.position_value) - total_before
        return self._state(), reward, done
