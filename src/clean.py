"""Build the clean_311 table from raw_311 and report what each rule did.

Run:  python src/clean.py

Steps: read raw_311 -> apply the rules in cleaning.py -> save
data/processed/clean_311.csv -> COPY it into clean_311 -> save the rule
counts in the cleaning_report table.
"""
import pandas as pd
import psycopg

import cleaning
import config

SCHEMA_FILE = config.SQL_DIR / "schema" / "02_clean_table.sql"
CLEAN_CSV = config.PROCESSED_DIR / "clean_311.csv"

RAW_COLUMNS = [
    "unique_key", "created_date", "closed_date", "agency", "agency_name",
    "complaint_type", "descriptor", "borough", "incident_zip", "status",
    "open_data_channel_type", "resolution_action_updated_date",
    "latitude", "longitude",
]

CLEAN_COLUMNS = [
    "unique_key", "created_at", "closed_at", "agency", "agency_name",
    "complaint_type", "descriptor", "borough", "incident_zip", "status",
    "open_data_channel_type", "latitude", "longitude", "resolution_status",
    "resolution_hours", "is_zero_duration", "is_extreme", "is_borough_known",
    "season", "created_month",
]


def main():
    report = []  # (rule, rows_affected, action)

    def log(rule, rows, action):
        report.append((rule, int(rows), action))
        print(f"{rule:55} {int(rows):>10,}   -> {action}")

    print("Reading raw_311 ...")
    df = pd.read_sql(f"SELECT {', '.join(RAW_COLUMNS)} FROM raw_311", config.get_engine())
    log("rows in raw_311", len(df), "starting point")

    df = cleaning.blank_to_missing(df)
    df = cleaning.parse_timestamps(df)

    # A request with no usable created date can't be placed in time at all.
    bad_created = df["created_at"].isna()
    log("created_date missing or unparseable", bad_created.sum(), "dropped")
    df = df[~bad_created]

    # A close date that's present but unreadable becomes NaT, so it would be
    # treated as 'no close date'. Count it separately so it isn't hidden.
    bad_closed = df["closed_date"].notna() & df["closed_at"].isna()
    log("closed_date present but unparseable", bad_closed.sum(), "treated as no close date")

    df, n_dupes = cleaning.drop_duplicate_keys(df)
    log("duplicate unique_key rows", n_dupes, "dropped (kept latest update)")

    df = cleaning.classify_resolution(df, now=pd.Timestamp.now())
    status = df["resolution_status"]
    no_close = status == "no_close_date"
    still_open = no_close & (df["status"].fillna("") != "Closed")
    log("closed_date missing: status not Closed (still open)", still_open.sum(),
        "kept, excluded from resolution time")
    log("closed_date missing: status says Closed", (no_close & ~still_open).sum(),
        "kept, excluded from resolution time")
    log("closed_date before created_date", (status == "closed_before_created").sum(),
        "kept, excluded from resolution time")
    log("placeholder closed_date (<2000 or in the future)", (status == "placeholder_date").sum(),
        "kept, excluded from resolution time")
    log("has closed_date but status is not Closed",
        ((status == "closed") & (df["status"].fillna("") != "Closed")).sum(),
        "kept, timed from closed_date")
    log("valid resolution time (resolution_status = closed)", (status == "closed").sum(),
        "used for resolution time analysis")
    log("closed in 0 seconds (is_zero_duration)", df["is_zero_duration"].sum(), "kept, flagged")

    df = cleaning.clean_borough(df)
    log("borough missing or Unspecified", (~df["is_borough_known"]).sum(),
        "kept as UNSPECIFIED, excluded from borough analysis")

    df = cleaning.add_season(df)

    df = cleaning.flag_extreme(df)
    log("extreme resolution time (> Q3 + 3*IQR for its type)", df["is_extreme"].sum(),
        "kept, flagged")

    log("rows in clean_311", len(df), "final")

    # Final types for the database
    df["unique_key"] = df["unique_key"].astype("int64")  # fails loudly if a key isn't a number
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df[CLEAN_COLUMNS].to_csv(CLEAN_CSV, index=False)
    print(f"\nSaved {CLEAN_CSV.name}. Loading into clean_311 ...")

    with psycopg.connect(config.get_database_url()) as conn:
        conn.execute(SCHEMA_FILE.read_text())
        with conn.cursor() as cur:
            copy_sql = (f"COPY clean_311 ({', '.join(CLEAN_COLUMNS)}) "
                        "FROM STDIN WITH (FORMAT csv, HEADER true)")
            with cur.copy(copy_sql) as copy, open(CLEAN_CSV, "rb") as f:
                while block := f.read(1024 * 1024):
                    copy.write(block)
            cur.executemany(
                "INSERT INTO cleaning_report (step, rule, rows_affected, action) "
                "VALUES (%s, %s, %s, %s)",
                [(i, *row) for i, row in enumerate(report, start=1)],
            )
        loaded = conn.execute("SELECT COUNT(*) FROM clean_311").fetchone()[0]

    print(f"clean_311 rows in database: {loaded:,}")
    print("Season counts:", df["season"].value_counts().to_dict())


if __name__ == "__main__":
    main()
