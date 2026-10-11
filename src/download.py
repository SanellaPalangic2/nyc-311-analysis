"""Download 2025 NYC 311 requests from the Socrata API, one CSV file per page.

Run:  python src/download.py

How it works
- Walks through 2025 one day at a time. For each day it asks the API for up
  to 50,000 rows ($limit), filtered to that day with $where, sorted by
  unique_key with $order, and moves on with $offset if the day has more.
- Why one day at a time: tested on the live API, a 50,000-row page sorted by
  unique_key across the whole year timed out (>5 min), and so did a deep
  $offset. The same request limited to one day (~10,000 rows) came back in
  about 13 seconds. Small windows keep the server's sort small.
- Each page is saved as data/raw/day_2025-03-10_000.csv (day, page number).
- Resumable: if the script dies halfway, run it again. Pages already on disk
  are skipped, and it picks up at the first missing one.
"""
import csv
import time
from datetime import date, timedelta

import requests

import config

CSV_URL = config.API_URL.replace(".json", ".csv")
PAGE_SIZE = 50_000

# Only the columns the analysis needs. Leaving out long free-text fields like
# resolution_description keeps the download much smaller.
COLUMNS = [
    "unique_key",
    "created_date",
    "closed_date",
    "agency",
    "agency_name",
    "complaint_type",
    "descriptor",
    "location_type",
    "incident_zip",
    "city",
    "borough",
    "status",
    "resolution_action_updated_date",
    "community_board",
    "open_data_channel_type",
    "latitude",
    "longitude",
]

WHERE = (
    f"created_date >= '{config.START_DATE}' "
    f"AND created_date < '{config.END_DATE}'"
)


def get_with_retries(params, max_attempts=5):
    """GET the API, waiting longer after each failure (exponential backoff)."""
    headers = {}
    if config.SOCRATA_APP_TOKEN:
        headers["X-App-Token"] = config.SOCRATA_APP_TOKEN

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(CSV_URL, params=params, headers=headers, timeout=300)
            # 429 = rate limited, 5xx = server trouble: both are worth retrying.
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"HTTP {response.status_code}")
            response.raise_for_status()  # other errors (e.g. 400 bad query) fail right away
            return response
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as err:
            if attempt == max_attempts:
                raise
            wait = 2 ** attempt  # 2, 4, 8, 16 seconds
            print(f"  attempt {attempt} failed ({err}); retrying in {wait}s")
            time.sleep(wait)


def count_rows(path):
    """Count data rows in a page file. Uses the csv module, not a line count,
    because a quoted field can contain a newline."""
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f)) - 1  # minus the header


def expected_total():
    """Ask the API how many 2025 rows exist, so we can check the download."""
    response = get_with_retries({"$select": "count(*) AS n", "$where": WHERE})
    return int(response.text.splitlines()[1].strip('"'))


def days_in_scope():
    """Every calendar day from START_DATE up to (not including) END_DATE."""
    day = date.fromisoformat(config.START_DATE[:10])
    end = date.fromisoformat(config.END_DATE[:10])
    while day < end:
        yield day
        day += timedelta(days=1)


def download_day(day):
    """Download every page for one day. Returns the number of rows on disk."""
    next_day = day + timedelta(days=1)
    where = (
        f"created_date >= '{day.isoformat()}T00:00:00' "
        f"AND created_date < '{next_day.isoformat()}T00:00:00'"
    )

    page = 0
    day_rows = 0
    while True:
        path = config.RAW_DIR / f"day_{day.isoformat()}_{page:03d}.csv"

        if path.exists():
            rows = count_rows(path)  # done in an earlier run, skip it
        else:
            params = {
                "$select": ",".join(COLUMNS),
                "$where": where,
                "$order": "unique_key",
                "$limit": PAGE_SIZE,
                "$offset": page * PAGE_SIZE,
            }
            response = get_with_retries(params)

            # Write to a temporary file first, then rename. A rename is all or
            # nothing, so a crash mid-write never leaves a half page that the
            # next run would mistake for a finished one.
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_bytes(response.content)
            tmp_path.replace(path)
            rows = count_rows(path)

        day_rows += rows
        # A page shorter than PAGE_SIZE means this day has no more rows.
        if rows < PAGE_SIZE:
            return day_rows
        page += 1


def main():
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    print("Counting 2025 rows on the API (can take a minute or two)...")
    total = expected_total()
    (config.RAW_DIR / "expected_count.txt").write_text(str(total))
    print(f"API reports {total:,} rows for 2025.")
    if not config.SOCRATA_APP_TOKEN:
        print("No SOCRATA_APP_TOKEN set; requests may be throttled.")

    downloaded = 0
    for day in days_in_scope():
        start = time.time()
        day_rows = download_day(day)
        downloaded += day_rows
        print(f"{day}: {day_rows:7,} rows  ({time.time() - start:5.1f}s)  "
              f"running total {downloaded:,}")

    print(f"\nDone: {downloaded:,} rows on disk, API expected {total:,}.")
    if downloaded != total:
        print("WARNING: counts differ. The live dataset may have changed during the "
              "download; the load step reports duplicates and missing keys.")


if __name__ == "__main__":
    main()
