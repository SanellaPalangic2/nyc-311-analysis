"""Export tidy CSVs for the Power BI dashboard.

Run:  python src/export_powerbi.py

Writes to outputs/powerbi/:
  fact_requests.csv        one row per request (the table Power BI calculates from)
  dim_agency.csv           agency code -> name
  dim_complaint_type.csv   complaint type, main agency, volume, 1,000+ flag
  dim_borough.csv          borough, sort order, known/unknown flag
  dim_season.csv           season, calendar sort order
  summary_*.csv            the SQL analysis results, used as an answer key to
                           check the Power BI measures against
  cleaning_report.csv      rows affected by each cleaning rule

Why a request-level fact table instead of only summary tables: medians and
percentiles can't be combined. The median of five borough medians is NOT the
citywide median. If Power BI only had pre-computed medians, any slicer
combination (say Bronx + Summer) would have to average medians and would
show wrong numbers. With one row per request, Power BI computes the real
median for whatever is filtered.
"""
import psycopg

import config
from run_analysis import ANALYSIS_DIR, create_view, run_query_file

# Flags are exported as 1/0 instead of true/false so Power BI can SUM them
# (e.g. SUM(closed_within_30d) = number closed within 30 days).
QUERIES = {
    "fact_requests": """
        SELECT
            unique_key,
            created_at::date                         AS created_date,
            created_month,
            season,
            agency,
            CASE WHEN agency = 'NYPD' THEN 'NYPD' ELSE 'Other agencies' END AS agency_group,
            complaint_type,
            borough,
            resolution_status,
            ROUND(resolution_hours::numeric, 4)      AS resolution_hours,
            (resolution_status = 'no_close_date')::int AS has_no_close_date,
            outcome_30d_known::int                   AS outcome_30d_known,
            closed_within_30d::int                   AS closed_within_30d,
            is_extreme::int                          AS is_extreme,
            is_zero_duration::int                    AS is_zero_duration
        FROM v_requests
        ORDER BY unique_key
    """,
    "dim_agency": """
        SELECT agency, mode() WITHIN GROUP (ORDER BY agency_name) AS agency_name
        FROM clean_311
        GROUP BY agency
        ORDER BY agency
    """,
    "dim_complaint_type": """
        SELECT
            complaint_type,
            mode() WITHIN GROUP (ORDER BY agency)    AS main_agency,
            COUNT(*)                                 AS n_requests,
            (COUNT(*) >= 1000)::int                  AS has_1000_plus,
            -- Same top 10 as 06_complaint_type_by_borough.sql (ROW_NUMBER, like its LIMIT 10)
            (ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) <= 10)::int AS is_top10_volume
        FROM clean_311
        GROUP BY complaint_type
        ORDER BY complaint_type
    """,
    "dim_borough": """
        SELECT
            borough,
            CASE borough
                WHEN 'BRONX' THEN 1 WHEN 'BROOKLYN' THEN 2 WHEN 'MANHATTAN' THEN 3
                WHEN 'QUEENS' THEN 4 WHEN 'STATEN ISLAND' THEN 5 ELSE 6
            END                                      AS borough_order,
            (borough <> 'UNSPECIFIED')::int          AS is_known
        FROM clean_311
        GROUP BY borough
        ORDER BY borough_order
    """,
    "dim_season": """
        SELECT * FROM (VALUES
            ('Winter', 1, 'Dec, Jan, Feb'),
            ('Spring', 2, 'Mar, Apr, May'),
            ('Summer', 3, 'Jun, Jul, Aug'),
            ('Fall',   4, 'Sep, Oct, Nov')
        ) AS s(season, season_order, months)
    """,
    "cleaning_report": "SELECT * FROM cleaning_report ORDER BY step",
}

# SQL analysis files exported as answer-key tables
SUMMARIES = {
    "00": "summary_overview",
    "02": "summary_by_agency",
    "03": "summary_by_borough",
    "04": "summary_by_season",
    "05": "summary_slowest_types",
    "06": "summary_type_by_borough",
    "07": "summary_season_mix",
}


def export_query(conn, sql, path):
    """Stream a query result straight to a CSV file with COPY ... TO STDOUT.

    This is the reverse of the COPY used to load data: Postgres writes the CSV
    itself, which is far faster than fetching 3.6M rows into Python first.
    """
    copy_sql = f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER true)"
    with conn.cursor() as cur, cur.copy(copy_sql) as copy, open(path, "wb") as f:
        for block in copy:
            f.write(block)


def count_data_rows(path):
    with open(path, "rb") as f:
        return sum(1 for _ in f) - 1  # no embedded newlines in these columns


def main():
    config.POWERBI_DIR.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(config.get_database_url()) as conn:
        create_view(conn)

        for name, sql in QUERIES.items():
            path = config.POWERBI_DIR / f"{name}.csv"
            export_query(conn, sql, path)
            size_mb = path.stat().st_size / 1_000_000
            print(f"{path.name:32} {count_data_rows(path):>10,} rows  {size_mb:8.1f} MB")

        for prefix, name in SUMMARIES.items():
            sql_file = next(ANALYSIS_DIR.glob(f"{prefix}_*.sql"))
            path = config.POWERBI_DIR / f"{name}.csv"
            run_query_file(sql_file, conn).to_csv(path, index=False)
            print(f"{path.name:32} {count_data_rows(path):>10,} rows  (from {sql_file.name})")


if __name__ == "__main__":
    main()
