"""Stage 1: survival analysis of time to resolution (lifelines).

Run:  python analysis/survival.py      (about 5-10 minutes)

Why survival analysis: a still-open request has no resolution time, but we
do know it has taken AT LEAST as long as it's been open. Survival methods use
that partial information ("censoring") instead of dropping the request, so
the slowest requests are no longer left out.

Vocabulary
- event = 1: the request closed; duration = hours from created to closed.
- event = 0 (censored): still open at the data snapshot; duration = hours
  from created to the snapshot.
- Survival curve S(t): share of requests still open after t hours.
- Median survival time: the t where S(t) drops to 0.5.
- Hazard ratio (Cox model): how much faster a group closes than the reference
  group at any moment. 2.0 = closes at twice the rate; 0.5 = half the rate.

Outputs: results/figures/1*.png, results/data/survival.json, results/results.md
"""
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter
from lifelines.statistics import multivariate_logrank_test, proportional_hazard_test
from lifelines.utils import median_survival_times

import common
import results_md
from common import BLUE, INK, INK_2, MUTED, PALETTE, SEED, plt

COX_SAMPLE_SIZE = 250_000
# The PH test's run time grows with roughly the square of the sample size
# (measured: 25k rows 50 s, 50k rows 193 s), so it runs on its own 50k fit.
PH_SAMPLE_SIZE = 50_000
# Agencies this small have too few requests in a sample for a stable hazard
# ratio (OTI: ~15 rows in 250k), so the Cox model groups them together.
SMALL_AGENCY_MAX = 5_000
N_TOP_AGENCIES = 8
N_TOP_TYPES = 10
REFERENCE = {"agency": "NYPD", "borough": "BROOKLYN", "season": "Winter"}


# --- Data ----------------------------------------------------------------------

def build_survival_table(df, snapshot):
    """Turn cleaned requests into (duration_hours, event) rows.

    - closed with a valid time            -> event 1, duration = resolution_hours
    - no close date and status not Closed -> event 0 (censored), duration =
                                             snapshot - created_at
    - left out (time unknowable): status Closed but no close date, closed
      before created, placeholder close dates. These are counted, not hidden.
    Returns (table, counts).
    """
    status = df["resolution_status"]
    closed = status == "closed"
    still_open = (status == "no_close_date") & (df["status"].fillna("") != "Closed")
    keep = closed | still_open

    out = df.loc[keep].copy()
    out["event"] = closed[keep].astype(int)
    hours_open = (snapshot - out["created_at"]).dt.total_seconds() / 3600
    out["duration_hours"] = out["resolution_hours"].where(out["event"] == 1, hours_open)

    counts = {
        "rows_in_clean_table": len(df),
        "events_closed": int(closed.sum()),
        "censored_still_open": int(still_open.sum()),
        "excluded_closed_status_without_date": int(((status == "no_close_date") & ~still_open).sum()),
        "excluded_closed_before_created": int((status == "closed_before_created").sum()),
        "excluded_placeholder_date": int((status == "placeholder_date").sum()),
        "rows_used": int(keep.sum()),
    }
    return out, counts


def km_fit(durations, events, label=None):
    """Kaplan-Meier fit plus its median and the median's 95% confidence interval.

    A median of inf means "not reached": the curve never drops to 50%, i.e.
    more than half the requests were still open at the end of follow-up.
    """
    kmf = KaplanMeierFitter(label=label).fit(durations, events)
    ci = median_survival_times(kmf.confidence_interval_).iloc[0]
    return kmf, {"median": kmf.median_survival_time_,
                 "ci_lower": min(ci.iloc[0], ci.iloc[1]),
                 "ci_upper": max(ci.iloc[0], ci.iloc[1])}


def finite(x):
    """JSON can't hold infinity; store 'not reached' medians as None."""
    return None if x is None or np.isinf(x) else float(x)


# --- Cox model -------------------------------------------------------------------

def cox_design(sample, top_types, small_agencies=()):
    """One 0/1 column per category, leaving out the reference category.

    Hazard ratios are then relative to: NYPD, Brooklyn, Winter, and complaint
    types outside the top 10. Requests with an unknown borough are left out.
    Agencies in small_agencies share one column.
    """
    X = pd.DataFrame({"duration_hours": sample["duration_hours"], "event": sample["event"]})
    agency = sample["agency"]
    if len(small_agencies):
        agency = agency.where(~agency.isin(small_agencies),
                              f"small agencies ({', '.join(sorted(small_agencies))})")
    groups = {"agency": agency, "borough": sample["borough"], "season": sample["season"]}
    for var, values in groups.items():
        for value in sorted(values.unique()):
            if value != REFERENCE[var]:
                X[f"{var}: {value}"] = (values == value).astype(float)
    for t in top_types:
        X[f"type: {t}"] = (sample["complaint_type"] == t).astype(float)
    # A category with no closed requests in the sample can't be estimated.
    no_events = [c for c in X.columns[2:] if X.loc[X["event"] == 1, c].sum() == 0]
    return X.drop(columns=no_events), no_events


def fit_cox(table, top_types, small_agencies, seed, n=COX_SAMPLE_SIZE):
    pool = table[table["borough"] != "UNSPECIFIED"]
    sample = pool.sample(n=n, random_state=seed)
    X, dropped = cox_design(sample, top_types, small_agencies)
    cph = CoxPHFitter().fit(X, duration_col="duration_hours", event_col="event")
    return cph, X, dropped


# --- Figures ---------------------------------------------------------------------

def plot_km(curves, medians, colors):
    """Survival curves on a log time axis, each labeled where it crosses 50%."""
    fig, ax = plt.subplots(figsize=(11, 6.2))
    grid = np.logspace(np.log10(1 / 60), np.log10(24 * 640), 500)
    for agency, kmf in curves.items():
        ax.plot(grid, kmf.survival_function_at_times(grid).values, color=colors[agency],
                linewidth=2, drawstyle="steps-post", label=agency)
    ax.axhline(0.5, color=MUTED, linewidth=1)
    ax.text(grid[0], 0.515, "50% still open = median", color=INK_2, fontsize=8.5)

    # Direct labels at each median, staggered so neighbors don't collide.
    order = sorted(medians, key=lambda a: medians[a]["median"])
    offsets = [0.09, -0.09, 0.17, -0.17]
    for i, agency in enumerate(order):
        m = medians[agency]["median"]
        if np.isinf(m):
            continue
        dy = offsets[i % 4]
        ax.plot([m], [0.5], "o", color=colors[agency], markersize=6,
                markeredgecolor=common.SURFACE, markeredgewidth=1.5, zorder=4)
        ax.annotate(agency, (m, 0.5), (m, 0.5 + dy), ha="center", fontsize=8.5, color=INK,
                    arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})

    common.time_axis(ax)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("Share of requests still open")
    ax.set_xlabel("Time since the request was created (log scale)")
    ax.legend(title="Agency (by volume)", loc="lower left", ncol=2, fontsize=9)
    ax.set_title("Kaplan-Meier curves: how fast each agency closes requests", pad=24)
    common.subtitle(ax, "Top 8 agencies by volume, 2025. Still-open requests are censored, not "
                        "dropped. Curves start below 100% where requests closed within a minute.")
    common.save_figure(fig, "1a_km_top8_agencies.png")


def plot_median_bias(table_rows):
    """Dumbbell: median from closed requests only vs Kaplan-Meier median."""
    rows = sorted(table_rows, key=lambda r: r["median_closed_only"])
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(10.5, 6.5))
    right_edge = 24 * 800
    for yi, r in zip(y, rows):
        km = r["km_median"] if r["km_median"] is not None else right_edge
        ax.plot([r["median_closed_only"], km], [yi, yi], color=common.AXIS, linewidth=2, zorder=1)
        if r["km_median"] is None:
            ax.text(right_edge * 1.1, yi, "not reached", va="center", fontsize=8.5, color=INK_2)
    ax.scatter([r["median_closed_only"] for r in rows], y, s=55, color=MUTED, zorder=3,
               edgecolor=common.SURFACE, linewidth=1.5, label="Closed requests only (earlier)")
    reached = [(yi, r["km_median"]) for yi, r in zip(y, rows) if r["km_median"] is not None]
    ax.scatter([m for _, m in reached], [yi for yi, _ in reached], s=55, color=BLUE, zorder=3,
               edgecolor=common.SURFACE, linewidth=1.5, label="Kaplan-Meier (includes open)")
    ax.set_yticks(y, [r["agency"] for r in rows])
    ax.grid(axis="y", visible=False)
    common.time_axis(ax)
    ax.set_xlim(right=right_edge * 4)
    ax.set_xlabel("Median time to resolution (log scale)")
    ax.legend(loc="lower right")
    ax.set_title("Dropping open requests made some agencies look much faster", pad=24)
    common.subtitle(ax, "Median by agency: closed requests only vs Kaplan-Meier with open "
                        "requests censored")
    common.save_figure(fig, "1b_median_bias_by_agency.png")


def plot_hazard_ratios(summary):
    """Forest plot of Cox hazard ratios with 95% CIs (log scale)."""
    df = summary.copy()
    df["group"] = df.index.str.split(":").str[0]
    df = df.iloc[::-1]
    y = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(10, 0.27 * len(df) + 1.6))
    ax.hlines(y, df["hr_lower"], df["hr_upper"], color=common.AXIS, linewidth=2)
    ax.scatter(df["hr"], y, s=30, color=BLUE, zorder=3, edgecolor=common.SURFACE, linewidth=1)
    ax.axvline(1, color=INK_2, linewidth=1)
    ax.set_xscale("log")
    ticks = [t for t in [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5]
             if df["hr_lower"].min() / 1.5 <= t <= df["hr_upper"].max() * 1.5]
    ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
    ax.xaxis.set_minor_locator(plt.NullLocator())
    ax.set_yticks(y, df.index, fontsize=8.5)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Hazard ratio (log scale): above 1 = closes faster than the reference, "
                  "below 1 = slower")
    ax.set_title("Cox model: what speeds up or slows down resolution", pad=24)
    common.subtitle(ax, f"Random sample of {COX_SAMPLE_SIZE:,} requests (seed {SEED}). "
                        "Reference: NYPD, Brooklyn, Winter, other complaint types.")
    common.save_figure(fig, "1c_cox_hazard_ratios.png")


def plot_loglog(curves, colors):
    """Proportional-hazards check: log(-log S) vs log t should be parallel."""
    fig, ax = plt.subplots(figsize=(10.5, 6))
    grid = np.logspace(np.log10(1 / 60), np.log10(24 * 640), 400)
    for agency, kmf in curves.items():
        s = kmf.survival_function_at_times(grid).values
        ok = (s > 0) & (s < 1)
        ax.plot(grid[ok], np.log(-np.log(s[ok])), color=colors[agency], linewidth=2, label=agency)
    common.time_axis(ax)
    ax.set_ylabel("log(-log(share still open))")
    ax.set_xlabel("Time since the request was created (log scale)")
    ax.legend(title="Agency", loc="upper left", ncol=2, fontsize=9)
    ax.set_title("Proportional hazards check: curves should run parallel, but they cross", pad=24)
    common.subtitle(ax, "If one agency's hazard were a constant multiple of another's, these "
                        "lines would be parallel.")
    common.save_figure(fig, "1d_ph_check_loglog.png")


# --- Main ------------------------------------------------------------------------

def main():
    print("Loading clean_311 ...")
    df = common.load_requests(["created_at", "closed_at", "agency", "borough", "season",
                               "complaint_type", "status", "resolution_status",
                               "resolution_hours"])
    snapshot = df["closed_at"].max()
    table, counts = build_survival_table(df, snapshot)
    print(f"snapshot (latest close date): {snapshot}")
    for k, v in counts.items():
        print(f"  {k:40} {v:>10,}")

    # Overall: closed-only median vs Kaplan-Meier median
    _, overall = km_fit(table["duration_hours"], table["event"])
    overall_closed_only = table.loc[table["event"] == 1, "duration_hours"].median()

    # Per agency (all 15): the bias comparison
    volume = table["agency"].value_counts()
    agency_rows, km_by_agency = [], {}
    for agency in volume.index:
        sub = table[table["agency"] == agency]
        kmf, m = km_fit(sub["duration_hours"], sub["event"], label=agency)
        km_by_agency[agency] = (kmf, m)
        closed_only = sub.loc[sub["event"] == 1, "duration_hours"].median()
        km_median = finite(m["median"])
        agency_rows.append({
            "agency": agency,
            "n": len(sub),
            "censored": int((sub["event"] == 0).sum()),
            "pct_censored": 100 * (sub["event"] == 0).mean(),
            "median_closed_only": closed_only,
            "km_median": km_median,
            "km_ci_lower": finite(m["ci_lower"]),
            "km_ci_upper": finite(m["ci_upper"]),
            "difference_hours": None if km_median is None else km_median - closed_only,
            "difference_pct": None if km_median is None else 100 * (km_median - closed_only) / closed_only,
        })

    # Cross-check: closed-only medians must equal the earlier SQL results
    sql_key = common.PROJECT_ROOT / "outputs" / "powerbi" / "summary_by_agency.csv"
    if sql_key.exists():
        sql = pd.read_csv(sql_key).set_index("agency")["median_hours"]
        mismatches = [r["agency"] for r in agency_rows
                      if abs(round(r["median_closed_only"], 2) - sql[r["agency"]]) > 0.006]
        print(f"closed-only medians vs SQL 02_by_agency: "
              f"{len(agency_rows) - len(mismatches)}/{len(agency_rows)} match {mismatches or ''}")

    # Top 8 agencies: KM chart + log-rank test
    top8 = list(volume.index[:N_TOP_AGENCIES])
    colors = dict(zip(top8, PALETTE))  # color follows the agency in every chart
    plot_km({a: km_by_agency[a][0] for a in top8}, {a: km_by_agency[a][1] for a in top8}, colors)
    plot_loglog({a: km_by_agency[a][0] for a in top8}, colors)
    plot_median_bias(agency_rows)

    print("Log-rank test across the top 8 agencies ...")
    t8 = table[table["agency"].isin(top8)]
    lr = multivariate_logrank_test(t8["duration_hours"], t8["agency"], t8["event"])
    logrank = {"agencies": top8, "n": len(t8), "test_statistic": lr.test_statistic,
               "degrees_of_freedom": lr.degrees_of_freedom, "p_value": lr.p_value}
    print(f"  chi-square = {lr.test_statistic:,.2f}, df = {lr.degrees_of_freedom}, p = {lr.p_value}")

    # Cox model on a random sample, refit on a second sample to check stability
    print(f"Fitting Cox model on {COX_SAMPLE_SIZE:,} sampled requests ...")
    top_types = list(df["complaint_type"].value_counts().index[:N_TOP_TYPES])
    small = sorted(volume[volume < SMALL_AGENCY_MAX].index)
    cph, X, dropped = fit_cox(table, top_types, small, SEED)
    cph_2, _, _ = fit_cox(table, top_types, small, SEED + 1)
    hr = pd.DataFrame({
        "hr": cph.hazard_ratios_,
        "hr_lower": np.exp(cph.confidence_intervals_.iloc[:, 0]),
        "hr_upper": np.exp(cph.confidence_intervals_.iloc[:, 1]),
        "p": cph.summary["p"],
    })
    shared = hr.index.intersection(cph_2.hazard_ratios_.index)
    stability = (np.log(hr.loc[shared, "hr"]) - np.log(cph_2.hazard_ratios_[shared])).abs()
    print(f"  concordance = {cph.concordance_index_:.4f}; second sample: largest |log HR| "
          f"difference = {stability.max():.4f} ({stability.idxmax()})")
    plot_hazard_ratios(hr)

    print(f"Testing the proportional hazards assumption on a {PH_SAMPLE_SIZE:,}-row fit ...")
    cph_ph, X_ph, _ = fit_cox(table, top_types, small, SEED, n=PH_SAMPLE_SIZE)
    ph = proportional_hazard_test(cph_ph, X_ph, time_transform="rank").summary.reset_index()
    ph = ph.rename(columns={ph.columns[0]: "covariate"}).set_index("covariate")
    ph_violations = ph[ph["p"] < 0.05]
    print(f"  {len(ph_violations)} of {len(ph)} covariates violate PH at p < 0.05")

    common.save_json("survival.json", {
        "snapshot": str(snapshot),
        "counts": counts,
        "overall": {"median_closed_only": overall_closed_only,
                    "km_median": finite(overall["median"]),
                    "km_ci_lower": finite(overall["ci_lower"]),
                    "km_ci_upper": finite(overall["ci_upper"])},
        "agencies": agency_rows,
        "top8": top8,
        "logrank": logrank,
        "cox": {
            "sample_size": COX_SAMPLE_SIZE, "seed": SEED, "references": REFERENCE,
            "top_types": top_types, "dropped_no_events": dropped,
            "small_agencies_grouped": small, "small_agency_max": SMALL_AGENCY_MAX,
            "concordance": cph.concordance_index_,
            "n_events_in_sample": int(X["event"].sum()),
            "hazard_ratios": hr.reset_index(names="covariate").to_dict("records"),
            "stability_max_abs_log_hr_diff": stability.max(),
            "stability_worst_covariate": stability.idxmax(),
        },
        "ph_test": {
            "sample_size": PH_SAMPLE_SIZE, "seed": SEED,
            "n_covariates": len(ph), "n_violating_p05": len(ph_violations),
            "rows": ph.reset_index(names="covariate")[["covariate", "test_statistic", "p"]]
                      .to_dict("records"),
        },
    })
    results_md.render()


if __name__ == "__main__":
    main()
