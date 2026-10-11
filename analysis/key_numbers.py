"""The headline numbers from Stages 1-3, gathered in one place.

results.md's summary tables, the README's Advanced Analysis section and the
terminal summary (summary.py) all read from key_numbers(), so the same number
can never differ between them. Every value comes from results/data/*.json.
"""
import json
import math

import common


def _load(name):
    path = common.DATA_DIR / name
    if not path.exists():
        raise SystemExit(f"{path.name} is missing: run the stage scripts in analysis/ first.")
    return json.loads(path.read_text(encoding="utf-8"))


def key_numbers():
    sv, st, pr = _load("survival.json"), _load("stats.json"), _load("prediction.json")
    agencies = {r["agency"]: r for r in sv["agencies"]}
    reached = [r for r in sv["agencies"] if r["difference_pct"] is not None]
    eps = {t["factor"]: t["epsilon_squared"] for t in st["kruskal_wallis"]}
    res = pr["results"]
    best = pr["best_model"]
    singles = sorted((r for r in pr["permutation"]["importance"] if "+" not in r["feature"]),
                     key=lambda r: r["auc_drop"], reverse=True)
    pair = next(r for r in pr["permutation"]["importance"] if "+" in r["feature"])
    others = [n for n in res if n not in ("Majority-class baseline", best)]

    # Largest Cox effect per group on the log scale (0.5x and 2x count equally)
    def largest_log_hr(prefix):
        return max(abs(math.log(r["hr"])) for r in sv["cox"]["hazard_ratios"]
                   if r["covariate"].startswith(prefix))
    cox_agency_dominates = largest_log_hr("agency:") > max(largest_log_hr("borough:"),
                                                           largest_log_hr("season:"))
    return {
        "cox_agency_dominates": cox_agency_dominates,
        "agency_largest_effect": eps["agency"] == max(eps.values()),
        "perm_rows": pr["permutation"]["rows"],
        # Stage 1
        "rows_used": sv["counts"]["rows_used"],
        "events": sv["counts"]["events_closed"],
        "censored": sv["counts"]["censored_still_open"],
        "excluded_unknown_time": (sv["counts"]["excluded_closed_status_without_date"]
                                  + sv["counts"]["excluded_closed_before_created"]
                                  + sv["counts"]["excluded_placeholder_date"]),
        "pct_censored": 100 * sv["counts"]["censored_still_open"] / sv["counts"]["rows_used"],
        "overall_closed_only": sv["overall"]["median_closed_only"],
        "overall_km": sv["overall"]["km_median"],
        "overall_km_ci": (sv["overall"]["km_ci_lower"], sv["overall"]["km_ci_upper"]),
        "top8_km": [(a, agencies[a]["km_median"], agencies[a]["km_ci_lower"],
                     agencies[a]["km_ci_upper"]) for a in sv["top8"]],
        "biggest_bias": sorted(reached, key=lambda r: r["difference_pct"], reverse=True)[:2],
        "n_under_1pct": sum(abs(r["difference_pct"]) < 1 for r in reached),
        "n_agencies": len(sv["agencies"]),
        "logrank_chi2": sv["logrank"]["test_statistic"],
        "logrank_df": sv["logrank"]["degrees_of_freedom"],
        "cox_concordance": sv["cox"]["concordance"],
        "cox_sample": sv["cox"]["sample_size"],
        "ph_violating": sv["ph_test"]["n_violating_p05"],
        "ph_covariates": sv["ph_test"]["n_covariates"],
        # Stage 2
        "eps": eps,
        "eps_ratio_vs_next": eps["agency"] / max(v for k, v in eps.items() if k != "agency"),
        "n_closed": st["n_closed"],
        "n_boot": st["n_boot"],
        "boot_widest": max(st["bootstrap"]["agency"], key=lambda r: r["ci_upper"] - r["ci_lower"]),
        # Stage 3
        "best_model": best,
        "best_auc": res[best]["roc_auc"],
        "baseline_auc": res["Majority-class baseline"]["roc_auc"],
        "runner_up": others[0] if others else None,
        "runner_up_auc": res[others[0]]["roc_auc"] if others else None,
        "best_slow": res[best]["slow"],
        "runner_up_slow": res[others[0]]["slow"] if others else None,
        "test_rows": pr["counts"]["test_rows"],
        "train_rows": pr["counts"]["train_rows"],
        "test_pct_within_7d": pr["counts"]["test_pct_within_7d"],
        "brier": pr["calibration"]["brier"],
        "top3_features": [(r["feature"], r["auc_drop"]) for r in singles[:3]],
        "pair_drop": pair["auc_drop"],
        "agency_rank": [r["feature"] for r in singles].index("agency") + 1,
    }
