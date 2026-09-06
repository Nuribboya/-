import numpy as np
import pandas as pd
import pytest

from stock_valuation.rl_agent import recommend_action, train_q_learning
from stock_valuation.rl_env import ACTIONS, BuyTimingEnv, Episode, build_episodes


def _episode(prices: list[float], quality: float = 0.5, cheap: float = 0.2) -> Episode:
    n = len(prices)
    return Episode(
        ticker="AAA",
        quality_score=np.full(n, quality),
        cheapness_percentile=np.full(n, cheap),
        price=np.array(prices, dtype=float),
    )


def test_hold_action_gives_zero_reward_regardless_of_price_move():
    env = BuyTimingEnv(_episode([100.0, 130.0]))
    env.reset()
    _, reward, done = env.step(0)  # ACTIONS[0] == 0.0 (invest nothing)
    assert reward == pytest.approx(0.0)
    assert done is True


def test_full_invest_reward_matches_price_growth():
    env = BuyTimingEnv(_episode([100.0, 110.0]))
    env.reset()
    _, reward, _ = env.step(3)  # ACTIONS[3] == 1.0 (invest all cash)
    assert reward == pytest.approx(0.10)


def test_partial_invest_scales_reward_by_invested_fraction():
    env = BuyTimingEnv(_episode([100.0, 110.0]))
    env.reset()
    _, reward, _ = env.step(1)  # ACTIONS[1] == 0.25
    assert reward == pytest.approx(0.25 * 0.10)


def test_position_keeps_growing_across_steps_without_new_investment():
    env = BuyTimingEnv(_episode([100.0, 110.0, 121.0]))
    env.reset()
    env.step(3)  # invest fully at t=0
    _, reward, done = env.step(0)  # hold — no new cash, existing position still grows
    # total was 1.10 after step 1; another +10% price move grows it to 1.21
    assert reward == pytest.approx(0.11)
    assert done is True


def test_done_flag_only_true_on_final_transition():
    env = BuyTimingEnv(_episode([100.0, 105.0, 110.0]))
    env.reset()
    _, _, done1 = env.step(0)
    assert done1 is False
    _, _, done2 = env.step(0)
    assert done2 is True


def test_build_episodes_drops_short_and_nan_series_and_sorts_by_period():
    history = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA", "AAA", "BBB"],
            "period": pd.to_datetime(["2022-06-30", "2022-03-31", "2022-09-30", "2022-03-31"]),
            "quality_score": [0.5, 0.6, 0.7, np.nan],
            "cheapness_percentile": [0.2, 0.3, 0.1, 0.5],
            "price": [110.0, 100.0, 120.0, 50.0],
        }
    )
    episodes = build_episodes(history)
    assert len(episodes) == 1  # BBB dropped: only 1 valid row after dropping its NaN quality_score
    assert episodes[0].ticker == "AAA"
    assert episodes[0].price.tolist() == [100.0, 110.0, 120.0]  # sorted by period, not input order


def test_q_learning_learns_to_invest_in_rising_state_and_hold_in_falling_state():
    good_episode = _episode([100.0, 120.0], quality=0.8, cheap=0.05)  # +20%, high quality, cheap
    bad_episode = _episode([100.0, 90.0], quality=0.1, cheap=0.5)  # -10%, low quality, expensive

    good_state = BuyTimingEnv(good_episode).reset()
    bad_state = BuyTimingEnv(bad_episode).reset()
    assert good_state != bad_state  # sanity: discretization actually separates these

    q_table = train_q_learning([good_episode, bad_episode], n_epochs=300, epsilon=0.3, seed=0)

    assert recommend_action(q_table, good_state) == max(ACTIONS)
    assert recommend_action(q_table, bad_state) == min(ACTIONS)
