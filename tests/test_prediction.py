"""Tests for the target and feature builders in analysis/prediction.py."""
import pandas as pd

from prediction import FORBIDDEN, build_target, creation_features


def requests():
    return pd.DataFrame({
        "created_at": pd.to_datetime(["2025-03-03 09:15"] * 5),
        "agency": ["NYPD"] * 5, "complaint_type": ["Noise"] * 5, "borough": ["BRONX"] * 5,
        "open_data_channel_type": ["PHONE"] * 5,
        "resolution_status": ["closed", "closed", "no_close_date", "no_close_date",
                              "closed_before_created"],
        "status": ["Closed", "Closed", "Open", "Closed", "Closed"],
        "resolution_hours": [168.0, 168.5, None, None, None],
        "closed_at": pd.to_datetime(["2025-03-10", "2025-03-11", None, None, "2025-03-01"]),
    })


def test_target_labels():
    y = build_target(requests())
    # exactly 7 days counts as within; still open = not within; unknown outcomes = NaN
    assert y.iloc[0] == 1 and y.iloc[1] == 0 and y.iloc[2] == 0
    assert y.iloc[3:].isna().all()


def test_features_are_known_at_creation_only():
    X = creation_features(requests())
    assert list(X.columns) == ["agency", "complaint_type", "borough", "channel",
                               "month", "day_of_week", "hour"]
    assert not FORBIDDEN & set(X.columns)
    assert X.loc[0, ["month", "day_of_week", "hour"]].tolist() == [3, 0, 9]  # March, Monday, 9am
