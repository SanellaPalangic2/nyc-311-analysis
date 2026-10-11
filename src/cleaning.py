"""Cleaning rules, written as small functions so each one can be tested alone.

Every function takes a DataFrame and returns a new DataFrame. None of them
read from or write to the database; that's clean.py's job. Keeping them pure
is what makes the pytest tests in tests/test_cleaning.py simple.

Resolution status categories (column resolution_status):
    closed                 has a valid closed_date -> resolution_hours computed
    no_close_date          closed_date missing (mostly requests still open)
    closed_before_created  closed_date earlier than created_date (bad data)
    placeholder_date       closed_date before 2000 (e.g. 1900-01-01) or in the future
Only 'closed' rows get a resolution_hours value; the others stay in the table
so they can be counted, but they are left out of time calculations.
"""
import pandas as pd

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"  # e.g. 2025-10-31T09:28:03.000
PLACEHOLDER_CUTOFF = pd.Timestamp("2000-01-01")

SEASONS = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer",
    9: "Fall", 10: "Fall", 11: "Fall",
}


def blank_to_missing(df):
    """Turn empty or whitespace-only strings into real missing values (NA).

    The API's CSV writes missing values as "", which pandas would otherwise
    treat as a normal piece of text.
    """
    df = df.copy()
    text_cols = df.select_dtypes(include=["object", "string"]).columns
    for col in text_cols:
        df[col] = df[col].str.strip().replace("", pd.NA)
    return df


def parse_timestamps(df):
    """Add created_at and closed_at as real datetimes.

    errors="coerce" turns anything unparseable into NaT (missing) instead of
    crashing, so bad values can be counted afterwards.
    """
    df = df.copy()
    df["created_at"] = pd.to_datetime(df["created_date"], format=TIMESTAMP_FORMAT, errors="coerce")
    df["closed_at"] = pd.to_datetime(df["closed_date"], format=TIMESTAMP_FORMAT, errors="coerce")
    return df


def drop_duplicate_keys(df):
    """Keep one row per unique_key.

    If a key appears more than once, keep the version with the most recent
    resolution_action_updated_date, since that's the latest state of the
    request. Returns (clean_df, number_of_rows_dropped).
    """
    ordered = df.sort_values("resolution_action_updated_date", ascending=False, na_position="last")
    deduped = ordered.drop_duplicates(subset="unique_key", keep="first")
    return deduped.sort_index(), len(df) - len(deduped)


def classify_resolution(df, now):
    """Add resolution_status and resolution_hours (see module docstring).

    `now` is passed in instead of read from the clock so tests are
    repeatable: any closed_at after `now` is treated as a placeholder.
    """
    df = df.copy()
    status = pd.Series("closed", index=df.index)
    status[df["closed_at"].isna()] = "no_close_date"
    status[df["closed_at"] < df["created_at"]] = "closed_before_created"
    # Checked last, so a 1900-01-01 close date (also "before created") ends
    # up labeled placeholder, which is the more specific reason.
    status[(df["closed_at"] < PLACEHOLDER_CUTOFF) | (df["closed_at"] > now)] = "placeholder_date"
    df["resolution_status"] = status

    hours = (df["closed_at"] - df["created_at"]).dt.total_seconds() / 3600
    df["resolution_hours"] = hours.where(status == "closed")
    df["is_zero_duration"] = df["resolution_hours"] == 0
    return df


def clean_borough(df):
    """Standardize borough. Missing or 'Unspecified' becomes 'UNSPECIFIED'.

    These rows are kept (they still count for complaint-type and agency
    analysis) and is_borough_known lets borough queries leave them out.
    """
    df = df.copy()
    borough = df["borough"].str.upper().fillna("UNSPECIFIED")
    df["borough"] = borough
    df["is_borough_known"] = borough != "UNSPECIFIED"
    return df


def add_season(df):
    """Add season and month from created_at, using meteorological seasons:
    Winter = Dec-Feb, Spring = Mar-May, Summer = Jun-Aug, Fall = Sep-Nov.
    """
    df = df.copy()
    df["created_month"] = df["created_at"].dt.month
    df["season"] = df["created_month"].map(SEASONS)
    return df


def flag_extreme(df, k=3.0):
    """Flag extreme resolution times WITHOUT removing them (is_extreme).

    Uses Tukey's "far out" fence, Q3 + 3 * IQR, computed separately for each
    complaint type, because what is extreme depends on the problem: 30 days is
    normal for some repairs and extreme for a noise complaint.
    """
    df = df.copy()
    by_type = df.groupby("complaint_type")["resolution_hours"]
    q1 = by_type.transform(lambda s: s.quantile(0.25))
    q3 = by_type.transform(lambda s: s.quantile(0.75))
    fence = q3 + k * (q3 - q1)
    # Comparisons with a missing value are False, so rows without a
    # resolution time are never flagged.
    df["is_extreme"] = (df["resolution_hours"] > fence).fillna(False)
    return df
