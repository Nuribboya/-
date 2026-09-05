from mathgrader.solvers.combinatorics import combination_steps, permutation_steps


def test_permutation():
    _, value = permutation_steps("n=5, r=2")
    assert value == 20


def test_permutation_bare_args():
    _, value = permutation_steps("5, 2")
    assert value == 20


def test_combination():
    _, value = combination_steps("n=5, r=2")
    assert value == 10
