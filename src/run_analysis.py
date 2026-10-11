"""Run the analysis SQL files and print each result.

Run:  python src/run_analysis.py            (all files in sql/analysis)
      python src/run_analysis.py 03         (only files whose name starts with 03)

Recreates the v_requests view first, since rebuilding clean_311 drops it.
The SQL files are plain SQL, so you can also paste them into psql or pgAdmin.
"""
import sys

import pandas as pd
import psycopg

import config

VIEW_FILE = config.SQL_DIR / "schema" / "03_analysis_view.sql"
ANALYSIS_DIR = config.SQL_DIR / "analysis"


def create_view(conn):
    conn.execute(VIEW_FILE.read_text())
    conn.commit()


def run_query_file(path, conn):
    """Return the result of one .sql file as a DataFrame.

    Runs the file through psycopg with no parameters. That matters: drivers
    treat '%' as a parameter placeholder when parameters are involved (which
    is how pandas.read_sql sends queries), and the SQL comments contain '%'.
    """
    cur = conn.execute(path.read_text())
    columns = [col.name for col in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns)


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else ""

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.max_rows", 300)

    with psycopg.connect(config.get_database_url()) as conn:
        create_view(conn)
        for path in sorted(ANALYSIS_DIR.glob(f"{prefix}*.sql")):
            print(f"\n===== {path.name} =====")
            print(run_query_file(path, conn).to_string(index=False))


if __name__ == "__main__":
    main()
