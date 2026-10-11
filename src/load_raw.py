"""Bulk-load the downloaded CSV pages into the raw_311 table with COPY.

Run:  python src/load_raw.py

Rebuilds raw_311 from scratch each run, so running it twice gives the same
result (no doubled rows).
"""
import psycopg

import config
from download import COLUMNS

SCHEMA_FILE = config.SQL_DIR / "schema" / "01_raw_table.sql"


def main():
    pages = sorted(config.RAW_DIR.glob("day_*.csv"))
    if not pages:
        raise SystemExit("No page files in data/raw. Run src/download.py first.")

    with psycopg.connect(config.get_database_url()) as conn:
        conn.execute(SCHEMA_FILE.read_text())

        column_list = ", ".join(COLUMNS)
        # COPY ... FROM STDIN streams the file to the server in one command.
        # It's much faster than INSERTing row by row because Postgres
        # parses the CSV itself without a round trip for every row.
        # HEADER true tells it to skip the header line of each file.
        copy_sql = f"COPY raw_311 ({column_list}) FROM STDIN WITH (FORMAT csv, HEADER true)"

        with conn.cursor() as cur:
            for path in pages:
                with cur.copy(copy_sql) as copy, open(path, "rb") as f:
                    while block := f.read(1024 * 1024):  # 1 MB at a time
                        copy.write(block)
                # After a COPY, rowcount holds the number of rows it loaded.
                print(f"loaded {path.name}: {cur.rowcount:,} rows")

        # Commit happens when the 'with connect' block exits without an error,
        # so a failure partway leaves the database untouched (all or nothing).

        total, distinct_keys = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT unique_key) FROM raw_311"
        ).fetchone()
        min_created, max_created = conn.execute(
            "SELECT MIN(created_date), MAX(created_date) FROM raw_311"
        ).fetchone()

    expected_file = config.RAW_DIR / "expected_count.txt"
    expected = int(expected_file.read_text()) if expected_file.exists() else None

    print("\n=== raw_311 load report ===")
    print(f"page files loaded      : {len(pages)}")
    print(f"rows in raw_311        : {total:,}")
    print(f"distinct unique_keys   : {distinct_keys:,}")
    print(f"duplicate rows         : {total - distinct_keys:,}")
    if expected is not None:
        print(f"API count at download  : {expected:,}  (difference {total - expected:+,})")
    print(f"created_date range     : {min_created}  to  {max_created}")


if __name__ == "__main__":
    main()
