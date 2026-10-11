"""Stage 2: statistical testing of resolution time by agency, borough and season.

Run:  python analysis/stats_tests.py      (about 8 minutes)

1. Kruskal-Wallis test for each factor. It asks: do the groups' resolution
   times come from the same distribution? It works on ranks, so it doesn't
   assume normal data (ours is extremely skewed) and isn't thrown off by the
   handful of year-long requests.
2. Epsilon-squared effect size for each test: the share of the variation in
   (ranked) resolution time explained by the grouping, from 0 to 1. With
   3.6M rows every p-value is essentially 0, so the p-value can't tell a big
   difference from a trivial one. The effect size can.
3. Bootstrapped 95% CIs for the median per agency and per borough: resample
   each group with replacement 1,000 times, take the median each time, and
   use the middle 95% of those medians.

Uses closed requests only (resolution_status = 'closed'), the same rows as
the earlier medians: these methods need an observed resolution time. Open
requests are handled in Stage 1 (survival analysis).

Outputs: results/figures/2*.png, results/data/stats.json, results/results.md
"""
import time

import numpy as np
from scipy.stats import kruskal

import common
import results_md
from common import BLUE, INK_2, MUTED, SEED, plt

N_BOOT = 1_000


def epsilon_squared(h, n):
    """Effect size for Kruskal-Wallis: epsilon^2 = H / (n - 1).

    (The textbook form H / ((n^2 - 1) / (n + 1)) simplifies to this.)
    """
    return h / (n - 1)


def bootstrap_median_ci(values, n_boot, rng, level=0.95):
    """Percentile bootstrap CI for the median.

    Each round draws len(values) values WITH replacement (so some repeat and
    some are left out), which mimics drawing a new sample of the same size.
    The spread of the medians across rounds shows how much the median would
    move by chance.
    """
    values = np.asarray(values)
    medians = np.empty(n_boot)
    for i in range(n_boot):
        medians[i] = np.median(values[rng.integers(0, len(values), len(values))])
    tail = (1 - level) / 2 * 100
    lower, upper = np.percentile(medians, [tail, 100 - tail])
    return float(lower), float(upper)


def kw_test(df, factor):
    groups = [g["resolution_hours"].to_numpy() for _, g in df.groupby(factor)]
    h, p = kruskal(*groups)
    n = sum(len(g) for g in groups)
    return {"factor": factor, "groups": len(groups), "n": n, "H": float(h), "p_value": float(p),
            "df": len(groups) - 1, "epsilon_squared": epsilon_squared(h, n)}


# --- Figures ---------------------------------------------------------------------

def plot_effect_sizes(tests):
    tests = sorted(tests, key=lambda t: t["epsilon_squared"])
    y = np.arange(len(tests))
    values = [t["epsilon_squared"] for t in tests]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.barh(y, values, height=0.5, color=BLUE)
    for yi, t in zip(y, tests):
        ax.text(t["epsilon_squared"] + max(values) * 0.01, yi,
                f"{t['epsilon_squared']:.4f}   (H = {t['H']:,.0f})",
                va="center", fontsize=9, color=INK_2)
    ax.set_yticks(y, [t["factor"].title() for t in tests])
    ax.set_xlim(0, max(values) * 1.9)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Epsilon-squared (share of rank variation explained, 0 to 1)")
    top, second = tests[-1], tests[-2]
    ax.set_title(f"Effect size: {top['factor']} explains "
                 f"{top['epsilon_squared'] / second['epsilon_squared']:,.1f}x more than "
                 f"{second['factor']}, the next-largest factor", pad=24)
    common.subtitle(ax, "Kruskal-Wallis tests on resolution time, closed requests. "
                        "All p-values are ~0, so the effect size is what separates them.")
    common.save_figure(fig, "2a_effect_sizes.png")


def plot_bootstrap(rows, label, name, title, log_scale):
    rows = sorted(rows, key=lambda r: r["median"])
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(rows) + 1.8))
    ax.hlines(y, [r["ci_lower"] for r in rows], [r["ci_upper"] for r in rows],
              color=MUTED, linewidth=3)
    ax.scatter([r["median"] for r in rows], y, s=40, color=BLUE, zorder=3,
               edgecolor=common.SURFACE, linewidth=1.5)
    for yi, r in zip(y, rows):
        ax.text(r["ci_upper"] * (1.15 if log_scale else 1) + (0 if log_scale else 0.3), yi,
                f"{r['median']:,.2f} h  [{r['ci_lower']:,.2f} - {r['ci_upper']:,.2f}]",
                va="center", fontsize=8.5, color=INK_2)
    ax.set_yticks(y, [r[label] for r in rows])
    ax.grid(axis="y", visible=False)
    if log_scale:
        common.time_axis(ax)
        ax.set_xlim(right=max(r["ci_upper"] for r in rows) * 30)
        ax.set_xlabel("Median hours to close (log scale)")
    else:
        ax.set_xlim(0, max(r["ci_upper"] for r in rows) * 1.6)
        ax.set_xlabel("Median hours to close")
    ax.set_title(title, pad=24)
    common.subtitle(ax, f"Median with bootstrapped 95% CI ({N_BOOT:,} resamples, seed {SEED}), "
                        "closed requests. Most intervals are narrower than the dots.")
    common.save_figure(fig, name)


# --- Main ------------------------------------------------------------------------

def main():
    print("Loading clean_311 ...")
    df = common.load_requests(["agency", "borough", "season", "resolution_status",
                               "resolution_hours"])
    closed = df[df["resolution_status"] == "closed"]
    known = closed[closed["borough"] != "UNSPECIFIED"]
    print(f"closed requests: {len(closed):,} (known borough: {len(known):,})")

    tests = []
    for factor, data in [("agency", closed), ("borough", known), ("season", closed)]:
        t = kw_test(data, factor)
        tests.append(t)
        print(f"Kruskal-Wallis {factor:8}: H = {t['H']:,.2f}, df = {t['df']}, "
              f"p = {t['p_value']}, epsilon^2 = {t['epsilon_squared']:.6f}")
    plot_effect_sizes(tests)

    rng = np.random.default_rng(SEED)
    boot = {}
    started = time.time()
    for factor, data in [("agency", closed), ("borough", known)]:
        rows = []
        for name, g in data.groupby(factor):
            values = g["resolution_hours"].to_numpy()
            lower, upper = bootstrap_median_ci(values, N_BOOT, rng)
            rows.append({factor: name, "n": len(values), "median": float(np.median(values)),
                         "ci_lower": lower, "ci_upper": upper})
            print(f"  bootstrap {factor} {name:14} median {np.median(values):10,.2f} "
                  f"[{lower:,.2f} - {upper:,.2f}]")
        boot[factor] = rows
    bootstrap_minutes = (time.time() - started) / 60
    print(f"bootstrap took {bootstrap_minutes:.1f} minutes")
    plot_bootstrap(boot["agency"], "agency", "2b_bootstrap_median_agency.png",
                   "Median resolution time by agency, with 95% bootstrap CIs", log_scale=True)
    plot_bootstrap([{**r, "borough": r["borough"].title()} for r in boot["borough"]], "borough",
                   "2c_bootstrap_median_borough.png",
                   "Median resolution time by borough, with 95% bootstrap CIs", log_scale=False)

    common.save_json("stats.json", {"n_closed": len(closed), "n_known_borough": len(known),
                                    "n_boot": N_BOOT, "seed": SEED, "subsampled": False,
                                    "bootstrap_minutes": bootstrap_minutes,
                                    "kruskal_wallis": tests, "bootstrap": boot})
    results_md.render()


if __name__ == "__main__":
    main()
