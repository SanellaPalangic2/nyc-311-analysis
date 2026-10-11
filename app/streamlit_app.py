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


PAGES = {"Overview": page_overview, "Resolution time explorer": page_explorer,
         "Survival curves": page_survival, "Prediction model": page_model}
choice = st.sidebar.radio("Page", list(PAGES), key="page")
st.sidebar.caption("Built from pre-aggregated files: no database, no raw rows. "
                   "Code and full analysis: see the project README.")
PAGES[choice]()
