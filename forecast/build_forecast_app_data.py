"""Build the small files behind the dashboard's Forecast page (Phase 5).

Run:  python forecast/build_forecast_app_data.py   (seconds; run Phases 1-4 first)

Like analysis/build_app_data.py, everything the public app shows is computed
here once, so the deployed app reads a few small files and never fits a model,
touches the database or needs credentials:

  forecast_daily.parquet     actual requests per day, 2025, Total + top-5 agencies
  forecast_backtest.parquet  test-period forecasts with 80% intervals: the model
                             chosen on validation for each series
  forecast_summary.json      model comparison, per-agency results, staffing numbers
"""
import json

import pandas as pd

import fc_common as fc

APP_DATA = fc.PROJECT_ROOT / "app" / "data"
KEEP = ["series", "model", "cutoff", "day", "horizon", "forecast", "lo", "hi", "actual"]


def build_backtest():
    total = pd.read_csv(fc.DAILY_FILE.parent / "best_model_intervals.csv", parse_dates=["cutoff", "day"])
    total["series"] = fc.TOTAL
    agencies = pd.read_csv(fc.DAILY_FILE.parent / "agency_intervals.csv", parse_dates=["cutoff", "day"])
    out = pd.concat([total[KEEP], agencies[KEEP]], ignore_index=True)
    for col in ("forecast", "lo", "hi", "actual"):
        out[col] = out[col].round(1)
    return out


def build_summary():
    p1, p2, p3, p4 = (fc.load_json(f) for f in ("phase1_data.json", "phase2_baselines.json",
                                                "phase3_models.json", "phase4_agencies.json"))
    return {
        "horizon": p2["horizon"],
        "validation_cutoffs": p2["validation_cutoffs"],
        "test_cutoffs": p2["test_cutoffs"],
        "labels": p3["labels"],
        "order": p3["order"],
        "best_model": p3["best_model"],
        "chosen_settings": p3["chosen_settings"],
        "total_metrics": p3["metrics"],
        "total_coverage": p3["interval_coverage"],
        "total_error_sources": p3["error_sources_test"],
        "agency_order": p4["ranking"],
        "agency_chosen": p4["chosen"],
        "agency_metrics": p4["metrics"],
        "agency_per": p4["per_agency"],
        "agency_forecastability": p4["forecastability"],
        "hotspot_pct_total": p1["series_summary"][fc.TOTAL]["hotspot_pct"],
    }


def main():
    APP_DATA.mkdir(parents=True, exist_ok=True)
    daily = fc.load_daily().reset_index().melt(id_vars="day", var_name="series", value_name="actual")
    daily.to_parquet(APP_DATA / "forecast_daily.parquet", index=False)
    build_backtest().to_parquet(APP_DATA / "forecast_backtest.parquet", index=False)
    (APP_DATA / "forecast_summary.json").write_text(json.dumps(build_summary(), indent=1) + "\n", encoding="utf-8")
    for name in ("forecast_daily.parquet", "forecast_backtest.parquet", "forecast_summary.json"):
        path = APP_DATA / name
        print(f"saved {path.relative_to(fc.PROJECT_ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
