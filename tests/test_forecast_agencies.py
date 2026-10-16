"""Tests for the per-agency model choice and staffing statistics in forecast/agencies.py."""
import pandas as pd
import pytest

from agencies import choose_on_validation, staffing_stats


def test_choice_uses_validation_only():
    table = pd.DataFrame({
        "variant": "as_recorded", "series": "HPD",
        "split": ["validation", "validation", "test", "test"],
        "model": ["a", "b", "a", "b"],
        "mae": [10.0, 20.0, 99.0, 1.0],   # b is far better on test, but must not be chosen
    })
    assert choose_on_validation(table) == {("as_recorded", "HPD"): "a"}


def test_staffing_stats_compare_forecast_and_p90_plans():
    rows = pd.DataFrame({"actual": [100.0, 120.0, 90.0, 150.0],
                         "forecast": [110.0, 110.0, 110.0, 110.0],
                         "hi": [132.0, 132.0, 132.0, 132.0]})
    s = staffing_stats(rows)
    # plan = forecast: short on 120 and 150 (2 of 4 days), short by 10 and 40
    assert s["forecast"]["short_days_pct"] == 50
    assert s["forecast"]["avg_shortfall"] == pytest.approx(25)
    assert s["forecast"]["avg_unused"] == pytest.approx(15)       # (10 + 20) / 2
    # plan = P90: short only on 150, by 18; spare 32, 12, 42 on the others
    assert s["p90"]["short_days_pct"] == 25
    assert s["p90"]["avg_shortfall"] == pytest.approx(18)
    assert s["p90"]["avg_unused"] == pytest.approx((32 + 12 + 42) / 3)
    assert s["buffer_pct"] == pytest.approx(20)
