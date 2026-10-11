"""Tests for the effect size and bootstrap helpers in analysis/stats_tests.py."""
import numpy as np
from scipy.stats import kruskal

from stats_tests import bootstrap_median_ci, epsilon_squared


def test_epsilon_squared_is_zero_for_identical_groups_and_large_for_separated():
    same = [np.arange(1, 101), np.arange(1, 101)]
    apart = [np.arange(1, 101), np.arange(1001, 1101)]
    eps_same = epsilon_squared(kruskal(*same).statistic, 200)
    eps_apart = epsilon_squared(kruskal(*apart).statistic, 200)
    assert eps_same == 0
    assert 0.7 < eps_apart <= 1  # groups don't overlap at all -> most variation explained


def test_epsilon_squared_formula_matches_textbook_form():
    h, n = 12.5, 400
    assert np.isclose(epsilon_squared(h, n), h / ((n**2 - 1) / (n + 1)))


def test_bootstrap_ci_contains_median_and_is_reproducible():
    values = np.random.default_rng(0).lognormal(0, 1, 5_000)
    ci_1 = bootstrap_median_ci(values, 200, np.random.default_rng(42))
    ci_2 = bootstrap_median_ci(values, 200, np.random.default_rng(42))
    assert ci_1 == ci_2                                # same seed -> same interval
    assert ci_1[0] < np.median(values) < ci_1[1]


def test_bootstrap_ci_of_constant_values_has_zero_width():
    assert bootstrap_median_ci(np.full(100, 7.0), 50, np.random.default_rng(1)) == (7.0, 7.0)
