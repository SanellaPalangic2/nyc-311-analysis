"""Shared helpers for the advanced analyses (survival, statistics, prediction).

- Paths: code lives in analysis/, figures go to results/figures/, numbers go to
  results/*.json and are rendered into results/results.md by results_md.py.
- load_requests(): reads the cleaned table from PostgreSQL.
- Chart style: the same palette and look as src/findings.py.
"""
import io
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # draw to files only

import matplotlib.pyplot as plt
import pandas as pd
import psycopg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
import config  # noqa: E402  (reuses DATABASE_URL handling from the main project)

RESULTS_DIR = PROJECT_ROOT / "results"
FIG_DIR = RESULTS_DIR / "figures"
DATA_DIR = RESULTS_DIR / "data"  # numbers behind each stage (JSON / CSV)

SEED = 42  # one fixed random seed for every sample, bootstrap and model

# --- Chart style (same tokens as src/findings.py) ------------------------------
SURFACE, INK, INK_2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
# Categorical palette in its fixed, validated order (checked with the dataviz
# validator: passes colorblind separation for adjacent series).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
BLUE, ORANGE = PALETTE[0], PALETTE[1]

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 10,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK_2,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": MUTED,
    "ytick.color": INK_2,
    "text.color": INK,
    "legend.frameon": False,
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
    "savefig.facecolor": SURFACE,
})

# Hours -> readable labels for log-scale time axes
TIME_TICKS = {1 / 60: "1 min", 1: "1 hour", 24: "1 day", 24 * 7: "1 week",
              24 * 30: "30 days", 24 * 365: "1 year"}


def time_axis(ax, axis="x"):
    """Log scale with ticks at familiar durations instead of raw hours."""
    getattr(ax, f"set_{axis}scale")("log")
    getattr(ax, f"set_{axis}ticks")(list(TIME_TICKS), list(TIME_TICKS.values()))
    getattr(ax, f"{axis}axis").set_minor_locator(matplotlib.ticker.NullLocator())


def subtitle(ax, text):
    ax.text(0, 1.02, text, transform=ax.transAxes, color=INK_2, fontsize=9.5, va="bottom")


def save_figure(fig, name):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / name
    fig.savefig(path)
    plt.close(fig)
    print(f"saved {path.relative_to(PROJECT_ROOT)}")


def save_json(name, data):
    """Store a stage's numbers; results_md.py renders them into results.md."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    path.write_text(json.dumps(data, indent=2, default=float) + "\n", encoding="utf-8")
    print(f"saved {path.relative_to(PROJECT_ROOT)}")


def load_requests(columns):
    """Read columns of clean_311 from PostgreSQL into a DataFrame.

    Uses COPY ... TO STDOUT (Postgres writes CSV directly), which is much
    faster than pandas.read_sql for millions of rows.
    """
    sql = f"COPY (SELECT {', '.join(columns)} FROM clean_311) TO STDOUT WITH (FORMAT csv, HEADER true)"
    buffer = io.BytesIO()
    with psycopg.connect(config.get_database_url()) as conn:
        with conn.cursor() as cur, cur.copy(sql) as copy:
            for block in copy:
                buffer.write(block)
    buffer.seek(0)
    date_cols = [c for c in columns if c in ("created_at", "closed_at")]
    return pd.read_csv(buffer, parse_dates=date_cols)
