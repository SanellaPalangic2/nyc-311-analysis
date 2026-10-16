"""Forecast Phase 1: build and check the daily demand series.

Run:  python forecast/daily_series.py      (about 1 minute)

Builds one daily series of total 311 requests and one per top-5 agency with
the SQL in sql/forecast/, then checks it for the problems that break
forecasts: missing days, partial days, and outliers (holidays, storms, data
outages). Nothing is dropped or edited. Outlier days stay in the series
because they are real demand, and a forecast should be judged on them.

The outlier check compares each day with the SAME weekday in the 3 weeks
before and after it. That uses future days, which is fine here: it's a
one-off description of the data. The flags are never fed to a model (a model
can't know in advance that tomorrow will be an outlier). Holidays can be known
in advance, so Phase 3 uses them as a feature.

Outputs: data/processed/daily_counts.csv, data/processed/daily_hotspots.csv, results/forecast/figures/f1*.png,
         results/forecast/data/phase1_data.json, results/forecast/forecast.md
"""
import warnings

import numpy as np
import pandas as pd

import matplotlib.dates

import fc_common as fc
import report
from common import INK, INK_2, MUTED, ORANGE, PALETTE, plt, subtitle

NEIGHBOR_WEEKS = 3  # compare a day with the same weekday up to 3 weeks either side
Z_THRESHOLD = 3.5   # robust z-score cut-off for an outlier (Iglewicz & Hoaglin)
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# --- Checks (pure functions, tested in tests/test_forecast_daily_series.py) -------------

def expected_clock_hours(day):
    """Distinct local clock hours on a day: 23 on the spring DST day, else 24.

    The autumn DST day has 25 hours, but 01:00-02:00 happens twice, so it still
    has 24 distinct clock hours (which is what date_trunc('hour') counts).
    """
    day = pd.Timestamp(day)
    start = day.tz_localize("America/New_York")
    # DateOffset, not Timedelta: Timestamp + Timedelta warns under pandas 2.3 + numpy 2.5 (BUILD_LOG #1)
    end = (day + pd.DateOffset(days=1)).tz_localize("America/New_York")  # next local midnight
    hours = pd.date_range(start, end, freq="h", inclusive="left")
    return hours.tz_localize(None).nunique()


def check_calendar(daily, start, end):
    """Missing and partial days in the output of 01_daily_total.sql."""
    expected = pd.date_range(start, end, freq="D")
    days = pd.DatetimeIndex(daily["day"])
    hours_expected = pd.Series([expected_clock_hours(d) for d in days], index=days)
    hours_seen = pd.Series(daily["hours_with_data"].to_numpy(), index=days)
    partial = hours_seen[(hours_seen < hours_expected) & (hours_seen > 0)]
    zero = days[daily["requests"].to_numpy() == 0]
    return {
        "expected_days": len(expected),
        "days_in_series": len(days),
        "days_missing_from_calendar": [d.date() for d in expected.difference(days)],
        "days_with_zero_requests": [d.date() for d in zero],
        "days_missing_clock_hours": {str(d.date()): int(hours_expected[d] - n) for d, n in partial.items()},
        "dst_days": {str(d.date()): int(n) for d, n in hours_expected.items() if n != 24},
        "first_created": daily["first_created"].min(),
        "last_created": daily["last_created"].max(),
    }


def same_weekday_baseline(data, weeks=NEIGHBOR_WEEKS, min_neighbors=3):
    """Median of the same weekday 1..weeks weeks before and after each day.

    Works on one series or a table of series (one column each). The day itself
    is left out, so a spike doesn't pull its own baseline up. Near the start
    and end of the year fewer neighbours exist; days with fewer than
    min_neighbors get NaN.
    """
    shifts = [7 * k for k in range(1, weeks + 1)]
    stack = np.stack([data.shift(s).to_numpy(dtype=float) for s in shifts + [-s for s in shifts]])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices -> NaN, handled below
        median = np.nanmedian(stack, axis=0)
    median[(~np.isnan(stack)).sum(axis=0) < min_neighbors] = np.nan
    if isinstance(data, pd.DataFrame):
        return pd.DataFrame(median, index=data.index, columns=data.columns)
    return pd.Series(median, index=data.index, name=data.name)


def flag_outliers(series, threshold=Z_THRESHOLD):
    """Robust z-score of each day against its same-weekday baseline (on a log scale).

    log(count / baseline) turns "30% above normal" into the same number on a
    busy and a quiet weekday. The z-score uses the median and MAD instead of
    mean and standard deviation, so the outliers themselves don't hide.
    """
    baseline = same_weekday_baseline(series)
    log_ratio = np.log(series / baseline)
    mad = (log_ratio - log_ratio.median()).abs().median()
    robust_z = (log_ratio - log_ratio.median()) / (1.4826 * mad)
    return pd.DataFrame({
        "requests": series,
        "baseline": baseline,
        "pct_vs_baseline": 100 * (series / baseline - 1),
        "robust_z": robust_z,
        "is_outlier": robust_z.abs() > threshold,
    })


def outlier_driver(by_type, type_baseline, day):
    """Complaint type whose count moved furthest from its own same-weekday baseline."""
    excess = (by_type.loc[day] - type_baseline.loc[day]).dropna()
    top = excess.abs().idxmax()
    return top, float(excess[top])


def outlier_episodes(outliers, max_gap_days=3):
    """Group outlier days that are at most max_gap_days apart into episodes.

    outliers is the list of dicts built in main(). A storm or a filing burst
    lasts several days; one row per episode is easier to read than one per day.
    """
    episodes = []
    for o in sorted(outliers, key=lambda o: o["day"]):
        day = pd.Timestamp(o["day"])
        if episodes and (day - episodes[-1]["end"]).days <= max_gap_days:
            ep = episodes[-1]
            ep["end"], ep["days"] = day, ep["days"] + [o]
        else:
            episodes.append({"start": day, "end": day, "days": [o]})
    rows = []
    for ep in episodes:
        peak = max(ep["days"], key=lambda o: abs(o["robust_z"]))
        holidays_in = [o["holiday"] for o in ep["days"] if o["holiday"]]
        # A holiday explains the whole episode around it; otherwise the most common driver.
        driver = holidays_in[0] if holidays_in else             pd.Series([o["top_complaint_type"] for o in ep["days"]]).mode().iloc[0]
        excess = sum(o["requests"] - o["baseline"] for o in ep["days"])
        hotspot = sum(o["hotspot_requests"] for o in ep["days"])
        rows.append({
            "start": ep["start"].date(), "end": ep["end"].date(), "n_outlier_days": len(ep["days"]),
            "direction": "high" if peak["robust_z"] > 0 else "low",
            "peak_day": peak["day"], "peak_pct_vs_baseline": peak["pct_vs_baseline"],
            "peak_robust_z": peak["robust_z"],
            "main_driver": driver,
            "excess_requests": excess,  # sum of (requests - baseline); negative for dips
            "hotspot_requests": hotspot,
            # Share of a spike's extra requests that came from hotspots (None for dips)
            "hotspot_share_of_excess_pct": round(100 * hotspot / excess, 1) if excess > 0 else None,
            "holidays": holidays_in,
        })
    return rows


def complete_weeks(series):
    """Weekly totals (Monday start) for weeks that have all 7 days in the data.

    2025 starts on a Wednesday and ends on a Wednesday, so the first and last
    weeks are partial; summing them would look like a sudden drop in demand.
    """
    weeks = series.groupby(series.index.to_period("W-SUN"))
    totals, n_days = weeks.sum(), weeks.size()
    full = totals[n_days == 7]
    full.index = full.index.start_time
    return full, [str(p.start_time.date()) for p in n_days[n_days < 7].index]


def weekday_profile(series):
    """Median % above/below the week's own average, per weekday (complete weeks only).

    Dividing by the same week's average removes slow level changes (e.g. the
    winter heating season), leaving just the weekly shape.
    """
    week = series.index.to_period("W-SUN")
    n_days = series.groupby(week).transform("size")
    full = series[n_days == 7]
    ratio = full / full.groupby(full.index.to_period("W-SUN")).transform("mean")
    profile = 100 * (ratio.groupby(ratio.index.dayofweek).median() - 1)
    profile.index = WEEKDAYS
    return profile


def hotspot_table(hot, index, columns):
    """Daily hotspot requests in the same shape as the daily table (0 where none).

    hot is the long output of 04_daily_hotspots.sql (day, agency,
    hotspot_requests). The Total column counts hotspots of every agency, not
    just the top 5.
    """
    wide = hot.pivot_table(index="day", columns="agency", values="hotspot_requests",
                           aggfunc="sum").reindex(index).fillna(0)
    out = pd.DataFrame(0, index=index, columns=columns)
    for col in columns:
        if col == fc.TOTAL:
            out[col] = wide.sum(axis=1)
        elif col in wide:
            out[col] = wide[col]
    return out.astype(int)


# --- Figures -----------------------------------------------------------------

def month_axis(ax):
    ax.xaxis.set_major_locator(matplotlib.dates.MonthLocator())
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b"))


def plot_daily_total(total, flags, labels):
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.plot(total.index, total, color=MUTED, linewidth=0.9, label="Daily requests")
    ax.plot(total.index, total.rolling(7).mean(), color=PALETTE[0], linewidth=2,
            label="7-day average (past 7 days)")
    out = flags[flags["is_outlier"]]
    ax.scatter(out.index, out["requests"], s=40, color=ORANGE, zorder=3,
               edgecolor="white", linewidth=1.5, label="Outlier day")
    placed = []  # (day, above) of labels already drawn, to stack ones that are close in time
    for day, text in sorted(labels.items()):
        above = flags.at[day, "robust_z"] > 0
        ha = "left" if day.month == 1 else "right" if day.month == 12 else "center"
        stack = sum(1 for d, a in placed if a == above and (day - d).days < 30)
        placed.append((day, above))
        dy = (10 + 16 * stack) * (1 if above else -1)
        ax.annotate(text, (day, total[day]), xytext=(6 if ha == "left" else -6 if ha == "right" else 0, dy),
                    textcoords="offset points", ha=ha, va="bottom" if above else "top",
                    fontsize=8.5, color=INK_2,
                    arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.6} if stack else None)
    ax.set_ylim(total.min() * 0.85, total.max() * 1.1)
    month_axis(ax)
    ax.set_title("Daily 311 requests, 2025", pad=24)
    subtitle(ax, f"{len(total):,} days, no gaps. Orange: {len(out)} outlier days "
                 f"(more than {Z_THRESHOLD} robust SDs from the same weekday in nearby weeks).")
    ax.set_ylabel("Requests per day")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(loc="upper center", ncol=3, bbox_to_anchor=(0.5, -0.08))
    fc.save_figure(fig, "f1a_daily_total.png")


def plot_weekly(weekly):
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.plot(weekly.index, weekly, color=PALETTE[0], linewidth=2, marker="o", markersize=4)
    month_axis(ax)
    ax.set_title("Weekly 311 requests (complete Monday-Sunday weeks)", pad=24)
    subtitle(ax, "The partial first and last weeks of 2025 are left out so they don't look like drops.")
    ax.set_ylabel("Requests per week")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylim(0, weekly.max() * 1.1)
    fc.save_figure(fig, "f1b_weekly_total.png")


def plot_weekday_profiles(profiles):
    names = list(profiles)
    fig, axes = plt.subplots(2, 3, figsize=(11, 6), sharey=True)
    lim = max(abs(p).max() for p in profiles.values()) * 1.25
    for i, (ax, name) in enumerate(zip(axes.flat, names)):
        color = INK_2 if name == fc.TOTAL else PALETTE[i - 1]
        p = profiles[name]
        ax.bar(p.index, p, color=color, width=0.7)
        ax.axhline(0, color=INK, linewidth=0.8)
        for x, v in zip(p.index, p):
            ax.text(x, v + (1 if v >= 0 else -1) * lim * 0.04, f"{v:+.0f}%", ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=7.5, color=INK_2)
        ax.set_title(name, fontsize=11)
        ax.set_ylim(-lim, lim)
        ax.grid(axis="x", visible=False)
    for ax in axes[:, 0]:
        ax.set_ylabel("% vs that week's average")
    fig.suptitle("Day-of-week pattern: each weekday vs its own week's average (median over complete weeks)",
                 x=0.01, ha="left", fontweight="bold", fontsize=12)
    fig.tight_layout()
    fc.save_figure(fig, "f1c_weekday_profile.png")


def plot_agencies(wide, agencies):
    fig, axes = plt.subplots(len(agencies), 1, figsize=(11, 2.1 * len(agencies)), sharex=True)
    for i, (ax, name) in enumerate(zip(axes, agencies)):
        s = wide[name]
        ax.plot(s.index, s, color=MUTED, linewidth=0.7)
        ax.plot(s.index, s.rolling(7).mean(), color=PALETTE[i], linewidth=2)
        ax.set_ylim(0, s.max() * 1.1)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.text(0.005, 0.95, f"{name}  (mean {s.mean():,.0f}/day)", transform=ax.transAxes,
                fontsize=10, fontweight="bold", va="top", color=INK)
    month_axis(axes[-1])
    axes[0].set_title("Daily requests for the top 5 agencies (grey: daily, colour: 7-day average)", pad=24)
    subtitle(axes[0], "Each panel has its own scale. Seasonal swings (HPD's winter heating season) "
                      "happen once a year, so one year of data can't teach a model them.")
    fig.tight_layout()
    fc.save_figure(fig, "f1d_agency_daily.png")


def plot_hotspots(wide, hot_wide):
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    for ax, name in zip(axes, [fc.TOTAL, "NYPD"]):
        ax.plot(wide.index, wide[name], color=ORANGE, linewidth=0.9, label="All requests")
        ax.plot(wide.index, wide[name] - hot_wide[name], color=PALETTE[0], linewidth=1.2,
                label="Excluding hotspot location-days")
        ax.set_ylim(0, wide[name].max() * 1.1)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        share = 100 * hot_wide[name].sum() / wide[name].sum()
        ax.text(0.005, 0.95, f"{name}: hotspots are {share:.1f}% of the year's requests",
                transform=ax.transAxes, fontsize=10, fontweight="bold", va="top", color=INK)
    month_axis(axes[-1])
    axes[0].set_title("Most spikes come from single-location bursts", pad=24)
    subtitle(axes[0], "Hotspot = one location filing more than 100 requests of one complaint type "
                      "in a single day (a same-day rule, no look-ahead).")
    axes[1].legend(loc="upper center", ncol=2, bbox_to_anchor=(0.5, -0.12))
    fig.tight_layout()
    fc.save_figure(fig, "f1e_hotspots.png")


# --- Main --------------------------------------------------------------------

def main():
    daily = fc.run_sql("01_daily_total.sql")
    daily["day"] = pd.to_datetime(daily["day"])
    by_agency = fc.run_sql("02_daily_top_agencies.sql")
    by_agency["day"] = pd.to_datetime(by_agency["day"])
    agencies = list(dict.fromkeys(by_agency["agency"]))  # SQL orders by volume
    by_type = fc.run_sql("03_daily_by_complaint_type.sql")
    by_type["day"] = pd.to_datetime(by_type["day"])
    hot = fc.run_sql("04_daily_hotspots.sql")
    hot["day"] = pd.to_datetime(hot["day"])
    profile = fc.run_sql("05_hotspot_profile.sql").iloc[0]
    hotspot_profile = {k: (v if isinstance(v, str) else round(float(v), 2)) for k, v in profile.items()}

    # One wide table: Total + one column per agency, every day of the year.
    wide = by_agency.pivot(index="day", columns="agency", values="requests")[agencies]
    wide.insert(0, fc.TOTAL, daily.set_index("day")["requests"])
    wide = wide.asfreq("D").astype(int)
    assert wide.notna().all().all() and len(wide) == 365
    fc.DAILY_FILE.parent.mkdir(parents=True, exist_ok=True)
    wide.to_csv(fc.DAILY_FILE)
    hot_wide = hotspot_table(hot, wide.index, wide.columns)
    hot_wide.to_csv(fc.HOTSPOT_FILE)
    for path in (fc.DAILY_FILE, fc.HOTSPOT_FILE):
        print(f"saved {path.relative_to(fc.PROJECT_ROOT)}")

    calendar = check_calendar(daily, "2025-01-01", "2025-12-31")
    type_wide = by_type.pivot(index="day", columns="complaint_type", values="requests") \
                       .reindex(wide.index).fillna(0)

    # Outliers in the total series, each with a reason: holiday name or the complaint type that moved most.
    flags = flag_outliers(wide[fc.TOTAL])
    type_baseline = same_weekday_baseline(type_wide)
    outliers = []
    for day, row in flags[flags["is_outlier"]].iterrows():
        driver, excess = outlier_driver(type_wide, type_baseline, day)
        outliers.append({
            "day": day.date(), "weekday": day.day_name(), "requests": int(row["requests"]),
            "baseline": round(row["baseline"]), "pct_vs_baseline": round(row["pct_vs_baseline"], 1),
            "robust_z": round(row["robust_z"], 2), "holiday": fc.US_HOLIDAYS.get(day),
            "top_complaint_type": driver, "top_type_excess": round(excess),
            "hotspot_requests": int(hot_wide.at[day, fc.TOTAL]),
        })
    holiday_flags = {str(d): {"name": name, "pct_vs_baseline": round(flags.at[pd.Timestamp(d), "pct_vs_baseline"], 1),
                         "robust_z": round(flags.at[pd.Timestamp(d), "robust_z"], 2)}
                     for d, name in sorted(fc.US_HOLIDAYS.items()) if pd.Timestamp(d) in flags.index}

    # Per-series summary: level, weekly shape, noise, outliers, monthly level.
    weekly, partial_weeks = complete_weeks(wide[fc.TOTAL])
    profiles = {name: weekday_profile(wide[name]) for name in wide.columns}
    series_summary = {}
    def noise_pct(f):
        """Typical day-to-day noise left after the weekly pattern: robust SD of
        log(count / same-weekday baseline), as a %."""
        log_ratio = np.log(f["requests"] / f["baseline"]).dropna()
        return round(100 * 1.4826 * (log_ratio - log_ratio.median()).abs().median(), 1)

    for name in wide.columns:
        f = flag_outliers(wide[name])
        f_excl = flag_outliers(wide[name] - hot_wide[name])
        monthly = wide[name].groupby(wide.index.month).mean()
        series_summary[name] = {
            "mean_per_day": round(wide[name].mean(), 1),
            "min": int(wide[name].min()), "max": int(wide[name].max()),
            "days_with_zero": int((wide[name] == 0).sum()),
            "noise_pct": noise_pct(f),
            "n_outlier_days": int(f["is_outlier"].sum()),
            "hotspot_requests": int(hot_wide[name].sum()),
            "hotspot_pct": round(100 * hot_wide[name].sum() / wide[name].sum(), 2),
            "hotspot_days": int((hot_wide[name] > 0).sum()),
            "noise_pct_excl_hotspots": noise_pct(f_excl),
            "n_outlier_days_excl_hotspots": int(f_excl["is_outlier"].sum()),
            "weekday_profile_pct": profiles[name].round(1).to_dict(),
            "monthly_index_pct": (100 * (monthly / wide[name].mean() - 1)).round(1).to_dict(),
        }

    episodes = outlier_episodes(outliers)
    flags_excl = flag_outliers(wide[fc.TOTAL] - hot_wide[fc.TOTAL])

    fc.save_json("phase1_data.json", {
        "series": list(wide.columns),
        "calendar": calendar,
        "partial_weeks_excluded": partial_weeks,
        "complete_weeks": len(weekly),
        "weekly_total": {"min": int(weekly.min()), "max": int(weekly.max()), "mean": round(weekly.mean())},
        "z_threshold": Z_THRESHOLD,
        "outliers_total": outliers,
        "outlier_episodes_total": episodes,
        "hotspot_profile": hotspot_profile,
        # Which outlier days remain once single-location bursts are subtracted
        "outlier_days_total_excl_hotspots": [d.date() for d in flags_excl.index[flags_excl["is_outlier"]]],
        "holidays_pct_vs_baseline": holiday_flags,
        "series_summary": series_summary,
    })

    # Label the 5 strongest episodes on the chart at their peak day.
    def label(ep):
        dates = f"{ep['start']:%b %d}" if ep["start"] == ep["end"] else f"{ep['start']:%b %d}-{ep['end']:%d}"
        mostly_hotspot = (ep["hotspot_share_of_excess_pct"] or 0) > 50
        return f"{dates}: " + ("one-location noise burst" if mostly_hotspot else ep["main_driver"])
    top = sorted(episodes, key=lambda e: -abs(e["peak_robust_z"]))[:5]
    labels = {pd.Timestamp(e["peak_day"]): label(e) for e in top}
    plot_daily_total(wide[fc.TOTAL], flags, labels)
    plot_weekly(weekly)
    plot_weekday_profiles(profiles)
    plot_agencies(wide, agencies)
    plot_hotspots(wide, hot_wide)
    report.write()


if __name__ == "__main__":
    main()
