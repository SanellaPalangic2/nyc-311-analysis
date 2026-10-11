"""Build the small, pre-aggregated files the Streamlit dashboard reads.

Run:  python analysis/build_app_data.py      (about 3 minutes; run Stages 1-3 first)

The app (app/streamlit_app.py) must never touch the database or the 3.6M raw
rows: it runs on a public server with no credentials. So everything it shows
is computed here, once, and saved to app/data/ (well under 1 MB in total):

  cube.parquet        resolution-time stats for every combination of agency,
                      borough and season, including "All" for each one
  km_curves.parquet   Kaplan-Meier curves per agency on a fixed time grid
  km_medians.parquet  Kaplan-Meier median + 95% CI per agency
  summary.json        headline numbers and model results (from results/data)

Why a "cube" instead of one row per request: medians can't be combined. The
median for "Bronx + Summer" can't be worked out from the Bronx median and the
Summer median, so each filter combination's median is computed exactly in SQL
with GROUP BY CUBE, which produces every combination in one query.
"""
import json

import numpy as np
import pandas as pd
import psycopg

import common
import survival
from key_numbers import key_numbers

APP_DATA = common.PROJECT_ROOT / "app" / "data"
VIEW_FILE = common.PROJECT_ROOT / "sql" / "schema" / "03_analysis_view.sql"
GRID_POINTS = 150  # time points per Kaplan-Meier curve

# GROUPING(x) = 1 marks the rows where x was rolled up into a total ("All").
CUBE_SQL = """
SELECT
    CASE WHEN GROUPING(agency) = 1 THEN 'All' ELSE agency END   AS agency,
    CASE WHEN GROUPING(borough) = 1 THEN 'All' ELSE borough END AS borough,
    CASE WHEN GROUPING(season) = 1 THEN 'All' ELSE season END   AS season,
    COUNT(*)                                                    AS n_requests,
    COUNT(resolution_hours)                                     AS n_with_time,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours) AS median_hours,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours) AS p90_hours,
    100.0 * SUM(closed_within_30d::int) / NULLIF(SUM(outcome_30d_known::int), 0)
                                                                AS pct_closed_within_30d,
    100.0 * AVG((resolution_status = 'no_close_date')::int)     AS pct_no_close_date
FROM v_requests
GROUP BY CUBE (agency, borough, season)
"""


def build_cube():
    with psycopg.connect(common.config.get_database_url()) as conn:
        conn.execute(VIEW_FILE.read_text())
        cur = conn.execute(CUBE_SQL)
        cube = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    for col in ["median_hours", "p90_hours", "pct_closed_within_30d", "pct_no_close_date"]:
        cube[col] = cube[col].astype(float)

    # Check against the earlier SQL answer key: the per-agency rows of the cube
    # must match 02_by_agency.sql to 2 decimals.
    key_file = common.PROJECT_ROOT / "outputs" / "powerbi" / "summary_by_agency.csv"
    if key_file.exists():
        key = pd.read_csv(key_file).set_index("agency")
        rows = cube[(cube["borough"] == "All") & (cube["season"] == "All") & (cube["agency"] != "All")]
        bad = [a for a, m in zip(rows["agency"], rows["median_hours"])
               if abs(round(m, 2) - key.loc[a, "median_hours"]) > 0.006]
        print(f"cube agency medians vs 02_by_agency.sql: {len(rows) - len(bad)}/{len(rows)} match {bad or ''}")
    return cube


def build_km():
    df = common.load_requests(["created_at", "closed_at", "agency", "status",
                               "resolution_status", "resolution_hours"])
    table, _ = survival.build_survival_table(df, df["closed_at"].max())
    grid = np.logspace(np.log10(1 / 60), np.log10(table["duration_hours"].max()), GRID_POINTS)
    curves, medians = [], []
    groups = [("All agencies", table)] + list(table.groupby("agency"))
    for agency, sub in groups:
        kmf, m = survival.km_fit(sub["duration_hours"], sub["event"])
        # The confidence band is a step function, so carry each value forward
        # to the grid times.
        ci = kmf.confidence_interval_survival_function_
        ci = ci.reindex(ci.index.union(grid)).ffill().loc[grid]
        curves.append(pd.DataFrame({
            "agency": agency, "hours": grid,
            "survival": kmf.survival_function_at_times(grid).to_numpy(),
            "lower": ci.iloc[:, 0].to_numpy(), "upper": ci.iloc[:, 1].to_numpy()}))
        medians.append({"agency": agency, "n": len(sub), "censored": int((sub["event"] == 0).sum()),
                        "km_median": survival.finite(m["median"]),
                        "ci_lower": survival.finite(m["ci_lower"]),
                        "ci_upper": survival.finite(m["ci_upper"])})
    return pd.concat(curves, ignore_index=True), pd.DataFrame(medians)


def build_summary():
    k = key_numbers()
    pred = json.loads((common.DATA_DIR / "prediction.json").read_text(encoding="utf-8"))
    stats = json.loads((common.DATA_DIR / "stats.json").read_text(encoding="utf-8"))
    sv = json.loads((common.DATA_DIR / "survival.json").read_text(encoding="utf-8"))
    with psycopg.connect(common.config.get_database_url()) as conn:
        n_types = conn.execute("SELECT COUNT(DISTINCT complaint_type) FROM clean_311").fetchone()[0]
    return {
        "n_complaint_types": n_types,
        "key_numbers": {key: value for key, value in k.items()
                        if isinstance(value, (int, float, str)) or value is None},
        "eps": k["eps"],
        "top3_features": k["top3_features"],
        "snapshot": sv["snapshot"],
        "agency_volume_order": sv["top8"] + [r["agency"] for r in sv["agencies"]
                                             if r["agency"] not in sv["top8"]],
        "kruskal_wallis": stats["kruskal_wallis"],
        "model": {name: pred[name] for name in
                  ["results", "best_model", "calibration", "permutation", "roc", "counts",
                   "threshold", "features", "split"]},
    }


def main():
    APP_DATA.mkdir(parents=True, exist_ok=True)
    print("Building cube ...")
    cube = build_cube()
    cube.to_parquet(APP_DATA / "cube.parquet", index=False)
    print("Building Kaplan-Meier curves ...")
    curves, medians = build_km()
    curves.to_parquet(APP_DATA / "km_curves.parquet", index=False)
    medians.to_parquet(APP_DATA / "km_medians.parquet", index=False)
    (APP_DATA / "summary.json").write_text(
        json.dumps(build_summary(), indent=2, default=float) + "\n", encoding="utf-8")

    total = 0
    for path in sorted(APP_DATA.iterdir()):
        size = path.stat().st_size
        total += size
        print(f"  {path.name:20} {size / 1024:8.1f} KB")
    print(f"  total {total / 1024 / 1024:.2f} MB (limit 50 MB)")


if __name__ == "__main__":
    main()
