"""Forecast Phase 2: three baseline forecasts, scored with the rolling-origin backtest.

Run:  python forecast/baselines.py      (a few seconds; needs daily_series.py first)

Baselines are the bar every real model has to clear. If a complex model can't
beat "same as last week", it isn't adding anything.

- naive:            every future day = the last observed day ("tomorrow = today")
- seasonal_naive:   each future day = the same weekday in the last observed week
- weekday_avg_4wk:  each future day = average of the last 4 observed same weekdays

All three only read `history`, which backtest.run_backtest has already cut at
the cutoff, so they can't see the days they forecast.

Every series is scored twice: as recorded, and with single-location bursts
(hotspots, see sql/forecast/04) subtracted from both history and actuals.

Outputs: data/processed/backtest_baselines.csv, results/forecast/figures/f2*.png,
         results/forecast/data/phase2_baselines.json, results/forecast/forecast.md
"""
import math

import matplotlib.dates
import numpy as np
import pandas as pd

import backtest as bt
import fc_common as fc
import report
from common import INK, INK_2, MUTED, PALETTE, plt, subtitle

VARIANTS = {"as_recorded": False, "excl_hotspots": True}  # name -> exclude_hotspots
SPLITS = {"validation": bt.VALIDATION_CUTOFFS, "test": bt.TEST_CUTOFFS}
REFERENCE = "seasonal_naive"  # every model is compared with this one


# --- Baselines ---------------------------------------------------------------

def same_weekday_lags(history, day, n_weeks):
    """Dates of the n_weeks most recent same weekdays before `day` that are in history.

    For a day 1-7 days after the cutoff that starts 7 days back; for 8-14 days
    after, 14 days back (the week before it isn't observed yet), and so on.
    """
    ahead = (day - history.index[-1]).days
    first = 7 * math.ceil(ahead / 7)
    return [day - pd.DateOffset(days=first + 7 * k) for k in range(n_weeks)]


def naive(history, future):
    return np.full(len(future), history.iloc[-1], dtype=float)


def seasonal_naive(history, future):
    return np.array([history[same_weekday_lags(history, d, 1)[0]] for d in future], dtype=float)


def weekday_avg_4wk(history, future):
    return np.array([history[same_weekday_lags(history, d, 4)].mean() for d in future], dtype=float)


BASELINES = {"naive": naive, "seasonal_naive": seasonal_naive, "weekday_avg_4wk": weekday_avg_4wk}
LABELS = {"naive": "Naive (tomorrow = today)", "seasonal_naive": "Seasonal naive (same weekday last week)",
          "weekday_avg_4wk": "4-week same-weekday average"}


# --- Scoring -----------------------------------------------------------------

def run_all(models, variants=VARIANTS, splits=SPLITS):
    """Backtest every model on every series, variant and split; one long table."""
    frames = []
    for variant, exclude in variants.items():
        daily = fc.load_daily(exclude_hotspots=exclude)
        for series in daily.columns:
            for split, cutoffs in splits.items():
                for name, model in models.items():
                    res = bt.run_backtest(daily[series], model, cutoffs)
                    frames.append(res.assign(variant=variant, series=series, split=split, model=name))
    return pd.concat(frames, ignore_index=True)


def metrics_table(results, reference=REFERENCE):
    """Pooled MAE/RMSE/MAPE per (variant, split, series, model) + % better than the reference."""
    rows = []
    for key, g in results.groupby(["variant", "split", "series", "model"], sort=False):
        rows.append(dict(zip(["variant", "split", "series", "model"], key), **bt.score(g),
                         n_windows=g["cutoff"].nunique(), n_forecasts=len(g)))
    table = pd.DataFrame(rows)
    ref = table[table["model"] == reference].set_index(["variant", "split", "series"])
    for m in ("mae", "rmse", "mape"):
        ref_err = table.set_index(["variant", "split", "series"]).index.map(ref[m])
        table[f"{m}_improvement_pct"] = bt.improvement_pct(table[m], ref_err.to_numpy())
    return table


# --- Figures -----------------------------------------------------------------

def plot_design(daily):
    """Timeline: what each backtest window trains on and forecasts."""
    fig, ax = plt.subplots(figsize=(11, 4.4))
    rows = [("validation", c) for c in bt.VALIDATION_CUTOFFS] + [("test", c) for c in bt.TEST_CUTOFFS]
    start = daily.index[0]
    for i, (split, cutoff) in enumerate(rows):
        y = len(rows) - i
        ax.barh(y, (cutoff - start).days + 1, left=start, height=0.6, color=MUTED, alpha=0.35)
        ax.barh(y, bt.HORIZON, left=cutoff + pd.DateOffset(days=1), height=0.6,
                color=PALETTE[0] if split == "test" else PALETTE[2])
    ax.axvline(bt.TEST_CUTOFFS[0] + pd.DateOffset(days=1), color=INK, linewidth=1, linestyle="--")
    ax.text(bt.TEST_CUTOFFS[0] + pd.DateOffset(days=2), len(rows) + 0.7, "test period starts",
            fontsize=8.5, color=INK_2)
    ax.set_yticks([len(rows) - i for i in range(len(rows))],
                  [f"{'val' if s == 'validation' else 'test'} {c:%b %d}" for s, c in rows], fontsize=8)
    ax.xaxis.set_major_locator(matplotlib.dates.MonthLocator())
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b"))
    ax.grid(axis="y", visible=False)
    ax.set_title("Rolling-origin backtest: train on everything up to the cutoff, forecast the next 14 days", pad=24)
    subtitle(ax, "Grey: history the model may use. Green: validation windows (Phase 3 tunes on these). "
                 "Blue: test windows (reported only).")
    fc.save_figure(fig, "f2a_backtest_design.png")


def plot_test_windows(results, series=fc.TOTAL, variant="as_recorded"):
    r = results[(results["series"] == series) & (results["variant"] == variant) & (results["split"] == "test")]
    cutoffs = sorted(r["cutoff"].unique())
    fig, axes = plt.subplots(3, 3, figsize=(11, 7.5), sharey=True)
    for ax, cutoff in zip(axes.flat, cutoffs):
        w = r[r["cutoff"] == cutoff]
        actual = w[w["model"] == REFERENCE]
        ax.plot(actual["day"], actual["actual"], color=INK, linewidth=2, label="Actual")
        for i, m in enumerate(BASELINES):
            f = w[w["model"] == m]
            ax.plot(f["day"], f["forecast"], color=PALETTE[i], linewidth=1.3,
                    linestyle="--" if m == "naive" else "-", label=LABELS[m])
        ax.set_title(f"Cutoff {pd.Timestamp(cutoff):%b %d}", fontsize=10)
        ax.xaxis.set_major_locator(matplotlib.dates.DayLocator(bymonthday=[1, 8, 15, 22]))
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
        ax.tick_params(axis="x", labelsize=7.5)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v / 1000:.0f}k"))
    fig.suptitle(f"Baseline forecasts vs actual, {series}: the 9 test windows (14 days each)",
                 x=0.01, ha="left", fontweight="bold", fontsize=12)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, frameon=False)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fc.save_figure(fig, "f2b_test_windows.png")


def plot_by_horizon(results, series=fc.TOTAL, variant="as_recorded"):
    r = results[(results["series"] == series) & (results["variant"] == variant) & (results["split"] == "test")]
    fig, ax = plt.subplots(figsize=(11, 4))
    for i, m in enumerate(BASELINES):
        by_h = r[r["model"] == m].groupby("horizon").apply(lambda g: bt.mae(g["actual"], g["forecast"]),
                                                          include_groups=False)
        ax.plot(by_h.index, by_h, color=PALETTE[i], linewidth=2, marker="o", markersize=5, label=LABELS[m])
    ax.set_xticks(range(1, bt.HORIZON + 1))
    ax.set_xlabel("Days ahead")
    ax.set_ylabel("MAE (requests per day)")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylim(0, None)
    ax.set_title(f"Average miss by days ahead, {series} (test windows)", pad=24)
    subtitle(ax, f"Each point averages only {len(bt.TEST_CUTOFFS)} forecasts (one per test window), "
                 "so single events move it a lot.")
    ax.legend(loc="upper center", ncol=3, bbox_to_anchor=(0.5, -0.15))
    fc.save_figure(fig, "f2c_error_by_horizon.png")


# --- Main --------------------------------------------------------------------

def main():
    results = run_all(BASELINES)
    out = fc.DAILY_FILE.parent / "backtest_baselines.csv"
    results.to_csv(out, index=False)
    print(f"saved {out.relative_to(fc.PROJECT_ROOT)}")

    table = metrics_table(results)
    total_test = results[(results["series"] == fc.TOTAL) & (results["split"] == "test")]
    per_window = {variant: {str(pd.Timestamp(c).date()): {m: bt.mae(g2["actual"], g2["forecast"])
                                                         for m, g2 in g.groupby("model", sort=False)}
                            for c, g in total_test[total_test["variant"] == variant].groupby("cutoff")}
                  for variant in VARIANTS}
    by_horizon = {m: total_test[(total_test["variant"] == "as_recorded") & (total_test["model"] == m)]
                  .groupby("horizon").apply(lambda g: bt.mae(g["actual"], g["forecast"]), include_groups=False)
                  .round(1).to_dict() for m in BASELINES}

    fc.save_json("phase2_baselines.json", {
        "horizon": bt.HORIZON, "step": bt.STEP,
        "validation_cutoffs": [c.date() for c in bt.VALIDATION_CUTOFFS],
        "test_cutoffs": [c.date() for c in bt.TEST_CUTOFFS],
        "reference": REFERENCE, "labels": LABELS,
        "metrics": table.round(2).to_dict(orient="records"),
        "per_window_mae_total_test": per_window,
        "mae_by_horizon_total_test": by_horizon,
    })
    plot_design(fc.load_daily())
    plot_test_windows(results)
    plot_by_horizon(results)
    report.write()


if __name__ == "__main__":
    main()
