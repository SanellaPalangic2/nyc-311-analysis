"""Forecast Phase 4: forecast the top-5 agencies separately and turn forecasts into staffing plans.

Run:  python forecast/agencies.py      (about 4 minutes; needs evaluate_models.py first)

Approach per agency: the four candidates from Phases 2-3 (seasonal naive,
4-week same-weekday average, Holt-Winters and gradient boosting with the
settings tuned on the total series) are backtested on that agency, and the one
with the lowest MAE on the agency's VALIDATION windows is chosen. Test windows
are only used to report how the choice did. 80% intervals come from the chosen
model's own past errors (same method as Phase 3).

Staffing: an agency that rosters to the point forecast will be short on roughly
half of all days. Rostering to the upper end of the 80% interval (the 90th
percentile) is the staffing version of safety stock: extra capacity paid for on
quiet days in exchange for being short less often. staffing_stats() measures
both policies on the test days, in requests (no data on requests handled per
staff shift exists here, so it isn't converted to people).

Outputs: data/processed/agency_backtests.csv, data/processed/agency_intervals.csv,
         results/forecast/figures/f4*.png, results/forecast/data/phase4_agencies.json,
         results/forecast/forecast.md
"""
import matplotlib.dates
import numpy as np
import pandas as pd

import backtest as bt
import baselines
import daily_series
import evaluate_models as em
import fc_common as fc
import models
import report
from common import INK, INK_2, MUTED, ORANGE, PALETTE, plt, subtitle

CANDIDATE_NAMES = ["seasonal_naive", "weekday_avg_4wk", "holt_winters", "gradient_boosting"]


def candidates(params):
    return {"seasonal_naive": baselines.seasonal_naive, "weekday_avg_4wk": baselines.weekday_avg_4wk,
            "holt_winters": models.holt_winters(**params["holt_winters"]),
            "gradient_boosting": models.gradient_boosting(**params["gradient_boosting"])}


def choose_on_validation(table):
    """Per (variant, series): the candidate with the lowest validation MAE."""
    val = table[table["split"] == "validation"]
    return {key: g.loc[g["mae"].idxmin(), "model"] for key, g in val.groupby(["variant", "series"])}


def staffing_stats(rows):
    """Compare rostering to the forecast vs to the interval's upper end (P90), on actual outcomes.

    rows: forecasts with columns actual, forecast, hi.
    - short_days_pct: % of days demand exceeded the plan (the "stock-out rate")
    - avg_shortfall: mean requests above the plan on those short days
    - avg_unused: mean spare capacity (plan - actual) on days the plan was enough
    - buffer_pct: how much higher the P90 plan is than the forecast, on average
    """
    out = {}
    for policy, plan in {"forecast": rows["forecast"], "p90": rows["hi"]}.items():
        gap = rows["actual"] - plan
        short = gap > 0
        out[policy] = {
            "short_days_pct": float(100 * short.mean()),
            "avg_shortfall": float(gap[short].mean()) if short.any() else 0.0,
            "avg_unused": float((-gap[~short]).mean()) if (~short).any() else 0.0,
        }
    out["buffer_pct"] = float(100 * (rows["hi"] / rows["forecast"] - 1).mean())
    return out


def test_drivers():
    """Per agency: largest complaint type in the test period, and on its busiest test day."""
    df = fc.run_sql("06_agency_test_drivers.sql")
    return {r["agency"]: {k: (v if isinstance(v, str) else v.isoformat() if hasattr(v, "isoformat") else float(v))
                          for k, v in r.items() if k != "agency"} for r in df.to_dict(orient="records")}


# --- Figures -----------------------------------------------------------------

def plot_mape(table, chosen, agencies, labels):
    t = table[(table["variant"] == "as_recorded") & (table["split"] == "test")]
    fig, ax = plt.subplots(figsize=(11, 4.4))
    y = np.arange(len(agencies))[::-1]
    for i, m in enumerate(CANDIDATE_NAMES):
        vals = [t[(t["series"] == a) & (t["model"] == m)]["mape"].iloc[0] for a in agencies]
        ax.scatter(vals, y, s=60, color=PALETTE[i], zorder=3, edgecolor="white", linewidth=1.5, label=labels[m])
    for yi, a in zip(y, agencies):
        v = t[(t["series"] == a) & (t["model"] == chosen[("as_recorded", a)])]["mape"].iloc[0]
        ax.scatter([v], [yi], s=190, facecolor="none", edgecolor=INK, linewidth=1.4, zorder=4)
        ax.text(v, yi + 0.28, f"{v:.1f}%", ha="center", fontsize=8.5, color=INK_2)
    ax.set_yticks(y, agencies)
    ax.set_ylim(-0.6, len(agencies) - 0.3)
    ax.set_xlim(0, None)
    ax.set_xlabel("Test MAPE (average miss as % of actual; lower is better)")
    ax.grid(axis="y", visible=False)
    ax.set_title("Per-agency forecast error on the test windows, easiest at the top", pad=24)
    subtitle(ax, "Black ring = the approach chosen for that agency on its validation windows "
                 "(test results played no part in the choice).")
    ax.legend(loc="upper center", ncol=4, bbox_to_anchor=(0.5, -0.17))
    fc.save_figure(fig, "f4a_agency_mape.png")


def plot_agency_intervals(ivals, agencies, chosen, labels):
    fig, axes = plt.subplots(len(agencies), 1, figsize=(11, 2.2 * len(agencies)), sharex=True)
    for i, (ax, a) in enumerate(zip(axes, agencies)):
        r = ivals[(ivals["series"] == a) & (ivals["horizon"] <= 7)].sort_values("day")
        ax.fill_between(r["day"], r["lo"], r["hi"], color=PALETTE[0], alpha=0.18, linewidth=0)
        ax.plot(r["day"], r["forecast"], color=PALETTE[0], linewidth=1.8)
        ax.plot(r["day"], r["actual"], color=INK, linewidth=1.2)
        inside = 100 * ((r["actual"] >= r["lo"]) & (r["actual"] <= r["hi"])).mean()
        ax.text(0.005, 0.95, f"{a}: {labels[chosen[('as_recorded', a)]]}, {inside:.0f}% of days inside the 80% band",
                transform=ax.transAxes, fontsize=9.5, fontweight="bold", va="top", color=INK)
        ax.set_ylim(0, max(r["actual"].max(), r["hi"].max()) * 1.15)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    axes[0].set_title("Test period, 1-7 days ahead: actual (black) vs forecast (blue) with 80% interval", pad=24)
    subtitle(axes[0], "Each panel has its own scale. Each 7-day stretch comes from a different weekly cutoff.")
    axes[-1].xaxis.set_major_locator(matplotlib.dates.WeekdayLocator(byweekday=0, interval=2))
    axes[-1].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    fig.tight_layout()
    fc.save_figure(fig, "f4b_agency_intervals.png")


def plot_staffing(staffing, agencies):
    fig, ax = plt.subplots(figsize=(11, 4))
    y = np.arange(len(agencies))[::-1]
    h = 0.36
    for off, (policy, color, label) in zip([h / 2, -h / 2], [("forecast", ORANGE, "Roster to the forecast"),
                                                            ("p90", PALETTE[0], "Roster to the 80% interval's upper end (P90)")]):
        vals = [staffing[a][policy]["short_days_pct"] for a in agencies]
        ax.barh(y + off, vals, height=h - 0.04, color=color, label=label)
        for yi, v in zip(y, vals):
            ax.text(v + 0.8, yi + off, f"{v:.0f}%", va="center", fontsize=8.5, color=INK_2)
    ax.axvline(10, color=INK, linewidth=0.9, linestyle="--")
    ax.text(10.5, y[-1] - 0.55, "10% = what a P90 plan promises", fontsize=8.5, color=INK_2, va="center")
    ax.set_yticks(y, agencies)
    ax.set_xlim(0, max(staffing[a]["forecast"]["short_days_pct"] for a in agencies) * 1.15)
    ax.set_ylim(y[-1] - 0.9, y[0] + 0.5)
    ax.set_xlabel("% of test days on which demand exceeded the plan")
    ax.grid(axis="y", visible=False)
    ax.set_title("How often each staffing plan would have been short (test windows)", pad=24)
    fcst = [staffing[a]["forecast"]["short_days_pct"] for a in agencies]
    p90 = [staffing[a]["p90"]["short_days_pct"] for a in agencies]
    subtitle(ax, f"Rostering to the forecast was short on {min(fcst):.0f}-{max(fcst):.0f}% of days; the P90 buffer "
                 f"cut that to {min(p90):.0f}-{max(p90):.0f}%, above the 10% it was meant to deliver.")
    ax.legend(loc="upper center", ncol=2, bbox_to_anchor=(0.5, -0.17))
    fc.save_figure(fig, "f4c_staffing.png")


# --- Main --------------------------------------------------------------------

def main():
    p3 = fc.load_json("phase3_models.json")
    if not p3 or "chosen_params" not in p3:
        raise RuntimeError("Run `python forecast/evaluate_models.py` first (Phase 3 settings are reused).")
    cands = candidates(p3["chosen_params"])
    labels = {k: p3["labels"][k] for k in CANDIDATE_NAMES}
    daily = {v: fc.load_daily(exclude_hotspots=ex) for v, ex in baselines.VARIANTS.items()}
    agencies = [c for c in daily["as_recorded"].columns if c != fc.TOTAL]
    hot = pd.read_csv(fc.HOTSPOT_FILE, index_col="day")
    has_hotspots = [a for a in agencies if hot[a].sum() > 0]

    frames = []
    for variant, d in daily.items():
        for a in agencies:
            if variant == "excl_hotspots" and a not in has_hotspots:
                continue  # identical to as_recorded
            for name, f in cands.items():
                res = bt.run_backtest(d[a], f, em.CALIBRATION_CUTOFFS)
                frames.append(res.assign(variant=variant, series=a, model=name))
            print(f"  {variant} {a} done")
    allres = pd.concat(frames, ignore_index=True)
    allres.to_csv(fc.DAILY_FILE.parent / "agency_backtests.csv", index=False)

    split_of = {**{c: "validation" for c in bt.VALIDATION_CUTOFFS}, **{c: "test" for c in bt.TEST_CUTOFFS}}
    scored = allres[allres["cutoff"].isin(split_of)].assign(split=lambda df: df["cutoff"].map(split_of))
    table = baselines.metrics_table(scored)
    chosen = choose_on_validation(table)

    # Intervals, coverage, error sources and staffing for each agency's chosen model
    phase1 = fc.load_json("phase1_data.json")
    per_agency, interval_frames = {}, []
    for (variant, a), model in chosen.items():
        r = allres[(allres["variant"] == variant) & (allres["series"] == a)]
        cov, rows_by_split = {}, {}
        for split, cutoffs in baselines.SPLITS.items():
            rows = em.interval_rows(r, model, cutoffs)
            rows_by_split[split] = rows
            cov[split] = em.coverage_stats(rows)
        test_rows = rows_by_split["test"]
        outlier_days = daily_series.flag_outliers(daily[variant][a])
        outlier_days = list(outlier_days.index[outlier_days["is_outlier"]].date)
        test_all = scored[(scored["variant"] == variant) & (scored["series"] == a) & (scored["split"] == "test")]
        sources = em.error_sources(test_all, outlier_days)
        test_days = pd.date_range(bt.TEST_CUTOFFS[0] + pd.DateOffset(days=1), bt.LAST_DAY, freq="D")
        per_agency[f"{variant}/{a}"] = {
            "variant": variant, "series": a, "chosen_model": model,
            "coverage": cov,
            "error_sources_chosen": sources[model],
            "outlier_days_in_test_period": int(sum(pd.Timestamp(d) in test_days for d in outlier_days)),
            "staffing": staffing_stats(test_rows),
            "test_mean_actual": float(test_rows["actual"].mean()),
        }
        if variant == "as_recorded":
            interval_frames.append(test_rows.assign(series=a, model=model))
    ivals = pd.concat(interval_frames, ignore_index=True)
    ivals.to_csv(fc.DAILY_FILE.parent / "agency_intervals.csv", index=False)

    # Rank agencies by the chosen model's test MAPE (scale-free, so agencies of different size compare)
    t = table.set_index(["variant", "split", "series", "model"])
    ranking = sorted(agencies, key=lambda a: t.loc[("as_recorded", "test", a, chosen[("as_recorded", a)]), "mape"])
    s1 = phase1["series_summary"]
    forecastability = []
    for a in ranking:
        m = chosen[("as_recorded", a)]
        prof, monthly = s1[a]["weekday_profile_pct"], s1[a]["monthly_index_pct"]
        test_months = [monthly[k] for k in ("10", "11", "12")]
        forecastability.append({
            "series": a, "chosen_model": m,
            "test_mape": t.loc[("as_recorded", "test", a, m), "mape"],
            "validation_mape": t.loc[("as_recorded", "validation", a, m), "mape"],
            "test_mae": t.loc[("as_recorded", "test", a, m), "mae"],
            "test_mae_improvement_pct": t.loc[("as_recorded", "test", a, m), "mae_improvement_pct"],
            "noise_pct": s1[a]["noise_pct"],
            "weekday_swing_pct": max(prof.values()) - min(prof.values()),
            "oct_to_dec_level_change_pct": test_months[-1] - test_months[0],
            "hotspot_pct": s1[a]["hotspot_pct"],
            "outlier_days_in_test_period": per_agency[f"as_recorded/{a}"]["outlier_days_in_test_period"],
            "event_day_error_share_pct": per_agency[f"as_recorded/{a}"]["error_sources_chosen"]["event day"]["share_of_error_pct"],
        })

    fc.save_json("phase4_agencies.json", {
        "agencies": agencies, "ranking": ranking, "labels": labels,
        "candidates": CANDIDATE_NAMES, "settings": p3["chosen_settings"],
        "chosen": {f"{v}/{a}": m for (v, a), m in chosen.items()},
        "metrics": table.round(2).to_dict(orient="records"),
        "per_agency": per_agency,
        "forecastability": forecastability,
        "has_hotspots": has_hotspots,
        "test_drivers": test_drivers(),
    })
    plot_mape(table, chosen, ranking, labels)
    plot_agency_intervals(ivals, ranking, chosen, labels)
    plot_staffing({a: per_agency[f"as_recorded/{a}"]["staffing"] for a in ranking}, ranking)
    report.write()


if __name__ == "__main__":
    main()
