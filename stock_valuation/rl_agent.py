from __future__ import annotations

import random

import numpy as np

from stock_valuation.rl_env import ACTIONS, BuyTimingEnv, Episode, state_space_shape


def train_q_learning(
    episodes: list[Episode],
    n_epochs: int = 200,
    alpha: float = 0.1,
    gamma: float = 0.95,
    epsilon: float = 0.2,
    seed: int = 42,
) -> np.ndarray:
    """Tabular Q-learning, replaying the given historical episodes.

    Each epoch replays every episode once, epsilon-greedily. This is
    training on historical (in-sample) trajectories — the learned policy is
    a backtest-fit, not a validated forward-looking strategy; see the
    caveats in stock_valuation/README.md before treating its output as
    anything more than an experiment.
    """
    rng = random.Random(seed)
    q_table = np.zeros(state_space_shape() + (len(ACTIONS),))

    for _ in range(n_epochs):
        for episode in episodes:
            env = BuyTimingEnv(episode)
            state = env.reset()
            done = False
            while not done:
                if rng.random() < epsilon:
                    action = rng.randrange(len(ACTIONS))
                else:
                    action = int(np.argmax(q_table[state]))

                next_state, reward, done = env.step(action)
                best_next = 0.0 if done else float(np.max(q_table[next_state]))
                td_target = reward + gamma * best_next
                q_table[state][action] += alpha * (td_target - q_table[state][action])
                state = next_state

    return q_table


def recommend_action(q_table: np.ndarray, state: tuple[int, int, int]) -> float:
    """Best learned action (fraction of remaining cash to invest) for a state."""
    return ACTIONS[int(np.argmax(q_table[state]))]
