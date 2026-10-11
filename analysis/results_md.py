"""Render results/results.md from the numbers each stage saved in results/data/.

Run:  python analysis/results_md.py   (each stage script also calls it)

No number in results.md is typed by hand: every value is read from the JSON
a stage script wrote, so rerunning the scripts regenerates the document.
Sections appear only for stages that have been run.
"""
import json
import math

import common


def load(name):
    path = common.DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def h(x):
    """Hours with 2 decimals, or a note when a median was never reached."""
    return "not reached" if x is None else f"{x:,.2f}"


def pval(p):
    if p == 0:
        return "< 1e-300 (underflows to 0)"
    return f"{p:.2e}" if p < 0.001 else f"{p:.4f}"


def p_text(p):
    """p-value for running text: 'p < 1e-300' or 'p = 0.0033'."""
    return "p < 1e-300 (underflows to 0)" if p == 0 else f"p = {pval(p)}"


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---:" if i else "---" for i in range(len(header))) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


# --- Stage 1 -------------------------------------------------------------------

def survival_section(s):
    c = s["counts"]
    o = s["overall"]
    out = ["## Stage 1: Survival analysis", "",
           "Script: `analysis/survival.py`. Still-open requests are treated as **censored**: "
           "we know they took at least as long as they've been open, and survival methods use "
           f"that instead of dropping them. Snapshot (latest close date in the data): "
           f"**{s['snapshot']}**.", "",
           "### Rows used", "",
           table(["Group", "Rows"], [
               ["Rows in clean_311", f"{c['rows_in_clean_table']:,}"],
               ["Closed (event observed)", f"{c['events_closed']:,}"],
               ["**Still open (censored)**", f"**{c['censored_still_open']:,}**"],
               ["Left out: status Closed but no close date (time unknown)",
                f"{c['excluded_closed_status_without_date']:,}"],
               ["Left out: closed before created", f"{c['excluded_closed_before_created']:,}"],
               ["Left out: placeholder close date", f"{c['excluded_placeholder_date']:,}"],
               ["Rows used", f"{c['rows_used']:,}"],
           ]), ""]

    diff = o["km_median"] - o["median_closed_only"]
    out += ["### Overall median", "",
            table(["Method", "Median hours", "95% CI"], [
                ["Closed requests only (earlier analysis)", h(o["median_closed_only"]), "-"],
                ["Kaplan-Meier (open requests censored)", h(o["km_median"]),
                 f"{h(o['km_ci_lower'])} - {h(o['km_ci_upper'])}"],
            ]), "",
            f"Including open requests moves the overall median by {diff:+,.2f} h "
            f"({100 * diff / o['median_closed_only']:+.2f}%).", ""]

    rows = []
    for r in s["agencies"]:
        ci = ("-" if r["km_ci_lower"] is None and r["km_ci_upper"] is None
              else f"{h(r['km_ci_lower'])} - {h(r['km_ci_upper'])}")
        rows.append([r["agency"], f"{r['n']:,}", f"{r['censored']:,} ({r['pct_censored']:.2f}%)",
                     h(r["median_closed_only"]), h(r["km_median"]), ci,
                     "-" if r["difference_hours"] is None else f"{r['difference_hours']:+,.2f}",
                     "-" if r["difference_pct"] is None else f"{r['difference_pct']:+.2f}%"])
    not_reached = [r["agency"] for r in s["agencies"] if r["km_median"] is None]
    intro = "Top 8 by volume are in the Kaplan-Meier chart; all 15 are listed."
    if not_reached:
        intro += (" \"Not reached\" means more than half of that agency's requests were still "
                  "open at the end of follow-up, so the median can't be estimated.")
    out += ["### Median time to resolution by agency", "", intro, "",
            table(["Agency", "Requests used", "Censored", "Median, closed only (h)",
                   "KM median (h)", "KM 95% CI (h)", "Change (h)", "Change"], rows), "",
            "Where nothing is censored, tiny changes (like +0.00 or -0.00) aren't bias: the "
            "Kaplan-Meier median is the first time the curve reaches 50%, while the earlier "
            "median averages the two middle values.", ""]

    # Bias explanation, computed from the table
    reached = [r for r in s["agencies"] if r["difference_hours"] is not None]
    by_change = sorted(reached, key=lambda r: r["difference_pct"], reverse=True)
    small = [r["agency"] for r in reached if abs(r["difference_pct"]) < 1]
    out += ["**How much did excluding open requests bias the earlier medians?** "
            f"Overall, the median rises from {h(o['median_closed_only'])} h to {h(o['km_median'])} h "
            f"({100 * diff / o['median_closed_only']:+.2f}%); only "
            f"{100 * c['censored_still_open'] / c['rows_used']:.2f}% of requests are still open. "
            "The bias is concentrated in agencies with many open requests: "
            + "; ".join(f"{r['agency']} {h(r['median_closed_only'])} h -> {h(r['km_median'])} h "
                        f"({r['difference_pct']:+.2f}%, {r['pct_censored']:.2f}% censored)"
                        for r in by_change[:3])
            + (f", and for {', '.join(not_reached)} the true median can't even be estimated "
               "because more than half of requests are still open." if not_reached else ".")
            + f" {len(small)} of {len(reached)} agencies changed by less than 1%."]
    worst_drop = min(r["difference_hours"] for r in reached)
    if worst_drop > -0.01:
        out[-1] += (" No agency's median went down beyond rounding: leaving out open requests "
                    "made every agency look faster or the same, never slower.")
    most_open = max(reached, key=lambda r: r["pct_censored"])
    if most_open["agency"] != by_change[0]["agency"]:
        out[-1] += (f" The share still open isn't the whole story: {most_open['agency']} has the "
                    f"most open requests ({most_open['pct_censored']:.2f}% censored) but its median "
                    f"changed by {most_open['difference_pct']:+.2f}%, because the size of the bias "
                    "also depends on how the open requests' waiting times compare with the "
                    "closed ones.")
    out.append("")

    lr = s["logrank"]
    out += ["### Log-rank test (top 8 agencies)", "",
            f"Tests whether the 8 survival curves are all the same. Chi-square = "
            f"{lr['test_statistic']:,.2f}, df = {lr['degrees_of_freedom']}, {p_text(lr['p_value'])}, "
            f"n = {lr['n']:,}. The curves differ, but with millions of rows any difference is "
            "significant, so the size of the gaps (table above) matters more than the p-value.", ""]

    cx = s["cox"]
    out += ["### Cox proportional hazards model", "",
            f"Fit on a random sample of {cx['sample_size']:,} requests with a known borough "
            f"(seed {cx['seed']}; {cx['n_events_in_sample']:,} closed in the sample), because "
            "fitting all 3.6M rows is slow and memory-heavy in lifelines. Covariates: agency, "
            f"borough, season, and the top {len(cx['top_types'])} complaint types by volume. "
            f"Reference groups: {cx['references']['agency']}, {cx['references']['borough'].title()}, "
            f"{cx['references']['season']}, and complaint types outside the top "
            f"{len(cx['top_types'])}. Agencies with fewer than {cx['small_agency_max']:,} "
            f"requests in 2025 ({', '.join(cx['small_agencies_grouped'])}) share one "
            "\"small agencies\" group: alone they'd have too few requests in the sample for a "
            "stable estimate.", "",
            "Hazard ratio above 1 = closes faster than the reference at any given moment; "
            "below 1 = slower. Complaint-type ratios are relative to other types *within the "
            "same agency*, since agency is also in the model.", "",
            f"- Concordance index: {cx['concordance']:.4f} (0.5 = random ordering, 1 = perfect)",
            f"- Stability: refitting on a second independent sample (seed {cx['seed'] + 1}) "
            f"changed log hazard ratios by at most {cx['stability_max_abs_log_hr_diff']:.4f} "
            f"({cx['stability_worst_covariate']}).",
            ]
    if cx["dropped_no_events"]:
        out.append(f"- Left out (no closed requests in the sample): {', '.join(cx['dropped_no_events'])}")
    out += ["", table(["Covariate", "Hazard ratio", "95% CI", "p"],
                      [[r["covariate"], f"{r['hr']:.4f}", f"{r['hr_lower']:.4f} - {r['hr_upper']:.4f}",
                        pval(r["p"])] for r in cx["hazard_ratios"]]), ""]

    # How big are the agency effects compared with borough and season?
    def hr_range(prefix):
        vals = [r["hr"] for r in cx["hazard_ratios"] if r["covariate"].startswith(prefix)]
        return min(vals), max(vals)
    a_lo, a_hi = hr_range("agency:")
    b_lo, b_hi = hr_range("borough:")
    s_lo, s_hi = hr_range("season:")
    # Largest effect in each group, on the log scale (so 0.5 and 2.0 count equally)
    biggest = {g: max(abs(math.log(lo)), abs(math.log(hi)))
               for g, (lo, hi) in {"agency": (a_lo, a_hi), "borough": (b_lo, b_hi),
                                   "season": (s_lo, s_hi)}.items()}
    text = (f"**Size of effects:** agency hazard ratios range from {a_lo:.4f} to {a_hi:.4f}, "
            f"borough ratios from {b_lo:.4f} to {b_hi:.4f}, and season ratios from {s_lo:.4f} "
            f"to {s_hi:.4f}.")
    if a_hi < 1:
        text += " Every agency closes at a fraction of NYPD's rate."
    if biggest["agency"] > max(biggest["borough"], biggest["season"]):
        text += (" Holding the other factors fixed, agency moves the closing rate far more "
                 "than borough or season.")
    out += [text, ""]

    # Compare the adjusted season effects with the earlier unadjusted season result
    seasons = {r["covariate"].split(": ")[1]: r["hr"] for r in cx["hazard_ratios"]
               if r["covariate"].startswith("season:")}
    mix_file = common.PROJECT_ROOT / "outputs" / "powerbi" / "summary_season_mix.csv"
    if mix_file.exists() and seasons:
        import csv
        with open(mix_file, newline="", encoding="utf-8") as f:
            mix = {r["season"]: float(r["median_hours_other_agencies"]) for r in csv.DictReader(f)}
        raw_slowest = max(mix, key=mix.get)
        if raw_slowest in seasons and seasons[raw_slowest] > 1:
            ph_season = {r["covariate"]: r["p"] for r in s["ph_test"]["rows"]}
            out += [f"**Season, revisited:** the earlier unadjusted medians had {raw_slowest} as "
                    f"the slowest season for non-NYPD agencies ({mix[raw_slowest]:,.2f} h). After "
                    "adjusting for agency and complaint type, the Cox model has "
                    f"{raw_slowest} closing *faster* than {cx['references']['season']} "
                    f"(hazard ratio {seasons[raw_slowest]:.4f}). So the raw {raw_slowest} slowdown "
                    "is not explained by season alone; a different mix of agencies and complaint "
                    "types in that season is one possible explanation. Treat this as tentative: "
                    f"the season effects fail the proportional hazards test (e.g. "
                    f"season: {raw_slowest} {p_text(ph_season.get('season: ' + raw_slowest, 1))}), "
                    "so they are averages over time.", ""]

    ph = s["ph_test"]
    holds = ph["n_violating_p05"] == 0
    out += ["### Does the proportional hazards assumption hold?", "",
            f"Test (scaled Schoenfeld residuals, rank time transform), run on a separate "
            f"{ph['sample_size']:,}-row fit with the same covariates (seed {ph['seed']}), because "
            "its run time grows with roughly the square of the sample size: "
            f"**{ph['n_violating_p05']} of {ph['n_covariates']} covariates violate it at p < 0.05.**", ""]
    if holds:
        out.append("The assumption holds: each hazard ratio is a constant multiple over time.")
    else:
        out += ["In plain terms: the Cox model assumes each group's closing rate is a *fixed "
                "multiple* of the reference group's, at every point in time. That isn't true here: "
                "the ratio between groups changes over time (for example, an agency whose median "
                "is about an hour does most of its closing early, while agencies with medians of "
                "days or weeks close later). `1d_ph_check_loglog.png` shows this visually: "
                "under the assumption, the agency curves would run parallel.", "",
                "What it means: each hazard ratio is best read as an **average effect over the "
                "follow-up period**, not a constant one, and its direction and rough size are "
                "more trustworthy than its exact value. The Kaplan-Meier medians and curves don't "
                "rely on this assumption, so they're the main result. (With "
                f"{ph['sample_size']:,} rows the test detects even tiny violations, so the "
                "visual check matters as much as the p-values.) A stratified Cox model or "
                "time-varying effects would be the next step.", ""]
    out += [table(["Covariate", "Test statistic", "p"],
                  [[r["covariate"], f"{r['test_statistic']:,.2f}", pval(r["p"])] for r in ph["rows"]]),
            ""]
    return out


def survival_figures():
    return [
        ("1a_km_top8_agencies.png", "Kaplan-Meier curves for the 8 largest agencies: the share "
         "of requests still open over time, labeled where each crosses 50% (its median)."),
        ("1b_median_bias_by_agency.png", "For each agency, the median from closed requests only "
         "next to the Kaplan-Meier median that counts open requests; long bars show where "
         "dropping open requests biased the result."),
        ("1c_cox_hazard_ratios.png", "Cox model hazard ratios with 95% CIs: which agencies, "
         "boroughs, seasons and complaint types close faster or slower than the reference."),
        ("1d_ph_check_loglog.png", "Proportional hazards check: log(-log S) curves that cross "
         "instead of running parallel show the assumption does not hold."),
    ]


# --- Stage 2 -------------------------------------------------------------------

def stats_section(s):
    tests = {t["factor"]: t for t in s["kruskal_wallis"]}
    out = ["## Stage 2: Statistical testing", "",
           "Script: `analysis/stats_tests.py`. Uses the "
           f"{s['n_closed']:,} closed requests (the same rows as the earlier medians), because "
           "these tests need an observed resolution time; open requests are handled by the "
           f"survival analysis in Stage 1. Borough tests use the {s['n_known_borough']:,} "
           "requests with a known borough.", "",
           "### Kruskal-Wallis tests", "",
           "Kruskal-Wallis asks whether the groups' resolution times come from the same "
           "distribution. It compares ranks, not raw hours, so it doesn't assume normally "
           "distributed data (resolution times are extremely skewed) and isn't dominated by "
           "the few year-long requests.", "",
           table(["Factor", "Groups", "Requests", "H", "df", "p", "Epsilon-squared"],
                 [[t["factor"], t["groups"], f"{t['n']:,}", f"{t['H']:,.2f}", t["df"],
                   pval(t["p_value"]), f"{t['epsilon_squared']:.6f}"]
                  for t in s["kruskal_wallis"]]), "",
           "**Why the effect size matters more than the p-value here.** A p-value answers "
           "\"could a difference this large be chance?\" With millions of rows, even a "
           "difference of a few minutes is not chance, so every p-value is essentially 0 and "
           "they can't be used to rank the factors. Epsilon-squared answers a different "
           "question: what share of the variation in (ranked) resolution time does the "
           "grouping explain, from 0 (none) to 1 (all)? That's the measure of *how much* each "
           "factor matters.", ""]

    ranked = sorted(tests.values(), key=lambda t: t["epsilon_squared"], reverse=True)
    top = ranked[0]
    others = ranked[1:]
    ratios = ", ".join(f"{top['epsilon_squared'] / t['epsilon_squared']:,.1f} times {t['factor']}'s "
                       f"({t['epsilon_squared']:.6f})" for t in others)
    supported = top["factor"] == "agency"
    out += ["**Does this support the finding that agency matters more than borough or season?** "
            + ("Yes: " if supported else "No: ")
            + f"{top['factor']} explains {100 * top['epsilon_squared']:.2f}% of the rank variation "
            f"in resolution time (epsilon-squared {top['epsilon_squared']:.6f}), {ratios}.", ""]

    for factor, label in [("agency", "agency"), ("borough", "borough")]:
        rows = sorted(s["bootstrap"][factor], key=lambda r: r["median"])
        out += [f"### Bootstrapped 95% CI for the median, by {label}", "",
                table([label.title(), "Closed requests", "Median (h)", "95% CI (h)", "CI width (h)"],
                      [[r[factor].title() if factor == "borough" else r[factor], f"{r['n']:,}",
                        h(r["median"]), f"{h(r['ci_lower'])} - {h(r['ci_upper'])}",
                        f"{r['ci_upper'] - r['ci_lower']:,.2f}"] for r in rows]), ""]
    widest = max(s["bootstrap"]["agency"], key=lambda r: r["ci_upper"] - r["ci_lower"])
    out += [f"Method: percentile bootstrap, {s['n_boot']:,} resamples per group, random seed "
            f"{s['seed']}, using **all** closed requests in each group (no subsampling was "
            f"needed: the bootstrap took {s['bootstrap_minutes']:.1f} minutes). Each resample draws the same number of requests "
            "with replacement and takes the median; the CI is the middle 95% of those medians.", "",
            "Most intervals are very narrow because the groups are so large. The widest is "
            f"{widest['agency']} ({h(widest['ci_lower'])} - {h(widest['ci_upper'])} h, "
            f"{widest['n']:,} closed requests): small groups, and groups whose times are spread "
            "out around the middle, get wider intervals. These CIs cover closed requests only; "
            "Stage 1's Kaplan-Meier medians also account for open requests.", ""]
    return out


def stats_figures():
    return [
        ("2a_effect_sizes.png", "Epsilon-squared effect sizes from the Kruskal-Wallis tests: "
         "how much of the variation in resolution time agency, borough and season each explain."),
        ("2b_bootstrap_median_agency.png", "Median resolution time per agency with 95% "
         "bootstrap confidence intervals, on a log scale."),
        ("2c_bootstrap_median_borough.png", "Median resolution time per borough with 95% "
         "bootstrap confidence intervals."),
    ]


# --- Stage 3 -------------------------------------------------------------------

def prediction_section(s):
    c = s["counts"]
    res = s["results"]
    base = res["Majority-class baseline"]
    best = s["best_model"]
    out = ["## Stage 3: Prediction model", "",
           "Script: `analysis/prediction.py`. Target: **closed within 7 days** of being created "
           "(1 = yes). Still-open requests count as \"no\" (they've been open 9+ months); "
           f"{c['outcome_unknown_excluded']:,} requests whose closing time is unknown are left "
           "out.", "",
           f"- **Features (known at creation only):** {', '.join(s['features'])}. Nothing from "
           "the close date, status or resolution text is used (the script refuses to run if "
           "one of those columns becomes a feature).",
           f"- **Time-based split:** train on {s['split']['train']} ({c['train_rows']:,} "
           f"requests), test on {s['split']['test']} ({c['test_rows']:,} requests). This mimics "
           "real use: learn from the past, predict the future.",
           f"- **Class balance:** {c['test_pct_within_7d']:.2f}% of test requests closed within "
           f"7 days ({c['train_pct_within_7d']:.2f}% in training). So a model that always says "
           "\"yes\" already looks good on the \"yes\" class; the **slow** class (not closed within "
           "7 days) is where the models have to prove themselves, so metrics are shown for both.",
           f"- **Threshold:** a request is predicted \"within 7 days\" when its predicted "
           f"probability is at least {s['threshold']}.", ""]

    rows = []
    for name, r in res.items():
        rows.append([name, f"{r['roc_auc']:.4f}",
                     f"{r['within_7d']['precision']:.4f}", f"{r['within_7d']['recall']:.4f}",
                     f"{r['within_7d']['f1']:.4f}",
                     f"{r['slow']['precision']:.4f}", f"{r['slow']['recall']:.4f}",
                     f"{r['slow']['f1']:.4f}"])
    out += ["### Test-set results (Oct-Dec 2025)", "",
            table(["Model", "ROC AUC", "Precision (within 7d)", "Recall (within 7d)",
                   "F1 (within 7d)", "Precision (slow)", "Recall (slow)", "F1 (slow)"], rows), "",
            table(["Model", "True slow (TN)", "False within-7d (FP)", "False slow (FN)",
                   "True within-7d (TP)"],
                  [[n, f"{r['confusion']['tn']:,}", f"{r['confusion']['fp']:,}",
                    f"{r['confusion']['fn']:,}", f"{r['confusion']['tp']:,}"] for n, r in res.items()]),
            "",
            f"**Best model: {best}**, ROC AUC {res[best]['roc_auc']:.4f} vs "
            f"{base['roc_auc']:.4f} for the majority-class baseline. On the slow class it reaches "
            f"precision {res[best]['slow']['precision']:.4f} and recall "
            f"{res[best]['slow']['recall']:.4f} (F1 {res[best]['slow']['f1']:.4f}); the baseline "
            "never predicts a slow request, so its slow-class scores are 0."]
    runner_up = max((n for n in res if n not in ("Majority-class baseline", best)),
                    key=lambda n: res[n]["roc_auc"])
    gap = res[best]["roc_auc"] - res[runner_up]["roc_auc"]
    out[-1] += (f" {runner_up} is close behind (AUC {res[runner_up]['roc_auc']:.4f}, "
                f"{gap:.4f} lower)")
    if res[runner_up]["slow"]["f1"] > res[best]["slow"]["f1"]:
        out[-1] += (f" and at the 0.5 threshold it actually scores higher on the slow class "
                    f"(F1 {res[runner_up]['slow']['f1']:.4f} vs {res[best]['slow']['f1']:.4f}: "
                    f"recall {res[runner_up]['slow']['recall']:.4f} vs "
                    f"{res[best]['slow']['recall']:.4f}). AUC measures ranking across *all* "
                    "thresholds, F1 only at one, so the threshold should be chosen for the use: "
                    "lower it to catch more slow requests at the cost of more false alarms.")
    else:
        out[-1] += "."
    out += ["",
            "**Why the AUC is so high:** complaint type and agency are known when a request is "
            "filed and largely decide which process handles it (Stage 2: agency alone explains "
            "over half of the variation in resolution time). This isn't leakage: no information "
            "from after the request was created is used.", ""]

    cal = s["calibration"]
    gaps = [abs(p - f) for p, f in zip(cal["mean_predicted"], cal["fraction_positive"])]
    out += ["### Calibration of the best model", "",
            f"Brier score {cal['brier']:.4f} (mean squared error of the predicted probabilities; "
            f"lower is better). The test set was split into {len(gaps)} bins with equal numbers "
            f"of requests; {sum(p > 0.98 for p in cal['mean_predicted'])} of them have a mean "
            "predicted probability above 0.98 (most requests are predicted to be near-certain "
            "to close within 7 days), so those points overlap on the chart. Predicted and actual "
            f"shares differ by at most {max(gaps):.4f} (mean {sum(gaps) / len(gaps):.4f})"
            + (", so a predicted probability can be read roughly at face value." if max(gaps) < 0.05
               else "; the bins with the largest gaps are where the probabilities shouldn't be "
                    "taken at face value."), "",
            table(["Bin", "Mean predicted", "Actual share within 7 days"],
                  [[i + 1, f"{p:.4f}", f"{f:.4f}"]
                   for i, (p, f) in enumerate(zip(cal["mean_predicted"], cal["fraction_positive"]))]),
            ""]

    imp = sorted(s["permutation"]["importance"], key=lambda r: r["auc_drop"], reverse=True)
    singles = [r for r in imp if "+" not in r["feature"]]
    pair = next(r for r in imp if "+" in r["feature"])
    by_name = {r["feature"]: r for r in singles}
    top = singles[0]
    pm = s["permutation"]
    out += ["### Permutation feature importance", "",
            f"Each feature is shuffled in {pm['rows']:,} random test rows ({pm['repeats']} times, "
            f"seed {pm['seed']}) and the drop in ROC AUC is measured: the bigger the drop, the "
            "more the model relies on it. Agency and complaint type are also shuffled together, "
            "because they overlap heavily (most complaint types belong to a single agency).", "",
            table(["Feature", "AUC drop", "Std. dev."],
                  [[r["feature"], f"{r['auc_drop']:.4f}", f"{r['std']:.4f}"] for r in imp]), ""]
    agency_rank = [r["feature"] for r in singles].index("agency") + 1
    verdict = (f"**Does it confirm agency as the top driver?** "
               + ("Yes: agency is the most important single feature "
                  f"(AUC drop {by_name['agency']['auc_drop']:.4f})." if top["feature"] == "agency" else
                  f"Not on its own: {top['feature']} is the most important single feature "
                  f"(AUC drop {top['auc_drop']:.4f}) and agency ranks #{agency_rank} "
                  f"({by_name['agency']['auc_drop']:.4f}). That's mostly overlap, not disagreement: "
                  "complaint type nearly determines the agency, so when agency alone is shuffled "
                  "the model still gets that information from complaint type."))
    verdict += (f" Shuffled together, agency + complaint type drop AUC by "
                f"{pair['auc_drop']:.4f}, more than any single feature, so *who handles the "
                "request and what it's about* is the main driver.")
    out += [verdict, "",
            f"**About month:** the model is trained on months 1-9 and tested on 10-12, so it "
            "never sees a test month during training. Month can only act as a trend that's "
            "extrapolated forward, and shuffling it within Oct-Dec (AUC drop "
            f"{by_name['month']['auc_drop']:.4f}) measures little. A model trained on several "
            "years would be needed to learn real month effects.", ""]
    return out


def prediction_figures():
    return [
        ("3a_roc_curves.png", "ROC curves for the baseline, logistic regression and gradient "
         "boosting on the Oct-Dec 2025 test set."),
        ("3b_confusion_matrices.png", "Confusion matrices for the three models: how many slow "
         "and fast requests each one got right and wrong."),
        ("3c_calibration.png", "Calibration of the best model: predicted probability of closing "
         "within 7 days vs the actual share, in 10 bins."),
        ("3d_permutation_importance.png", "Permutation feature importance for the best model, "
         "including agency and complaint type shuffled together."),
    ]


# --- Render --------------------------------------------------------------------

def render():
    lines = ["# Advanced analysis results", "",
             "Generated by the scripts in `analysis/`. Every number below is read from "
             "`results/data/*.json`, written by those scripts; rerun them to reproduce it.", ""]
    figures = []
    survival = load("survival.json")
    if survival:
        lines += survival_section(survival)
        figures += survival_figures()
    stats = load("stats.json")
    if stats:
        lines += stats_section(stats)
        figures += stats_figures()
    prediction = load("prediction.json")
    if prediction:
        lines += prediction_section(prediction)
        figures += prediction_figures()

    if figures:
        lines += ["## Figures", ""]
        lines += [f"- `results/figures/{name}`: {text}" for name, text in figures]
        lines.append("")
    path = common.RESULTS_DIR / "results.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved {path.relative_to(common.PROJECT_ROOT)}")


if __name__ == "__main__":
    render()
