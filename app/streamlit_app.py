"""NYC 311 resolution times: public dashboard.

Run locally:  streamlit run app/streamlit_app.py

Reads only the small pre-aggregated files in app/data/ (built by
analysis/build_app_data.py). It never connects to a database and needs no
secrets or credentials, so it can run on a public server.
"""
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

DATA = Path(__file__).parent / "data"

# Same validated palette as the static charts; series colors in fixed order.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
BLUE, ORANGE, GRAY, INK = PALETTE[0], PALETTE[1], "#898781", "#0b0b0b"

# Log-scale time axis: ticks at familiar durations instead of raw hours.
TIME_TICKS = [1 / 60, 1, 24, 168, 720, 8760]
TIME_LABELS = ("datum.value < 0.1 ? '1 min' : datum.value == 1 ? '1 hour' : "
               "datum.value == 24 ? '1 day' : datum.value == 168 ? '1 week' : "
               "datum.value == 720 ? '30 days' : datum.value == 8760 ? '1 year' : ''")

st.set_page_config(page_title="NYC 311 Resolution Times", page_icon="🗽", layout="wide")


@st.cache_data
def load():
    summary = json.loads((DATA / "summary.json").read_text(encoding="utf-8"))
    return (pd.read_parquet(DATA / "cube.parquet"), pd.read_parquet(DATA / "km_curves.parquet"),
            pd.read_parquet(DATA / "km_medians.parquet"), summary)


cube, km_curves, km_medians, summary = load()
k = summary["key_numbers"]
model = summary["model"]


def time_axis(title):
    return alt.Axis(title=title, values=TIME_TICKS, labelExpr=TIME_LABELS, grid=True)


def agency_styles():
    """Fixed color per agency (by 2025 volume), so an agency keeps its color
    whatever else is selected. 15 agencies but 8 colors: ranks 9-15 reuse the
    colors with a dashed line (color + dash together tell them apart)."""
    order = summary["agency_volume_order"]
    colors = {a: PALETTE[i % 8] for i, a in enumerate(order)}
    dashes = {a: [1, 0] if i < 8 else [6, 3] for i, a in enumerate(order)}
    colors["All agencies"], dashes["All agencies"] = INK, [2, 2]
    return colors, dashes


# --- Pages -------------------------------------------------------------------------

def page_overview():
    st.title("NYC 311 resolution times, 2025")
    st.caption(f"NYC Open Data, 311 Service Requests (erm2-nwe9): requests created "
               f"Jan 1 - Dec 31, 2025. Data snapshot: {summary['snapshot']}.")
    total = cube[(cube.agency == "All") & (cube.borough == "All") & (cube.season == "All")].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Requests", f"{int(total.n_requests):,}")
    c2.metric("Median time to close", f"{total.median_hours:,.2f} h",
              help="Closed requests only.")
    c3.metric("Median incl. open requests", f"{k['overall_km']:,.2f} h",
              help="Kaplan-Meier estimate: still-open requests are counted as censored "
                   "instead of being dropped.")
    c4.metric("Closed within 30 days", f"{total.pct_closed_within_30d:.2f}%",
              help="Still-open requests count as not closed.")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Still open at snapshot", f"{k['censored']:,}")
    c6.metric("Complaint types", f"{summary['n_complaint_types']}")
    c7.metric("Agencies", f"{k['n_agencies']}")
    c8.metric("7-day model ROC AUC", f"{k['best_auc']:.4f}",
              help=f"{k['best_model']} vs {k['baseline_auc']:.4f} for a majority-class baseline.")

    st.subheader("What drives slow resolution?")
    eps = pd.DataFrame([{"factor": f.title(), "epsilon_squared": v}
                        for f, v in summary["eps"].items()]).sort_values("epsilon_squared")
    top, nxt = eps.iloc[-1], eps.iloc[-2]
    st.write(f"Kruskal-Wallis effect sizes on resolution time: **{top.factor.lower()}** explains "
             f"{100 * top.epsilon_squared:.2f}% of the (ranked) variation, "
             f"{top.epsilon_squared / nxt.epsilon_squared:,.1f}x more than {nxt.factor.lower()}, "
             "the next-largest factor. With 3.6M rows every p-value is ~0, so the effect size "
             "is what tells them apart.")
    chart = alt.Chart(eps).mark_bar(color=BLUE, cornerRadiusEnd=4, size=28).encode(
        x=alt.X("epsilon_squared:Q", title="Epsilon-squared (share of rank variation explained)"),
        y=alt.Y("factor:N", sort="-x", title=None),
        tooltip=[alt.Tooltip("factor:N", title="Factor"),
                 alt.Tooltip("epsilon_squared:Q", title="Epsilon-squared", format=".6f")])
    st.altair_chart(chart.properties(height=180), width="stretch")
    st.info("Use the pages on the left to filter resolution times, compare agencies' "
            "survival curves, and see how well resolution can be predicted at filing time.")


def page_explorer():
    st.title("Resolution time explorer")
    st.caption("Every number is computed exactly for the selected filters (medians can't be "
               "combined, so each combination was pre-computed in SQL).")
    boroughs = ["All"] + sorted(b for b in cube.borough.unique() if b not in ("All", "UNSPECIFIED"))
    seasons = ["All", "Winter", "Spring", "Summer", "Fall"]
    agencies = ["All"] + summary["agency_volume_order"]

    # Widgets show readable labels; the labels are mapped back to the data's values.
    f1, f2, f3 = st.columns(3)
    borough_label = f1.selectbox("Borough", [b.title() for b in boroughs], key="borough")
    borough = {b.title(): b for b in boroughs}[borough_label]
    season = f2.selectbox("Season", seasons, key="season")
    agency = f3.selectbox("Agency", agencies, key="agency")
    g1, g2, g3 = st.columns([1, 1.4, 1])
    group = g1.radio("Compare by", ["Agency", "Borough", "Season"], horizontal=True,
                     key="group").lower()
    metrics = {"median_hours": "Median hours to close", "p90_hours": "90th percentile hours",
               "pct_closed_within_30d": "% closed within 30 days",
               "pct_no_close_date": "% with no close date"}
    metric_label = g2.selectbox("Measure", list(metrics.values()), key="measure")
    metric = {label: key for key, label in metrics.items()}[metric_label]
    is_hours = metric in ("median_hours", "p90_hours")
    log_scale = g3.checkbox("Log scale", value=is_hours, disabled=not is_hours, key="log")

    selection = {"agency": agency, "borough": borough, "season": season}
    rows = cube[cube[group] != "All"]
    for dim, value in selection.items():
        if dim != group:
            rows = rows[rows[dim] == value]
    if group == "borough":
        rows = rows[rows.borough != "UNSPECIFIED"]  # unknown borough isn't a borough
    if selection[group] != "All":
        st.info(f"The {group} filter is set to {selection[group].title() if group == 'borough' else selection[group]}, "
                f"so only that {group} is shown. Set it to All to compare.")
        rows = rows[rows[group] == selection[group]]
    rows = rows.dropna(subset=[metric])
    if rows.empty:
        st.warning("No requests match these filters.")
        return

    rows = rows.assign(label=rows[group].str.title() if group == "borough" else rows[group])
    x = alt.X(f"{metric}:Q", title=metrics[metric],
              scale=alt.Scale(type="log") if (log_scale and is_hours) else alt.Scale(zero=True),
              axis=time_axis(metrics[metric]) if (log_scale and is_hours) else alt.Axis())
    chart = alt.Chart(rows).mark_bar(color=BLUE, cornerRadiusEnd=4).encode(
        x=x, y=alt.Y("label:N", sort="-x", title=None),
        tooltip=[alt.Tooltip("label:N", title=group.title()),
                 alt.Tooltip("n_requests:Q", title="Requests", format=","),
                 alt.Tooltip("median_hours:Q", title="Median hours", format=",.2f"),
                 alt.Tooltip("p90_hours:Q", title="90th pct hours", format=",.2f"),
                 alt.Tooltip("pct_closed_within_30d:Q", title="% within 30 days", format=".2f"),
                 alt.Tooltip("pct_no_close_date:Q", title="% no close date", format=".2f")])
    st.altair_chart(chart.properties(height=max(160, 32 * len(rows))), width="stretch")

    table = rows[["label", "n_requests", "n_with_time", "median_hours", "p90_hours",
                  "pct_closed_within_30d", "pct_no_close_date"]].sort_values(metric, ascending=False)
    st.dataframe(table.rename(columns={
        "label": group.title(), "n_requests": "Requests", "n_with_time": "With close time",
        "median_hours": "Median h", "p90_hours": "P90 h",
        "pct_closed_within_30d": "% within 30 days", "pct_no_close_date": "% no close date"}),
        hide_index=True, width="stretch",
        column_config={c: st.column_config.NumberColumn(format="%.2f")
                       for c in ["Median h", "P90 h", "% within 30 days", "% no close date"]})
    small = table[table.n_with_time < 100]
    if not small.empty:
        st.caption(f"Fewer than 100 closed requests behind: {', '.join(small.label)}. "
                   "Treat those numbers with caution.")
    st.caption("Medians and percentiles use closed requests only; where many requests are "
               "still open (see % no close date) they look faster than reality. The "
               "Kaplan-Meier page corrects for that.")


def page_survival():
    st.title("Kaplan-Meier survival curves")
    st.caption("Share of requests still open over time. Still-open requests are treated as "
               "censored (open at least this long) instead of being dropped.")
    colors, dashes = agency_styles()
    options = ["All agencies"] + summary["agency_volume_order"]
    chosen = st.multiselect("Agencies (up to 8)", options, default=summary["agency_volume_order"][:8],
                            max_selections=8, key="km_agencies")
    show_ci = st.checkbox("Show 95% confidence bands", value=False, key="km_ci")
    if not chosen:
        st.warning("Pick at least one agency.")
        return

    data = km_curves[km_curves.agency.isin(chosen)]
    scale = alt.Scale(domain=chosen, range=[colors[a] for a in chosen])
    dash = alt.Scale(domain=chosen, range=[dashes[a] for a in chosen])
    x = alt.X("hours:Q", scale=alt.Scale(type="log"), axis=time_axis("Time since created (log scale)"))
    lines = alt.Chart(data).mark_line(strokeWidth=2, interpolate="step-after").encode(
        x=x, y=alt.Y("survival:Q", title="Share still open", scale=alt.Scale(domain=[0, 1])),
        color=alt.Color("agency:N", scale=scale, legend=alt.Legend(title="Agency", orient="right")),
        strokeDash=alt.StrokeDash("agency:N", scale=dash, legend=None),
        tooltip=[alt.Tooltip("agency:N", title="Agency"),
                 alt.Tooltip("hours:Q", title="Hours", format=",.2f"),
                 alt.Tooltip("survival:Q", title="Share still open", format=".4f")])
    half = alt.Chart(pd.DataFrame({"y": [0.5]})).mark_rule(color=GRAY).encode(y="y:Q")
    layers = [half, lines]
    if show_ci:
        band = alt.Chart(data).mark_area(opacity=0.12, interpolate="step-after").encode(
            x=x, y="lower:Q", y2="upper:Q", color=alt.Color("agency:N", scale=scale, legend=None))
        layers.insert(0, band)
    st.altair_chart(alt.layer(*layers).properties(height=460), width="stretch")
    st.caption("Gray line = 50% still open: where a curve crosses it is that agency's median. "
               "Curves that start below 100% had requests closed within a minute. "
               "Dashed lines are agencies ranked 9-15 by volume, reusing the same 8 colors.")

    med = km_medians[km_medians.agency.isin(chosen)].copy()
    med["order"] = med.agency.map({a: i for i, a in enumerate(chosen)})
    med = med.sort_values("order").drop(columns="order")
    st.dataframe(med.rename(columns={"agency": "Agency", "n": "Requests", "censored": "Still open",
                                     "km_median": "KM median h", "ci_lower": "95% CI low",
                                     "ci_upper": "95% CI high"}),
                 hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.2f")
                                for c in ["KM median h", "95% CI low", "95% CI high"]})


def page_model():
    st.title("Predicting closure within 7 days")
    counts = model["counts"]
    st.caption(f"Features known at filing only ({', '.join(model['features'])}). Trained on "
               f"{model['split']['train']} ({counts['train_rows']:,} requests), tested on "
               f"{model['split']['test']} ({counts['test_rows']:,}). "
               f"{counts['test_pct_within_7d']:.2f}% of test requests closed within 7 days.")
    res = model["results"]
    rows = [{"Model": n, "ROC AUC": r["roc_auc"],
             "Precision (slow)": r["slow"]["precision"], "Recall (slow)": r["slow"]["recall"],
             "F1 (slow)": r["slow"]["f1"], "F1 (within 7d)": r["within_7d"]["f1"]}
            for n, r in res.items()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.4f")
                                for c in ["ROC AUC", "Precision (slow)", "Recall (slow)",
                                          "F1 (slow)", "F1 (within 7d)"]})
    st.caption("\"Slow\" = not closed within 7 days, the minority class. A model that always "
               "predicts \"within 7 days\" already scores well on that class, so the slow "
               f"class is the real test. Threshold: {model['threshold']}.")

    left, right = st.columns(2)
    roc = pd.concat([pd.DataFrame({"model": n, "fpr": c["fpr"], "tpr": c["tpr"]})
                     for n, c in model["roc"].items()])
    names = list(model["roc"])
    roc_colors = {"Majority-class baseline": GRAY, "Logistic regression": ORANGE,
                  "Gradient boosting": BLUE}
    left.subheader("ROC curves")
    left.altair_chart(alt.Chart(roc).mark_line(strokeWidth=2).encode(
        x=alt.X("fpr:Q", title="False positive rate"),
        y=alt.Y("tpr:Q", title="True positive rate"),
        color=alt.Color("model:N", scale=alt.Scale(domain=names,
                                                   range=[roc_colors.get(n, BLUE) for n in names]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["model:N", alt.Tooltip("fpr:Q", format=".2f"), alt.Tooltip("tpr:Q", format=".4f")]
    ).properties(height=360), width="stretch")

    best = model["best_model"]
    cal = model["calibration"]
    cal_df = pd.DataFrame({"predicted": cal["mean_predicted"], "actual": cal["fraction_positive"]})
    right.subheader(f"Calibration: {best}")
    diagonal = alt.Chart(pd.DataFrame({"v": [0, 1]})).mark_line(color=GRAY).encode(x="v:Q", y="v:Q")
    points = alt.Chart(cal_df).mark_line(point=True, color=BLUE, strokeWidth=2).encode(
        x=alt.X("predicted:Q", title="Predicted probability", scale=alt.Scale(domain=[0, 1])),
        y=alt.Y("actual:Q", title="Actual share within 7 days", scale=alt.Scale(domain=[0, 1])),
        tooltip=[alt.Tooltip("predicted:Q", format=".4f"), alt.Tooltip("actual:Q", format=".4f")])
    right.altair_chart((diagonal + points).properties(height=360), width="stretch")
    right.caption(f"Brier score {cal['brier']:.4f}. Gray = perfectly calibrated.")

    st.subheader("Confusion matrix")
    pick = st.selectbox("Model", names, index=names.index(best), key="cm_model")
    c = res[pick]["confusion"]
    st.dataframe(pd.DataFrame({"": ["Actually slow", "Actually within 7 days"],
                               "Predicted slow": [c["tn"], c["fn"]],
                               "Predicted within 7 days": [c["fp"], c["tp"]]}),
                 hide_index=True)

    st.subheader("Permutation feature importance")
    imp = pd.DataFrame(model["permutation"]["importance"])
    imp["kind"] = imp.feature.map(lambda f: "Shuffled together" if "+" in f else "Single feature")
    st.altair_chart(alt.Chart(imp).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("auc_drop:Q", title="Drop in ROC AUC when shuffled"),
        y=alt.Y("feature:N", sort="-x", title=None),
        color=alt.Color("kind:N", scale=alt.Scale(domain=["Single feature", "Shuffled together"],
                                                  range=[BLUE, GRAY]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["feature:N", alt.Tooltip("auc_drop:Q", format=".4f"),
                 alt.Tooltip("std:Q", title="Std. dev.", format=".4f")]
    ).properties(height=300), width="stretch")
    singles = imp[imp.kind == "Single feature"].sort_values("auc_drop", ascending=False)
    pair = imp[imp.kind == "Shuffled together"].iloc[0]
    st.caption(f"Most important single feature: {singles.iloc[0].feature} "
               f"({singles.iloc[0].auc_drop:.4f}). Agency and complaint type overlap (most "
               f"complaint types belong to one agency), so shuffled together they drop AUC by "
               f"{pair.auc_drop:.4f}. {model['permutation']['rows']:,} test rows, "
               f"{model['permutation']['repeats']} shuffles each.")


@st.cache_data
def load_forecast():
    fsum = json.loads((DATA / "forecast_summary.json").read_text(encoding="utf-8"))
    return (pd.read_parquet(DATA / "forecast_daily.parquet"), pd.read_parquet(DATA / "forecast_backtest.parquet"),
            fsum)


SHORT = {"naive": "Naive", "seasonal_naive": "Seasonal naive", "weekday_avg_4wk": "4-week same-weekday avg",
         "holt_winters": "Holt-Winters", "gradient_boosting": "Gradient boosting"}


def page_forecast():
    fdaily, fbt, fs = load_forecast()
    best = fs["best_model"]
    m = {(r["variant"], r["split"], r["model"]): r for r in fs["total_metrics"]}
    b_test, b_val = m[("as_recorded", "test", best)], m[("as_recorded", "validation", best)]
    test_best = min((r for r in fs["total_metrics"] if r["variant"] == "as_recorded" and r["split"] == "test"),
                    key=lambda r: r["mae"])
    cov = fs["total_coverage"]["as_recorded/test"]

    st.title("Demand forecast: daily requests, 14 days ahead")
    st.caption(f"Rolling-origin backtest: at each weekly cutoff a model sees only the days up to the cutoff and "
               f"forecasts the next {fs['horizon']}. Models were chosen on {len(fs['validation_cutoffs'])} validation "
               f"cutoffs ({fs['validation_cutoffs'][0]} to {fs['validation_cutoffs'][-1]}) and are scored here on "
               f"{len(fs['test_cutoffs'])} test cutoffs ({fs['test_cutoffs'][0]} to {fs['test_cutoffs'][-1]}), "
               f"which cover the last 10 weeks of 2025.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Model chosen on validation", SHORT[best],
              help=f"Lowest validation MAE for the citywide total ({b_val['mae']:,.0f} requests/day).")
    c2.metric("Its test MAE", f"{b_test['mae']:,.0f} / day", f"{b_test['mae_improvement_pct']:+.1f}% vs seasonal naive",
              help="Average miss per day over all test forecasts. Positive % = lower error than "
                   "'same weekday last week'.")
    c3.metric("Best test MAE", f"{test_best['mae']:,.0f} / day", SHORT[test_best["model"]], delta_color="off",
              help="Reported for honesty only: choosing a model on test results would leak the test set.")
    c4.metric("80% interval coverage (test)", f"{cov['coverage_pct']:.1f}%",
              help="Share of actual days inside the 80% interval. Target 80%; the intervals were "
                   "calibrated on calmer months.")

    # --- Actual vs forecast chart ---
    series_options = ["Total"] + fs["agency_order"]
    f1, f2, f3 = st.columns([1, 1.2, 1])
    series = f1.selectbox("Series", series_options, key="fc_series",
                          format_func=lambda s: "All agencies (Total)" if s == "Total" else s)
    horizon = f2.radio("Forecast made", ["1-7 days ahead", "8-14 days ahead"], horizontal=True, key="fc_horizon")
    show_band = f3.checkbox("Show 80% interval", value=True, key="fc_band")
    lo_h, hi_h = (1, 7) if horizon.startswith("1-") else (8, 14)

    rows = fbt[(fbt.series == series) & fbt.horizon.between(lo_h, hi_h)].sort_values("day")
    model_name = SHORT[rows.model.iloc[0]]
    first_day = rows.day.min()
    context = fdaily[(fdaily.series == series) & (fdaily.day >= first_day - pd.Timedelta(weeks=4))]
    wide = context[["day", "actual"]].merge(rows[["day", "forecast", "lo", "hi", "cutoff"]], on="day", how="left")
    long = wide.melt(id_vars="day", value_vars=["actual", "forecast"], var_name="line", value_name="requests")
    long["line"] = long.line.map({"actual": "Actual", "forecast": f"Forecast ({model_name})"})
    domain = ["Actual", f"Forecast ({model_name})"]

    base = alt.Chart(wide).encode(x=alt.X("day:T", title=None))
    layers = []
    if show_band:
        layers.append(base.mark_area(opacity=0.18, color=BLUE).encode(
            y=alt.Y("lo:Q", title="Requests per day"), y2="hi:Q"))
    layers.append(alt.Chart(long).mark_line(strokeWidth=2).encode(
        x="day:T", y=alt.Y("requests:Q", title="Requests per day"),
        color=alt.Color("line:N", scale=alt.Scale(domain=domain, range=[INK, BLUE]),
                        legend=alt.Legend(orient="bottom", title=None))))
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["day"], empty=False)
    layers.append(base.mark_rule(color=GRAY).encode(
        opacity=alt.condition(nearest, alt.value(0.8), alt.value(0)),
        tooltip=[alt.Tooltip("day:T", title="Day", format="%a %b %d"),
                 alt.Tooltip("actual:Q", title="Actual", format=",.0f"),
                 alt.Tooltip("forecast:Q", title="Forecast", format=",.0f"),
                 alt.Tooltip("lo:Q", title="80% low", format=",.0f"),
                 alt.Tooltip("hi:Q", title="80% high", format=",.0f"),
                 alt.Tooltip("cutoff:T", title="Made on cutoff", format="%b %d")]).add_params(nearest))
    st.altair_chart(alt.layer(*layers).properties(height=380), width="stretch")
    inside = ((rows.actual >= rows.lo) & (rows.actual <= rows.hi)).mean() * 100
    mape = ((rows.actual - rows.forecast).abs() / rows.actual).mean() * 100
    st.caption(f"{model_name} (chosen for this series on validation), forecasts made {horizon}. Each 7-day stretch "
               f"comes from a different weekly cutoff; the 4 weeks before the test period are shown as context. "
               f"In this view: MAPE {mape:.1f}%, {inside:.0f}% of actual days inside the 80% interval.")

    # --- Model comparison ---
    st.subheader("Model comparison: citywide total, same backtest for every model")
    variant = st.radio("Data", ["As recorded", "Excluding single-location bursts"], horizontal=True, key="fc_variant",
                       help=f"One Bronx location filed bursts of noise complaints ({fs['hotspot_pct_total']:.1f}% of "
                            f"2025 requests). Excluding them shows how much error they cause.")
    v = "as_recorded" if variant == "As recorded" else "excl_hotspots"
    comp = pd.DataFrame([{
        "Model": fs["labels"][k] + (" (chosen)" if k == best else ""),
        "Validation MAE": m[(v, "validation", k)]["mae"], "Test MAE": m[(v, "test", k)]["mae"],
        "Test RMSE": m[(v, "test", k)]["rmse"], "Test MAPE %": m[(v, "test", k)]["mape"],
        "Test MAE vs seasonal naive %": m[(v, "test", k)]["mae_improvement_pct"]} for k in fs["order"]])
    st.dataframe(comp, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.0f") for c in ["Validation MAE", "Test MAE", "Test RMSE"]} | {
        "Test MAPE %": st.column_config.NumberColumn(format="%.1f"),
        "Test MAE vs seasonal naive %": st.column_config.NumberColumn(format="%+.1f")})
    best_v = min(fs["order"], key=lambda k: m[(v, "test", k)]["mae"])
    st.caption(f"The model was chosen on validation (mid-Aug to mid-Oct, a calm period). On the volatile test weeks "
               f"(bursts, Thanksgiving, the December snow, Christmas) the lowest test MAE here is "
               f"{SHORT[best_v]}; nothing was re-tuned after seeing the test results.")

    # --- Per agency ---
    st.subheader("Per-agency forecasts, easiest to hardest")
    am = {(r["variant"], r["split"], r["series"], r["model"]): r for r in fs["agency_metrics"]}
    fx = {f["series"]: f for f in fs["agency_forecastability"]}
    agency_rows = []
    for a in fs["agency_order"]:
        k = fs["agency_chosen"][f"as_recorded/{a}"]
        t = am[("as_recorded", "test", a, k)]
        p = fs["agency_per"][f"as_recorded/{a}"]
        agency_rows.append({
            "Agency": a, "Chosen on validation": SHORT[k], "Test MAPE %": t["mape"], "Test MAE": t["mae"],
            "MAE vs seasonal naive %": t["mae_improvement_pct"],
            "80% interval coverage %": p["coverage"]["test"]["coverage_pct"],
            "Outlier days in test period": fx[a]["outlier_days_in_test_period"],
            "Error on event days %": p["error_sources_chosen"]["event day"]["share_of_error_pct"],
            "Level change Oct-Dec (pts)": fx[a]["oct_to_dec_level_change_pct"]})
    pct_cols = ["Test MAPE %", "80% interval coverage %", "Error on event days %"]
    st.dataframe(pd.DataFrame(agency_rows), hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.1f") for c in pct_cols} | {
        "Test MAE": st.column_config.NumberColumn(format="%.0f"),
        "MAE vs seasonal naive %": st.column_config.NumberColumn(format="%+.1f"),
        "Level change Oct-Dec (pts)": st.column_config.NumberColumn(format="%+.0f")})
    st.caption("DOT follows the working week and is easiest. DSNY is hardest: the December snow (Snow or Ice "
               "requests) caused most of its error. HPD's heating season lifts its level through the test period, and "
               "NYPD's error is mostly the single-location noise bursts.")

    # --- Staffing ---
    st.subheader("Staffing: roster to the forecast, or to the top of the 80% interval (P90)?")
    staff = []
    for a in fs["agency_order"]:
        s = fs["agency_per"][f"as_recorded/{a}"]["staffing"]
        staff.append({"Agency": a, "P90 buffer above forecast %": s["buffer_pct"],
                      "Short days, forecast plan %": s["forecast"]["short_days_pct"],
                      "Short days, P90 plan %": s["p90"]["short_days_pct"],
                      "Avg spare capacity, P90 plan (requests/day)": s["p90"]["avg_unused"]})
    staff = pd.DataFrame(staff)
    chart_df = staff.melt(id_vars="Agency", value_vars=["Short days, forecast plan %", "Short days, P90 plan %"],
                          var_name="Plan", value_name="pct")
    plans = ["Short days, forecast plan %", "Short days, P90 plan %"]
    bars = alt.Chart(chart_df).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("pct:Q", title="% of test days on which demand exceeded the plan"),
        y=alt.Y("Agency:N", sort=fs["agency_order"], title=None),
        yOffset=alt.YOffset("Plan:N", sort=plans, scale=alt.Scale(paddingInner=0.12)),
        color=alt.Color("Plan:N", scale=alt.Scale(domain=plans, range=[ORANGE, BLUE]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["Agency:N", "Plan:N", alt.Tooltip("pct:Q", title="% of days short", format=".1f")])
    target = alt.Chart(pd.DataFrame({"x": [10]})).mark_rule(color=INK, strokeDash=[4, 3]).encode(x="x:Q")
    st.altair_chart((bars + target).properties(height=300), width="stretch")
    st.dataframe(staff, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.1f") for c in staff.columns if c != "Agency"} | {
        "Avg spare capacity, P90 plan (requests/day)": st.column_config.NumberColumn(format="%.0f")})
    st.caption("Rostering to P90 is the staffing version of safety stock: it should leave a shift short on about 10% "
               "of days (dashed line). It cut short days sharply but stayed above 10%, because its buffer was sized "
               "on calm months. Requests, not staff: converting needs each agency's requests handled per shift.")


PAGES = {"Overview": page_overview, "Resolution time explorer": page_explorer,
         "Survival curves": page_survival, "Prediction model": page_model, "Forecast": page_forecast}
choice = st.sidebar.radio("Page", list(PAGES), key="page")
st.sidebar.caption("Built from pre-aggregated files: no database, no raw rows. "
                   "Code and full analysis: see the project README.")
PAGES[choice]()
