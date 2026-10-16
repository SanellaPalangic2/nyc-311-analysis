"""Shared paths and helpers for the demand-forecasting module (forecast/).

- Reuses the chart style, SEED and DATABASE_URL handling from analysis/common.py.
- Figures go to results/forecast/figures/, numbers to results/forecast/data/*.json,
  and report.py renders those numbers into results/forecast/forecast.md.
- The daily series itself is data, so it lives in data/processed/ (git-ignored)
  and daily_series.py rebuilds it from PostgreSQL in about a minute.
"""
import json
import sys
from pathlib import Path

import holidays
import pandas as pd
import psycopg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "analysis"))
import common  # noqa: E402  (chart style, SEED; also puts src/ on the path)
import config  # noqa: E402

SEED = common.SEED
SQL_DIR = PROJECT_ROOT / "sql" / "forecast"
OUT_DIR = PROJECT_ROOT / "results" / "forecast"
FIG_DIR = OUT_DIR / "figures"
DATA_DIR = OUT_DIR / "data"
DAILY_FILE = config.PROCESSED_DIR / "daily_counts.csv"
HOTSPOT_FILE = config.PROCESSED_DIR / "daily_hotspots.csv"  # same shape; see sql/forecast/04

TOTAL = "Total"  # column name of the citywide series in the daily table
# US federal holidays. Known years in advance, so a model may use them as features.
US_HOLIDAYS = holidays.country_holidays("US", years=range(2024, 2027))


def run_sql(name):
    """Run one file from sql/forecast/ and return the result as a DataFrame."""
    with psycopg.connect(config.get_database_url()) as conn:
        cur = conn.execute((SQL_DIR / name).read_text())
        columns = [col.name for col in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def load_daily(exclude_hotspots=False):
    """The daily table built by daily_series.py: index = day, columns = Total + top-5 agencies.

    exclude_hotspots=True subtracts the single-location bursts (sql/forecast/04).
    """
    if not DAILY_FILE.exists():
        raise FileNotFoundError(f"{DAILY_FILE} not found. Run `python forecast/daily_series.py` first.")
    daily = pd.read_csv(DAILY_FILE, index_col="day", parse_dates=["day"]).asfreq("D")
    if exclude_hotspots:
        daily = daily - pd.read_csv(HOTSPOT_FILE, index_col="day", parse_dates=["day"]).asfreq("D")
    return daily


def save_figure(fig, name):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / name
    fig.savefig(path)
    common.plt.close(fig)
    print(f"saved {path.relative_to(PROJECT_ROOT)}")


def _to_json(value):
    """numpy numbers -> Python numbers; dates/timestamps -> ISO strings."""
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value)}")


def save_json(name, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    path.write_text(json.dumps(data, indent=2, default=_to_json) + "\n", encoding="utf-8")
    print(f"saved {path.relative_to(PROJECT_ROOT)}")


def load_json(name):
    path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
