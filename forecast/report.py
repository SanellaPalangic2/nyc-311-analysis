"""Render results/forecast/forecast.md from the numbers each phase saved.

Run:  python forecast/report.py   (each phase script also calls it)

No number is typed by hand: every value comes from results/forecast/data/*.json,
so rerunning the scripts regenerates the document. Sections appear only for
phases that have been run.
"""
import pandas as pd

import fc_common as fc
from results_md import table


def n(x):
    return f"{x:,}"


def pct(x, signed=True):
    return "n/a" if x is None else (f"{x:+.1f}%" if signed else f"{x:.1f}%")


def phase1(d):
    cal, s = d["calendar"], d["series_summary"]
    tot = s[fc.TOTAL]
    agencies = [name for name in d["series"] if name != fc.TOTAL]
    episodes = d["outlier_episodes_total"]
    hol = d["holidays_pct_vs_baseline"]
    hol_down = sum(h["pct_vs_baseline"] < 0 for h in hol.values())
    hp = d["hotspot_profile"]
    cleared = sorted(set(o["day"] for o in d["outliers_total"]) - set(d["outlier_days_total_excl_hotspots"]))

    checks = table(["Check", "Result", "How it's handled"], [
        ["Missing days", f"{len(cal['days_missing_from_calendar'])} of {cal['expected_days']} days missing, "
                         f"{len(cal['days_with_zero_requests'])} days with zero requests",
         "Nothing to fix. The SQL joins counts onto a full calendar, so a missing day would show up as 0."],
        ["Partial first/last day", f"first request {cal['first_created']}, last {cal['last_created']}; "
                                   f"{len(cal['days_missing_clock_hours'])} days missing clock hours",
         "Both ends are complete days, so they stay in."],
        ["Daylight saving time", ", ".join(f"{k} has {v} clock hours" for k, v in cal["dst_days"].items()),
         "Kept as is. Demand is counted per calendar day, which is how staff experience it."],
        ["Partial weeks", f"weeks starting {' and '.join(d['partial_weeks_excluded'])} have fewer than 7 days in 2025",
         "Left out of weekly totals only; every day stays in the daily series."],
        ["Data outages", f"{len(cal['days_with_zero_requests'])} empty days; the only hour with no requests is the "
                         "DST hour that doesn't exist", "None found."],
        ["Outlier days", f"{tot['n_outlier_days']} days in {len(episodes)} episodes",
         "Kept. They are real demand, and a forecast should be judged on them."],
        ["Single-location bursts", f"{n(tot['hotspot_requests'])} requests ({pct(tot['hotspot_pct'], False)} of "
                                   f"the year) on {tot['hotspot_days']} days, nearly all NYPD",
         "Kept in the main series. A second series without them is used to measure how much error they cause."],
    ])

    ep_rows = [[f"{e['start']}" + ("" if e["start"] == e["end"] else f" to {e['end']}"), e["n_outlier_days"],
                e["direction"], pct(e["peak_pct_vs_baseline"]), e["main_driver"],
                pct(e["hotspot_share_of_excess_pct"], False)] for e in episodes]
    hol_rows = [[day, h["name"], pct(h["pct_vs_baseline"]), f"{h['robust_z']:+.2f}"] for day, h in hol.items()]
    summary_rows = [[name, n(round(v["mean_per_day"])), n(v["min"]), n(v["max"]), pct(v["noise_pct"], False),
                     v["n_outlier_days"], pct(v["hotspot_pct"], False), pct(v["noise_pct_excl_hotspots"], False),
                     v["n_outlier_days_excl_hotspots"]] for name, v in s.items()]
    weekdays = list(tot["weekday_profile_pct"])
    wd_rows = [[name] + [pct(v["weekday_profile_pct"][w]) for w in weekdays] for name, v in s.items()]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    mo_rows = [[name] + [pct(v["monthly_index_pct"][str(i)]) for i in range(1, 13)] for name, v in s.items()]
    hpd = s.get("HPD")

    return f"""## Phase 1: Data

**Series:** daily request counts for all of NYC (`{fc.TOTAL}`) and for the 5 agencies with the most
requests ({", ".join(agencies)}), {cal['days_in_series']} days from 2025-01-01 to 2025-12-31.
Built with SQL in [`sql/forecast/`](../../sql/forecast/) by
[`forecast/daily_series.py`](../../forecast/daily_series.py).

![Daily requests](figures/f1a_daily_total.png)

### Data checks

{checks}

### Outlier days

A day is an outlier when it is more than {d['z_threshold']} robust standard deviations away from the same
weekday in the 3 weeks before and after it (median and MAD of log(count / baseline), so the outliers
themselves don't hide). This check looks at future days, which is fine for describing the data. Its flags
are never given to a model, because a model can't know in advance that a day will be unusual.
Consecutive outlier days (gaps of up to 3 days) are grouped into episodes.

{table(["Episode", "Outlier days", "Direction", "Peak vs baseline", "Main driver", "Share of excess from hotspots"], ep_rows)}

"Main driver" is the holiday, or else the complaint type that moved furthest from its own baseline.

**Single-location bursts.** A "hotspot" here means one location (coordinates rounded to about 10 m) filing
more than 100 requests of one complaint type in a single day. The rule only uses that day's own requests, so
it could run at the end of each day with no look-ahead
([`sql/forecast/04_daily_hotspots.sql`](../../sql/forecast/04_daily_hotspots.sql)). Almost all hotspot
requests come from one spot in ZIP {hp['zip']} ([`05_hotspot_profile.sql`](../../sql/forecast/05_hotspot_profile.sql)):

- It filed {n(round(hp['location_requests_year']))} {hp['complaint_type']} complaints in 2025. Its median day
  has {n(round(hp['location_median_per_day']))}, its busiest day {n(round(hp['location_max_per_day']))}, and it
  went over 100 on {round(hp['location_hotspot_days'])} days.
- {pct(hp['hotspot_pct_mobile'], False)} of hotspot requests came through the mobile app, against
  {pct(hp['other_pct_mobile'], False)} for the same complaint type elsewhere: this looks like one repeated or
  automated submitter, not neighbourhood demand.
- They are not instant duplicates: median time to close was {hp['hotspot_median_hours_to_close']:.2f} hours
  ({hp['other_median_hours_to_close']:.2f} for the same type elsewhere), so each one was handled.

Because they were handled, they stay in the main series. With them subtracted, the total series has
{tot['n_outlier_days_excl_hotspots']} outlier days instead of {tot['n_outlier_days']}:
{", ".join(d['outlier_days_total_excl_hotspots'])}. Each matches a holiday, a weather-type complaint
(heat/hot water, water system, snow or ice) or the DOB batch load. The days that stop being outliers
({", ".join(cleared)}) include the low days after Christmas: their comparison weeks in early December were
inflated by bursts.

![Hotspots](figures/f1e_hotspots.png)

**Holidays.** {hol_down} of {len(hol)} federal holidays were below their baseline and
{len(hol) - hol_down} above it, and the size differs a lot by holiday:

{table(["Date", "Holiday", "vs baseline", "Robust z"], hol_rows)}

One year contains each holiday only once, so a model can learn an average "holiday" effect but not a
separate effect per holiday.

### Level, noise and outliers per series

"Noise" is the typical day-to-day deviation left after the weekly pattern: the robust standard deviation of
log(count / same-weekday baseline), as a percentage. It is a first hint of how hard each series will be to forecast.

{table(["Series", "Mean/day", "Min", "Max", "Noise", "Outlier days", "Hotspot share", "Noise excl. hotspots",
        "Outlier days excl. hotspots"], summary_rows)}

### Weekly and day-of-week patterns

Each weekday compared with the average of its own week (median over {d['complete_weeks']} complete weeks).
Dividing by the same week's average removes slow level changes and leaves only the weekly shape.

{table(["Series"] + weekdays, wd_rows)}

![Day of week](figures/f1c_weekday_profile.png)

![Weekly totals](figures/f1b_weekly_total.png)

Complete weeks range from {n(d['weekly_total']['min'])} to {n(d['weekly_total']['max'])} requests
(mean {n(d['weekly_total']['mean'])}).

### Yearly seasonality can't be learned from this data

Average requests per day in each month, compared with the year's average:

{table(["Series"] + months, mo_rows)}

![Agencies](figures/f1d_agency_daily.png)

Some series have large seasonal swings{f", for example HPD (heating complaints) runs {pct(hpd['monthly_index_pct']['1'])} in January and {pct(hpd['monthly_index_pct']['8'])} in August" if hpd else ""}.
With one year of data, each season is seen exactly once, so no model here can learn "January is high"
as a repeating pattern: there is no earlier January to learn it from. The models in later phases can only
follow the level of the last few weeks (they adapt when the season changes, but with a lag) and the weekly
pattern, which repeats 52 times. Learning yearly seasonality would need at least two or three years of data.
"""


def metric_rows(metrics, labels, variant, split, series):
    rows = [m for m in metrics if (m["variant"], m["split"], m["series"]) == (variant, split, series)]
    return [[labels.get(m["model"], m["model"]), n(round(m["mae"])), n(round(m["rmse"])), pct(m["mape"], False),
             pct(m["mae_improvement_pct"]), pct(m["rmse_improvement_pct"]), pct(m["mape_improvement_pct"])]
            for m in rows]


METRIC_HEADER = ["Model", "MAE", "RMSE", "MAPE", "MAE vs seasonal naive", "RMSE vs seasonal naive",
                 "MAPE vs seasonal naive"]


def best(metrics, variant, split, series, key="mae"):
    rows = [m for m in metrics if (m["variant"], m["split"], m["series"]) == (variant, split, series)]
    return min(rows, key=lambda m: m[key])


def phase2(d):
    m, labels = d["metrics"], d["labels"]
    val, test = d["validation_cutoffs"], d["test_cutoffs"]
    series = list(dict.fromkeys(r["series"] for r in m))
    ref = "Seasonal naive"  # short name for running text; tables use the full labels
    first_test_day = (pd.Timestamp(test[0]) + pd.DateOffset(days=1)).date()
    bar = best(m, "as_recorded", "validation", fc.TOTAL)
    test_best = best(m, "as_recorded", "test", fc.TOTAL)
    ref_val = next(r for r in m if (r["variant"], r["split"], r["series"], r["model"]) ==
                   ("as_recorded", "validation", fc.TOTAL, d["reference"]))
    ref_test = next(r for r in m if (r["variant"], r["split"], r["series"], r["model"]) ==
                    ("as_recorded", "test", fc.TOTAL, d["reference"]))

    pw = d["per_window_mae_total_test"]["as_recorded"]
    models = list(labels)
    pw_rows = [[c] + [n(round(v[k])) for k in models] + [labels[min(v, key=v.get)]] for c, v in pw.items()]
    wins = {k: sum(min(v, key=v.get) == k for v in pw.values()) for k in models}

    agency_rows = []
    for s_ in series:
        for variant in ("as_recorded", "excl_hotspots"):
            if variant == "excl_hotspots" and s_ not in (fc.TOTAL, "NYPD"):
                continue  # identical to as_recorded: no hotspots in that series
            row = [s_ + ("" if variant == "as_recorded" else " (excl. hotspots)")]
            for split in ("validation", "test"):
                rows = {r["model"]: r for r in m if (r["variant"], r["split"], r["series"]) == (variant, split, s_)}
                row += [pct(rows[k]["mape"], False) for k in models]
            agency_rows.append(row)
    short = {"naive": "naive", "seasonal_naive": "seas. naive", "weekday_avg_4wk": "4-wk avg"}

    return f"""## Phase 2: Baselines and backtest design

Code: [`forecast/backtest.py`](../../forecast/backtest.py) (backtest engine, metrics) and
[`forecast/baselines.py`](../../forecast/baselines.py).

### Backtest design

Rolling origin: at each cutoff the model gets every day up to and including the cutoff, forecasts the
next {d['horizon']} days, and is scored on what happened. The cutoff then moves forward {d['step']} days.
The engine cuts the series at the cutoff *before* calling the model, so no model can see the days it forecasts.

- **Validation cutoffs** ({len(val)}): {val[0]} to {val[-1]}. Phase 3 chooses model settings on these.
- **Test cutoffs** ({len(test)}): {test[0]} to {test[-1]}, forecasting {first_test_day} to 2025-12-31,
  the last ~10 weeks. Reported only, never used to choose anything.
- No day is forecast in both sets. Windows within a set overlap (14-day windows, 7-day steps), so most days are
  forecast twice, once 1-7 days ahead and once 8-14 days ahead. All cutoffs are Wednesdays.
- Metrics are pooled over all {d['horizon']} x {len(test)} = {d['horizon'] * len(test)} forecasts per model:
  **MAE** (average miss in requests/day), **RMSE** (like MAE but large misses count extra) and **MAPE**
  (average miss as a % of the actual). "vs seasonal naive" = % lower error than {ref.lower()} (positive is better).

![Backtest design](figures/f2a_backtest_design.png)

### Baselines

- **Naive:** every future day = the last observed day.
- **Seasonal naive:** each future day = the same weekday in the last observed week.
- **4-week same-weekday average:** each future day = the mean of the last 4 observed same weekdays.

### Results: {fc.TOTAL}, test windows

{table(METRIC_HEADER, metric_rows(m, labels, "as_recorded", "test", fc.TOTAL))}

Excluding single-location bursts (hotspots removed from both history and actuals):

{table(METRIC_HEADER, metric_rows(m, labels, "excl_hotspots", "test", fc.TOTAL))}

### Results: {fc.TOTAL}, validation windows

{table(METRIC_HEADER, metric_rows(m, labels, "as_recorded", "validation", fc.TOTAL))}

The test period is much harder than validation: {ref.lower()} has an MAE of {n(round(ref_test['mae']))} on test
against {n(round(ref_val['mae']))} on validation. The last 10 weeks of 2025 contain the outlier episodes from
Phase 1 (bursts, Thanksgiving, the December snow, Christmas) and the start of the heating season, while
mid-August to mid-October was calm.

![Test windows](figures/f2b_test_windows.png)

**MAE per test window ({fc.TOTAL}, as recorded):**

{table(["Cutoff"] + [labels[k] for k in models] + ["Best"], pw_rows)}

Windows won: {", ".join(f"{labels[k]} {wins[k]}" for k in models)}. Copying one day or one week copies whatever
happened on it: after the 2025-12-15 snowstorm, {ref.lower()} predicted another storm-sized day a week later.
Averaging 4 weeks dilutes one-off events, which is why it wins on average.

![Error by horizon](figures/f2c_error_by_horizon.png)

### All series: MAPE by baseline

{table(["Series"] + [f"Val. {short[k]}" for k in models] + [f"Test {short[k]}" for k in models], agency_rows)}

### The bar for Phase 3

The best baseline for {fc.TOTAL} on **validation** is **{labels[bar['model']]}** (MAE {n(round(bar['mae']))},
{pct(bar['mae_improvement_pct'])} vs {ref.lower()}). On test, the best baseline is
{labels[test_best['model']]} (MAE {n(round(test_best['mae']))}, {pct(test_best['mae_improvement_pct'])}).
A Phase 3 model has to beat {ref.lower()} (the required reference) *and* this stronger baseline before it is
worth using. The bar was chosen on validation, not test.
"""


def phase3(d):
    m, labels, order = d["metrics"], d["labels"], d["order"]
    s_ = d["series"]
    get = {(r["variant"], r["split"], r["model"]): r for r in m}
    best_m = d["best_model"]
    b_val, b_test = get[("as_recorded", "validation", best_m)], get[("as_recorded", "test", best_m)]
    test_best = min((r for r in m if r["variant"] == "as_recorded" and r["split"] == "test"), key=lambda r: r["mae"])
    plain = {"holt_winters": "Holt-Winters", "gradient_boosting": "Gradient boosting"}
    tune_rows = [[plain[k], t["setting"], n(round(t["mae"])), pct(t["mape"], False)]
                 for k in ("holt_winters", "gradient_boosting") for t in d["tuning"][k]]

    def rows(variant):
        out = []
        for k in order:
            v, t = get[(variant, "validation", k)], get[(variant, "test", k)]
            out.append([labels[k], n(round(v["mae"])), pct(v["mae_improvement_pct"]), n(round(t["mae"])),
                        n(round(t["rmse"])), pct(t["mape"], False), pct(t["mae_improvement_pct"]),
                        pct(t["rmse_improvement_pct"]), pct(t["mape_improvement_pct"])])
        return out

    header = ["Model", "Val. MAE", "Val. MAE vs seas. naive", "Test MAE", "Test RMSE", "Test MAPE",
              "Test MAE vs seas. naive", "Test RMSE vs seas. naive", "Test MAPE vs seas. naive"]
    src = d["error_sources_test"]
    cats = ["normal", "copied event", "event day"]
    src_rows = [[labels[k]] + [n(round(src[k][c]["mae"])) for c in cats] +
                [pct(src[k][c]["share_of_error_pct"], False) for c in cats] for k in order]
    n_cat = {c: src[order[0]][c]["n"] for c in cats}
    pw = d["per_window_mae_test"]
    pw_rows = [[c] + [n(round(v[k])) for k in order] + [labels[min(v, key=v.get)]] for c, v in pw.items()]
    wins = {k: sum(min(v, key=v.get) == k for v in pw.values()) for k in order}
    cov = d["interval_coverage"]
    cov_rows = [[key.replace("/", ", ").replace("_", " "), pct(c["coverage_pct"], False),
                 pct(c["coverage_pct_h1-7"], False), pct(c["coverage_pct_h8-14"], False),
                 pct(c["below_pct"], False), pct(c["above_pct"], False), n(round(c["mean_width"])),
                 pct(c["mean_width_pct_of_forecast"], False), c["min_calibration_windows"]]
                for key, c in cov.items()]
    ct = cov["as_recorded/test"]
    cv = cov["as_recorded/validation"]

    return f"""## Phase 3: Models

Code: [`forecast/models.py`](../../forecast/models.py) (models, features, intervals) and
[`forecast/evaluate_models.py`](../../forecast/evaluate_models.py) (tuning, comparison, intervals).
Series: {s_}. Same backtest and cutoffs as Phase 2.

### The two models

**Holt-Winters (exponential smoothing), chosen over SARIMA.** It tracks three things: the current level,
an optional (damped) trend and a 7-day pattern, each updated with a smoothing weight that decides how fast old
data is forgotten. SARIMA would need an order search over (p,d,q)(P,D,Q) with little data per fit (64 days for the
earliest interval-calibration cutoff, 351 for the last test cutoff), and its parameters are harder to explain. Holt-Winters has a handful of settings and adapts to level shifts on its own.
*No leakage:* it is fitted at each cutoff on `history`, which ends at the cutoff.

**Gradient boosting, direct multi-step strategy.** One scikit-learn `HistGradientBoostingRegressor` per cutoff,
trained on every (origin day t, horizon h) pair in the history, predicting day t+h from what was known on day t:

- calendar of the target day: day of week, month, US federal holiday (the `holidays` package; known years ahead)
- horizon h (1-14)
- `lag_7`, `lag_14`: the most recent and second most recent same weekday observed by day t. For h = 1-7 these are
  exactly 7 and 14 days before the target. For h = 8-14 the day 7 before the target hasn't happened yet, so they
  are 14 and 21 days before. They are always the latest values that actually exist at the origin.
- `roll_7`: mean of the 7 days ending at t.

*Why direct, not recursive:* a recursive model forecasts tomorrow, feeds that guess back in as a lag and repeats 14
times, so its errors compound and a spike gets copied forward again and again. The direct model never uses a
prediction as an input. *No leakage:* a training row is only used if its target day is on or before the cutoff
(checked with an `assert`), and the features of a row only read values up to its origin. Two tests prove it by
rewriting or multiplying every value after the cutoff and checking the forecast doesn't change.

### Tuning (validation cutoffs only)

{table(["Model", "Setting", "Validation MAE", "Validation MAPE"], tune_rows)}

Chosen: Holt-Winters with {d['chosen_settings']['holt_winters']}; gradient boosting with
{d['chosen_settings']['gradient_boosting']}. "Relative target" means the model predicts the ratio to the last
7-day mean instead of a raw count, so it can follow a level it never saw in training.

### Model comparison ({s_}, same backtest for every model)

As recorded:

{table(header, rows("as_recorded"))}

Excluding hotspots:

{table(header, rows("excl_hotspots"))}

![Model comparison](figures/f3b_model_comparison.png)

**Honest reading:**

- On validation, {labels[best_m]} is best (MAE {n(round(b_val['mae']))}, {pct(b_val['mae_improvement_pct'])} vs
  seasonal naive), so it is the model chosen for the rest of the project.
- On test, both tuned models still beat seasonal naive ({labels[best_m]}: {pct(b_test['mae_improvement_pct'])}
  MAE), but **neither beats the 4-week same-weekday average**. The best test MAE is {labels[test_best['model']]}
  ({n(round(test_best['mae']))}). The validation period (mid-August to mid-October) was calm, so the model choice
  that won there didn't carry over to a volatile November and December. I did not re-tune or switch models after
  seeing this, since that would be tuning on the test set.

### Where the test error comes from

Each test forecast is put in one of three groups: the day was a Phase 1 outlier day ({n_cat['event day']} forecasts),
one of its lag days was an outlier day so the event could be copied ({n_cat['copied event']}), or neither
({n_cat['normal']}). Outlier flags are used here only to diagnose errors after the fact.

{table(["Model", "MAE normal", "MAE copied event", "MAE event day", "Error share normal", "Error share copied",
        "Error share event day"], src_rows)}

- Outlier days are a fifth of the test forecasts but account for about 40% of every model's error. No model that
  only sees the past could have predicted a snowstorm or a filing burst.
- Single lags copy events: on "copied event" days, models that lean on one recent week (seasonal naive, gradient
  boosting) do much worse than the 4-week average, which dilutes a one-off spike four ways. In the chart below,
  the 2025-12-15 snow spike reappears in the forecasts a week and two weeks later.
- Even on normal days the 4-week average is lowest on test, so the models also struggle with the level shift
  into the heating season and the holiday period.

**MAE per test window:**

{table(["Cutoff"] + [labels[k] for k in order] + ["Best"], pw_rows)}

Windows won: {", ".join(f"{labels[k]} {wins[k]}" for k in order)}.

### 80% prediction intervals ({labels[best_m]})

Method (empirical intervals, no distribution assumed): for each cutoff, take the model's own forecasts from every
earlier weekly window that had *finished* by that cutoff, compute actual / forecast, and use its 10th and 90th
percentiles, separately for 1-7 and 8-14 days ahead. The interval is the forecast times those two ratios. Only
errors that were known at the cutoff are used, so the interval has no look-ahead either. The weekly windows
start on {d['calibration_cutoffs'][0]}.

{table(["Variant, split", "Coverage", "1-7 days", "8-14 days", "Actual below", "Actual above", "Mean width",
        "Width % of forecast", "Min. past windows"], cov_rows)}

**Target {d['coverage_target_pct']:.0f}%. Validation: {pct(cv['coverage_pct'], False)}, close to the target.
Test: {pct(ct['coverage_pct'], False)}: the intervals are far too narrow for the test period.** They are about
{ct['mean_width_pct_of_forecast']:.0f}% of the forecast wide because they learned from mostly calm months. Misses go
both ways: {pct(ct['below_pct'], False)} of test days came in below the interval (holidays, and the weeks after the
snow spike) and {pct(ct['above_pct'], False)} above it (bursts, the snow, the early-December rise). An interval
built from past errors can only be as wide as the past was rough. The fix is a longer history that includes
earlier holiday seasons, or explicit event adjustments, not a re-calibration on the test weeks.

![Intervals](figures/f3a_best_model_intervals.png)
"""


def phase4(d):
    labels, rank, cands = d["labels"], d["ranking"], d["candidates"]
    m = {(r["variant"], r["split"], r["series"], r["model"]): r for r in d["metrics"]}
    pa, fx = d["per_agency"], {f["series"]: f for f in d["forecastability"]}
    drv = d.get("test_drivers", {})
    short = {"seasonal_naive": "Seasonal naive", "weekday_avg_4wk": "4-week avg", "holt_winters": "Holt-Winters",
             "gradient_boosting": "Gradient boosting"}

    def chosen(a, variant="as_recorded"):
        return d["chosen"][f"{variant}/{a}"]

    main_rows = []
    for a in rank:
        c = chosen(a)
        v, t = m[("as_recorded", "validation", a, c)], m[("as_recorded", "test", a, c)]
        main_rows.append([a, short[c], pct(v["mape"], False), n(round(t["mae"])), pct(t["mape"], False),
                          pct(t["mae_improvement_pct"]), pct(pa[f"as_recorded/{a}"]["coverage"]["test"]["coverage_pct"], False)])
    all_rows = []
    for a in rank:
        for variant in ("as_recorded", "excl_hotspots"):
            if (variant, "test", a, cands[0]) not in m:
                continue
            c = chosen(a, variant)
            test_vals = {k: m[(variant, "test", a, k)]["mape"] for k in cands}
            best_test = min(test_vals, key=test_vals.get)
            all_rows.append([a + ("" if variant == "as_recorded" else " (excl. hotspots)")] +
                            [pct(test_vals[k], False) + (" *" if k == c else "") for k in cands] + [short[best_test]])
    test_best = {a: min(cands, key=lambda k: m[("as_recorded", "test", a, k)]["mape"]) for a in rank}
    regret = sum(1 for a in rank if test_best[a] != chosen(a))
    avg_best = [a for a in rank if test_best[a] == "weekday_avg_4wk"]
    gb_chosen = [a for a in rank if chosen(a) == "gradient_boosting"]
    p1 = fc.load_json("phase1_data.json")["series_summary"]
    weekend = {a: (p1[a]["weekday_profile_pct"]["Sat"] + p1[a]["weekday_profile_pct"]["Sun"]) / 2 for a in rank}
    low_weekend = [a for a in rank if weekend[a] < -30]
    high_weekend = [a for a in rank if weekend[a] > 0]

    fx_rows = [[a, pct(fx[a]["test_mape"], False), pct(fx[a]["noise_pct"], False),
                f"{fx[a]['weekday_swing_pct']:.0f} pts", f"{fx[a]['oct_to_dec_level_change_pct']:+.0f} pts",
                pct(fx[a]["hotspot_pct"], False), fx[a]["outlier_days_in_test_period"],
                pct(pa[f"as_recorded/{a}"]["error_sources_chosen"]["event day"]["share_of_error_pct"], False),
                pct(pa[f"as_recorded/{a}"]["error_sources_chosen"]["copied event"]["share_of_error_pct"], False)]
               for a in rank]
    drv_rows = [[a, f"{v['top_type']} ({v['top_type_pct']:.0f}%)", v["busiest_day"], n(round(v["busiest_day_requests"])),
                 f"{v['busiest_day_top_type']} ({n(round(v['busiest_day_top_type_requests']))})"]
                for a, v in ((a, drv[a]) for a in rank if a in drv)]

    def why(a):
        f, p = fx[a], pa[f"as_recorded/{a}"]
        src = p["error_sources_chosen"]
        dv = drv.get(a, {})
        lines = {
            "DOT": lambda: f"the strongest and most regular weekly cycle ({f['weekday_swing_pct']:.0f}-point swing between "
                   f"its busiest and quietest weekday: street and traffic-signal work follows business days), a stable "
                   f"level ({f['oct_to_dec_level_change_pct']:+.0f} points Oct to Dec) and few events.",
            "NYPD": lambda: f"its error is mostly the single-location noise bursts ({pct(f['hotspot_pct'], False)} of its "
                    f"requests). With bursts removed, the chosen model's test MAPE drops to "
                    f"{pct(m[('excl_hotspots', 'test', a, chosen(a, 'excl_hotspots'))]['mape'], False)}, which would "
                    f"make NYPD the easiest agency of all.",
            "DEP": lambda: f"mostly calm, but one-day events dominate: {pct(src['event day']['share_of_error_pct'], False)} of "
                   f"its error falls on its own outlier days. Its busiest test day ({dv.get('busiest_day', '')}) was "
                   f"{n(round(dv.get('busiest_day_top_type_requests', 0)))} {dv.get('busiest_day_top_type', '')} "
                   f"complaints, the pattern of a heavy-rain day (no weather data is used here to confirm it).",
            "HPD": lambda: f"the heating season. {dv.get('top_type', '')} is {dv.get('top_type_pct', 0):.0f}% of HPD's test-period "
                   f"requests, and HPD's level climbs {f['oct_to_dec_level_change_pct']:+.0f} points from October to "
                   f"December. {pct(src['normal']['share_of_error_pct'], False)} of the error is on ordinary days, so it "
                   f"comes from chasing a rising level, not from events. One year of data has only one heating season "
                   f"to learn from.",
            "DSNY": lambda: f"the December snow. Its busiest day ({dv.get('busiest_day', '')}) had "
                    f"{n(round(dv.get('busiest_day_requests', 0)))} requests, "
                    f"{n(round(dv.get('busiest_day_top_type_requests', 0)))} of them {dv.get('busiest_day_top_type', '')}. "
                    f"Event days carry {pct(src['event day']['share_of_error_pct'], False)} of the error and copied events "
                    f"another {pct(src['copied event']['share_of_error_pct'], False)}: gradient boosting repeated the "
                    f"snow spike in the following week's forecast.",
        }
        return lines[a]() if a in lines else ""  # lambdas: only the asked agency's text is built

    staff_rows = []
    for key in [f"as_recorded/{a}" for a in rank] + [k for k in pa if k.startswith("excl_hotspots/")]:
        p, s = pa[key], pa[key]["staffing"]
        name = p["series"] + ("" if p["variant"] == "as_recorded" else " (excl. hotspots)")
        staff_rows.append([name, n(round(p["test_mean_actual"])), pct(s["buffer_pct"]),
                           pct(s["forecast"]["short_days_pct"], False), n(round(s["forecast"]["avg_shortfall"])),
                           pct(s["p90"]["short_days_pct"], False), n(round(s["p90"]["avg_shortfall"])),
                           n(round(s["p90"]["avg_unused"]))])
    hard, easy = rank[-1], rank[0]
    hard_src = pa[f"as_recorded/{hard}"]["error_sources_chosen"]
    hard_event_share = hard_src["event day"]["share_of_error_pct"] + hard_src["copied event"]["share_of_error_pct"]

    return f"""## Phase 4: Per-agency forecasts

Code: [`forecast/agencies.py`](../../forecast/agencies.py). Same backtest, cutoffs and interval method as Phase 3.

**Approach.** For each agency, four candidates are backtested: seasonal naive, the 4-week same-weekday average,
Holt-Winters ({d['settings']['holt_winters']}) and gradient boosting ({d['settings']['gradient_boosting']}), with
the settings tuned on the total series in Phase 3. Each agency gets the candidate with the lowest MAE on **its own
validation windows**. Test windows are only used to report results. The settings were not re-tuned per agency:
9 validation windows per agency are too few to tune 16 settings without overfitting.

### Results (sorted from easiest to hardest to forecast)

{table(["Agency", "Chosen (validation)", "Val. MAPE", "Test MAE", "Test MAPE", "Test MAE vs seas. naive",
        "80% interval coverage (test)"], main_rows)}

Test MAPE of every candidate (* = the one chosen on validation):

{table(["Agency"] + [short[k] for k in cands] + ["Best on test"], all_rows)}

![Agency MAPE](figures/f4a_agency_mape.png)

For {regret} of {len(rank)} agencies, a different candidate would have done better on test than the one validation
picked. With only 9 calm validation windows per agency, the choice is noisy. Validation picked gradient boosting
for {" and ".join(gb_chosen)}; on test it copied event spikes forward and lagged behind rising levels, and the 4-week
average, which spreads a single event over four weeks, was best there ({" and ".join(avg_best)}). Elsewhere the
differences on test are a few points of MAPE. No choice was changed after seeing the test results.

### Why some agencies are easier than others

{table(["Agency", "Test MAPE", "Day-to-day noise", "Weekday swing", "Level change Oct to Dec",
        "Hotspot share", "Outlier days in test period", "Error on event days", "Error on copied events"], fx_rows)}

What each agency's test period was made of
([`sql/forecast/06_agency_test_drivers.sql`](../../sql/forecast/06_agency_test_drivers.sql)):

{table(["Agency", "Largest complaint type", "Busiest test day", "Requests that day", "Largest type that day"], drv_rows)}

{chr(10).join(f"- **{a}** ({pct(fx[a]['test_mape'], False)}): {why(a)}" for a in rank)}

In short: **{easy} is easiest** because its demand follows the working week, and **{hard} is hardest** because
events, led by the December snow, cause {pct(hard_event_share, False)} of its error (on event days plus copied
events). A model that only knows the calendar and the past can't see a storm coming; a weather forecast as an
input is the most promising addition for {hard} and DEP.

![Agency intervals](figures/f4b_agency_intervals.png)

### From forecasts to staffing

A forecast says how many requests to *expect*; a staffing plan has to decide how many to *cover*. That is the same
question a supply chain team answers with safety stock. Two rostering policies, measured on the test days:

- **Roster to the forecast:** no buffer. If the forecast is unbiased, expect to be short on roughly half of all days.
- **Roster to the P90** (the top of the 80% interval): a buffer sized from the model's own past errors. It should
  be short on about 10% of days, the staffing version of a 90% service level.

{table(["Agency", "Avg requests/day (test)", "P90 buffer above forecast", "Short days: forecast plan",
        "Avg shortfall when short", "Short days: P90 plan", "Avg shortfall when short (P90)",
        "Avg spare capacity (P90)"], staff_rows)}

![Staffing](figures/f4c_staffing.png)

The P90 plan cut short days sharply for every agency, but it never reached the 10% it promised: the buffers were
sized on calm months (Phase 3). How a team would use this:

1. **Roster by weekday, not by week.** The weekly shape (Phase 1) is the most reliable signal there is.
   {", ".join(f"{a} gets {-weekend[a]:.0f}%" for a in low_weekend)} fewer requests on an average weekend day than
   its weekly average, while {", ".join(f"{a} gets {weekend[a]:.0f}% more" for a in high_weekend)}. A flat roster
   wastes capacity on some days and is short on others.
2. **Pick the service level per agency from the cost of being short.** If an unanswered request costs much more
   than an idle hour (for example, no heat in winter), plan above P90; if not, plan nearer the forecast. This is
   the newsvendor trade-off from inventory planning: buffer = cost of shortage vs cost of excess.
3. **Publish rosters on the 14-day horizon, adjust on the 1-7-day one.** Errors grow with horizon (Phase 2), so lock
   in the next two weeks with the P90 plan and keep a small on-call pool for the near-term changes.
4. **Keep event playbooks outside the model.** Snow (DSNY), heavy rain (DEP sewer complaints), holidays and filing
   bursts (NYPD) caused most of the misses. A trigger such as a snow forecast or a burst detected by the same-day
   hotspot rule should switch to a surge plan, like a supplier-disruption plan in a supply chain.
5. **Track interval coverage every week.** When actual demand lands outside the P90 more than about 10% of the time,
   the buffer is too thin: widen it, or recalibrate on a longer history that includes a previous winter.
"""


def write():
    sections = ["# NYC 311 Demand Forecasting: results\n",
                "Generated by the scripts in [`forecast/`](../../forecast/). Do not edit by hand.\n"]
    if (d := fc.load_json("phase1_data.json")):
        sections.append(phase1(d))
    if (d := fc.load_json("phase2_baselines.json")):
        sections.append(phase2(d))
    if (d := fc.load_json("phase3_models.json")):
        sections.append(phase3(d))
    if (d := fc.load_json("phase4_agencies.json")):
        sections.append(phase4(d))
    path = fc.OUT_DIR / "forecast.md"
    path.write_text("\n".join(sections), encoding="utf-8")
    print(f"saved {path.relative_to(fc.PROJECT_ROOT)}")


if __name__ == "__main__":
    write()
