"""Run the analysis SQL, draw the charts, and write the top findings.

Run:  python src/findings.py

Outputs
- outputs/charts/*.png  (six charts)
- outputs/findings.md   (top 3 findings + caveats)

Every number in findings.md is read from a query result at run time, never
typed in by hand, so rerunning this script always reproduces it.
"""
import matplotlib

matplotlib.use("Agg")  # draw to files only; no window needed

import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
import pandas as pd
import psycopg
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

import config
from run_analysis import ANALYSIS_DIR, create_view, run_query_file

# --- Chart style -------------------------------------------------------------
# One quiet style for every chart: light surface, gray hairline grid, and
# at most two data colors (blue = main series, orange = second series).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
RED = "#e34948"
DIVERGING_BLUE = "#256abf"
NEUTRAL = "#f0efec"

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 10,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK_2,
    "axes.titlesize": 13,
    "axes.titleweight": "semibold",
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": MUTED,
    "ytick.color": INK_2,
    "text.color": INK,
    "legend.frameon": False,
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
    "savefig.facecolor": SURFACE,
})

# Hours -> readable tick labels for log-scale time axes.
TIME_TICKS = {1 / 60: "1 min", 1: "1 hour", 24: "1 day", 24 * 7: "1 week",
              24 * 30: "30 days", 24 * 365: "1 year"}


def use_time_axis(ax, axis="x"):
    """Log scale with ticks at familiar durations instead of raw hours."""
    getattr(ax, f"set_{axis}scale")("log")
    getattr(ax, f"set_{axis}ticks")(list(TIME_TICKS), list(TIME_TICKS.values()))
    getattr(ax, f"{axis}axis").set_minor_locator(matplotlib.ticker.NullLocator())


def contrast_ratio(color_a, color_b):
    """WCAG contrast ratio between two colors (1 = identical, 21 = black on white)."""
    def luminance(color):
        channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                    for c in matplotlib.colors.to_rgb(color)]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    lighter, darker = sorted([luminance(color_a), luminance(color_b)], reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def readable_text_color(background):
    """White or dark ink, whichever has more contrast on this background."""
    return max(["white", INK], key=lambda text: contrast_ratio(text, background))


def subtitle(ax, text):
    ax.text(0, 1.02, text, transform=ax.transAxes, color=INK_2, fontsize=9.5, va="bottom")


def save(fig, name):
    path = config.CHARTS_DIR / name
    fig.savefig(path)
    plt.close(fig)
    print(f"saved {path.relative_to(config.PROJECT_ROOT)}")


def num(x, decimals=2):
    """Format a query value exactly as returned (2 decimals, thousands commas)."""
    return f"{float(x):,.{decimals}f}"


# --- Charts --------------------------------------------------------------------

def chart_distribution(dist, overview):
    median = float(overview["median_hours"])
    p90 = float(overview["p90_hours"])
    mean = float(overview["mean_hours"])

    bins = dist.dropna(subset=["log10_bin_start"]).astype(float)
    n_zero = int(dist.loc[dist["log10_bin_start"].isna(), "n_requests"].sum())
    left = 10 ** bins["log10_bin_start"]
    width = 10 ** (bins["log10_bin_start"] + 0.25) - left

    fig, ax = plt.subplots(figsize=(10, 5))
    # edgecolor = surface color draws the thin gap between neighboring bars
    ax.bar(left, bins["n_requests"], width=width, align="edge", color=BLUE,
           edgecolor=SURFACE, linewidth=1)
    use_time_axis(ax)
    ax.grid(axis="x", visible=False)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:,.0f}K"))
    ax.set_ylabel("Requests")

    top = ax.get_ylim()[1]
    # The mean and p90 lines are close together, so the mean's label goes on
    # the left of its line and the others on the right, to avoid overlap.
    for value, label, color, side in [(median, "median", INK, "right"),
                                      (mean, "mean", INK_2, "left"),
                                      (p90, "90th percentile", INK_2, "right")]:
        ax.axvline(value, color=color, linewidth=1.2)
        x = value * 1.08 if side == "right" else value / 1.08
        ax.text(x, top * 0.97, f"{label}\n{num(value)} h", color=color, fontsize=9,
                va="top", ha="left" if side == "right" else "right")

    ax.set_title(f"Resolution times are highly skewed: the mean is {mean / median:.1f}x the median",
                 pad=24)
    subtitle(ax, f"{int(overview['n_with_resolution_time']):,} closed 2025 requests, log-scale bins. "
                 f"Not shown: {n_zero:,} closed in 0 seconds.")
    save(fig, "1_resolution_time_distribution.png")


def chart_agency(agency):
    df = agency.copy()
    df[["median_hours", "p90_hours"]] = df[["median_hours", "p90_hours"]].astype(float)
    df = df.sort_values("median_hours")
    df["label"] = (df["agency_name"].str.replace("Department of ", "", regex=False)
                   + " (" + df["agency"] + ")")
    y = np.arange(len(df))

    fig, ax = plt.subplots(figsize=(10, 6.5))
    ax.hlines(y, df["median_hours"], df["p90_hours"], color=AXIS, linewidth=2)
    # edgecolor = surface color gives each dot a ring so overlaps stay readable
    ax.scatter(df["median_hours"], y, s=60, color=BLUE, edgecolor=SURFACE, linewidth=2,
               zorder=3, label="Median")
    ax.scatter(df["p90_hours"], y, s=60, color=ORANGE, edgecolor=SURFACE, linewidth=2,
               zorder=3, label="90th percentile")
    ax.set_yticks(y, df["label"])
    ax.grid(axis="y", visible=False)
    use_time_axis(ax)
    ax.legend(loc="lower right")
    ax.set_title("The agency handling a request makes the biggest difference", pad=24)
    subtitle(ax, "Median and 90th percentile resolution time by agency, 2025 (log scale)")
    save(fig, "2_resolution_by_agency.png")


def chart_slowest_types(slow):
    df = slow.copy()
    for col in ["median_days", "pct_closed_within_30d", "pct_no_close_date"]:
        df[col] = df[col].astype(float)
    df = df.iloc[::-1]  # slowest at the top
    y = np.arange(len(df))
    # Highlight types where many requests are still open (median too optimistic)
    unreliable = df["pct_no_close_date"] >= 20
    colors = np.where(unreliable, ORANGE, BLUE)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 7), sharey=True,
                                   gridspec_kw={"width_ratios": [1.4, 1]})
    ax1.barh(y, df["median_days"].fillna(0), height=0.6, color=colors)
    ax1.axvline(60, color=INK_2, linewidth=1, zorder=1)
    ax1.text(61, len(df) - 0.4, "60 days", color=INK_2, fontsize=9)
    for yi, value in zip(y, df["median_days"]):
        # The surface-colored box lets the 60-day line pass behind the label.
        ax1.text((0 if np.isnan(value) else value) + 3, yi,
                 "no closed requests" if np.isnan(value) else num(value),
                 va="center", fontsize=8.5, color=INK_2, zorder=4,
                 bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1})
    ax1.set_yticks(y, df["complaint_type"])
    ax1.set_xlabel("Median days to close (closed requests only)")
    ax1.grid(axis="y", visible=False)

    ax2.barh(y, df["pct_closed_within_30d"], height=0.6, color=colors)
    for yi, value in zip(y, df["pct_closed_within_30d"]):
        ax2.text(value + 1, yi, f"{num(value)}%", va="center", fontsize=8.5, color=INK_2)
    ax2.set_xlim(0, 100)
    ax2.set_xlabel("% closed within 30 days (open requests count as not closed)")
    ax2.grid(axis="y", visible=False)

    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE),
               plt.Rectangle((0, 0), 1, 1, color=ORANGE)]
    fig.legend(handles, ["Under 20% still open", "20% or more still open (median too optimistic)"],
               loc="upper right", ncol=2, bbox_to_anchor=(1, 0.98))
    ax1.set_title("Slowest complaint types (1,000+ requests)", pad=34)
    subtitle(ax1, "Top 15 by median or by % closed within 30 days")
    save(fig, "3_slowest_complaint_types.png")


def chart_season_mix(mix):
    df = mix.copy()
    panels = [("median_hours_all", "All requests"),
              ("median_hours_nypd", "NYPD only"),
              ("median_hours_other_agencies", "All other agencies")]
    x = np.arange(len(df))
    labels = [f"{s}\n{num(p)}% NYPD" for s, p in zip(df["season"], df["pct_nypd"])]

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.8))
    for ax, (col, name) in zip(axes, panels):
        values = df[col].astype(float)
        ax.bar(x, values, width=0.55, color=BLUE)
        for xi, v in zip(x, values):
            ax.text(xi, v, f"{num(v)} h", ha="center", va="bottom", fontsize=9, color=INK_2)
        ax.set_xticks(x, labels, fontsize=8.5)
        ax.grid(axis="x", visible=False)
        ax.set_ylim(0, values.max() * 1.18)
        ax.set_title(name, fontsize=11)
    axes[0].set_ylabel("Median hours to close")

    all_slowest = df.loc[df["median_hours_all"].astype(float).idxmax(), "season"]
    other_slowest = df.loc[df["median_hours_other_agencies"].astype(float).idxmax(), "season"]
    fig.suptitle(f"Overall, {all_slowest} looks slowest. Without NYPD, {other_slowest} is slowest.",
                 x=0.01, ha="left", fontsize=13, fontweight="semibold")
    fig.text(0.01, 0.91, "Each season has a different share of fast NYPD requests, which moves "
             "the overall median (a mix effect).", color=INK_2, fontsize=9.5)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    save(fig, "4_season_mix_effect.png")


def chart_type_by_borough(inter):
    df = inter.copy()
    df["ratio_to_citywide"] = df["ratio_to_citywide"].astype(float)
    df["citywide_median_hours"] = df["citywide_median_hours"].astype(float)
    order = (df.drop_duplicates("complaint_type")
               .sort_values("citywide_median_hours")["complaint_type"].tolist())
    boroughs = sorted(df["borough"].unique())
    grid = df.pivot(index="complaint_type", columns="borough", values="ratio_to_citywide")
    grid = grid.loc[order, boroughs]
    citywide = df.drop_duplicates("complaint_type").set_index("complaint_type")["citywide_median_hours"]

    # log2 so that 2x slower and 2x faster sit the same distance from 1.0
    log_ratio = np.log2(grid.values)
    limit = np.nanmax(np.abs(log_ratio))
    cmap = LinearSegmentedColormap.from_list("faster_slower", [DIVERGING_BLUE, NEUTRAL, RED])
    norm = TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit)

    fig, ax = plt.subplots(figsize=(10, 6.5))
    image = ax.imshow(log_ratio, cmap=cmap, norm=norm, aspect="auto")
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            cell_color = cmap(norm(log_ratio[i, j]))
            ax.text(j, i, f"{grid.values[i, j]:.2f}x", ha="center", va="center", fontsize=9,
                    color=readable_text_color(cell_color))
    ax.set_xticks(range(len(boroughs)), [b.title() for b in boroughs])
    ax.set_yticks(range(len(order)), [f"{t}  ({num(citywide[t])} h)" for t in order])
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    ticks = [0.25, 0.5, 1, 2, 4]
    ticks = [t for t in ticks if abs(np.log2(t)) <= limit]
    bar = fig.colorbar(image, ax=ax, ticks=np.log2(ticks), shrink=0.8)
    bar.ax.set_yticklabels([f"{t}x" for t in ticks])
    bar.set_label("Borough median ÷ citywide median for that type")
    bar.outline.set_visible(False)
    ax.set_title("The same complaint type closes at different speeds in different boroughs", pad=24)
    subtitle(ax, "Top 10 complaint types by volume. Red = slower than citywide, blue = faster. "
                 "Citywide median in brackets.")
    save(fig, "5_complaint_type_by_borough.png")


def chart_open_share(by_type, overview):
    df = by_type[by_type["n_requests"] >= 1000].copy()
    df["pct_no_close_date"] = df["pct_no_close_date"].astype(float)
    df = df.nlargest(10, "pct_no_close_date").iloc[::-1]
    y = np.arange(len(df))

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.barh(y, df["pct_no_close_date"], height=0.6, color=BLUE)
    for yi, v, n in zip(y, df["pct_no_close_date"], df["n_requests"]):
        ax.text(v + 1, yi, f"{num(v)}%  of {int(n):,}", va="center", fontsize=9, color=INK_2)
    ax.set_yticks(y, df["complaint_type"])
    ax.set_xlim(0, 115)
    ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.grid(axis="y", visible=False)
    ax.set_title("Where many requests are still open, the median is too optimistic", pad=24)
    subtitle(ax, f"Share of requests with no close date, complaint types with 1,000+ requests "
                 f"(all requests: {num(overview['pct_no_close_date'])}%)")
    save(fig, "6_share_still_open.png")


# --- Findings --------------------------------------------------------------------

def require(condition, claim):
    """Stop instead of writing a sentence the data no longer supports.

    The numbers in findings.md are filled in from queries, but some wording
    ("slower", "reverses", "far more") states a direction. Each such phrase
    is checked here, so if the data ever changed, the script would fail loudly
    rather than print a claim that contradicts its own numbers.
    """
    if not condition:
        raise ValueError(f"Finding text no longer matches the data: {claim}")


def spread(df):
    """Fastest and slowest group by median, ignoring groups with no median."""
    d = df.dropna(subset=["median_hours"]).copy()
    d["median_hours"] = d["median_hours"].astype(float)
    fastest = d.loc[d["median_hours"].idxmin()]
    slowest = d.loc[d["median_hours"].idxmax()]
    return fastest, slowest


def write_findings(r):
    ov = r["00"].iloc[0]
    agency = r["02"].copy()
    total = int(ov["n_requests"])

    a_fast, a_slow = spread(agency)
    b_fast, b_slow = spread(r["03"])
    s_fast, s_slow = spread(r["04"])
    nypd = agency.set_index("agency").loc["NYPD"]
    second = agency.sort_values("n_requests", ascending=False).iloc[1]

    mix = r["07"].set_index("season")
    mix_all_slow = mix["median_hours_all"].astype(float).idxmax()
    mix_all_fast = mix["median_hours_all"].astype(float).idxmin()
    mix_other_slow = mix["median_hours_other_agencies"].astype(float).idxmax()
    mix_other_fast = mix["median_hours_other_agencies"].astype(float).idxmin()

    inter = r["06"].copy()
    inter["ratio_to_citywide"] = inter["ratio_to_citywide"].astype(float)
    bronx = inter[inter["borough"] == "BRONX"].set_index("complaint_type")["ratio_to_citywide"]
    manhattan = inter[inter["borough"] == "MANHATTAN"].set_index("complaint_type")["ratio_to_citywide"]

    slow = r["05"].copy()
    slow["median_hours"] = slow["median_hours"].astype(float)
    sixty = slow[(slow["median_hours"] >= 1440) & (slow["median_hours"] < 1441)]
    helicopter = slow.set_index("complaint_type").loc["Noise - Helicopter"]

    def row(df, key, col):
        return df.loc[key, col]

    def ratio(fast, slow):
        return float(slow["median_hours"]) / float(fast["median_hours"])

    # --- Check every direction-word in the text below against the data ---
    require(ratio(a_fast, a_slow) > ratio(b_fast, b_slow)
            and ratio(a_fast, a_slow) > ratio(s_fast, s_slow),
            "agency spread is wider than borough and season spread")
    require(a_fast["agency"] == "NYPD", "NYPD is the fastest agency")
    require(mix_all_slow != mix_other_slow, "the slowest season changes once NYPD is removed")
    require(mix_other_fast == mix_all_slow,
            "the overall slowest season is the fastest for other agencies")
    require(float(row(mix, mix_all_slow, "pct_nypd")) < float(row(mix, mix_all_fast, "pct_nypd")),
            "NYPD is a smaller share in the overall slowest season")
    require(float(row(mix, mix_all_slow, "median_hours_nypd"))
            > float(row(mix, mix_all_fast, "median_hours_nypd")),
            "NYPD itself is slower in the overall slowest season")
    for complaint_type in ["Illegal Parking", "Noise - Residential", "Abandoned Vehicle"]:
        require(bronx[complaint_type] > 1, f"Bronx is slower for {complaint_type}")
    require(bronx["HEAT/HOT WATER"] < 1, "Bronx is faster for HEAT/HOT WATER")
    require(manhattan["Illegal Parking"] < 1, "Manhattan is faster for Illegal Parking")
    require(manhattan["UNSANITARY CONDITION"] > 1, "Manhattan is slower for UNSANITARY CONDITION")
    require(len(sixty) > 0, "some complaint types have a median of about 60 days")

    lines = [
        "# Top findings",
        "",
        "Generated by `python src/findings.py`. Every number comes from the SQL files in "
        "`sql/analysis/` (file noted per finding); rerun the script to reproduce them.",
        "",
        f"Scope: {total:,} NYC 311 requests created in 2025; "
        f"{int(ov['n_with_resolution_time']):,} have a valid resolution time. "
        f"Overall median {num(ov['median_hours'])} h, 90th percentile {num(ov['p90_hours'])} h, "
        f"mean {num(ov['mean_hours'])} h.",
        "",
        "## 1. The agency handling a request matters far more than borough or season",
        "",
        f"- Range of medians across the {len(agency)} agencies: {num(a_fast['median_hours'])} h "
        f"({a_fast['agency']}) to {num(a_slow['median_hours'])} h ({a_slow['agency']}). "
        f"Across boroughs: {num(b_fast['median_hours'])} h ({b_fast['borough'].title()}) to "
        f"{num(b_slow['median_hours'])} h ({b_slow['borough'].title()}). Across seasons: "
        f"{num(s_fast['median_hours'])} h ({s_fast['season']}) to "
        f"{num(s_slow['median_hours'])} h ({s_slow['season']}).",
        f"- NYPD handles {int(nypd['n_requests']):,} requests "
        f"({100 * int(nypd['n_requests']) / total:.2f}% of all) with a median of "
        f"{num(nypd['median_hours'])} h. The next-largest agency, {second['agency']} "
        f"({int(second['n_requests']):,} requests), has a median of {num(second['median_hours'])} h.",
        f"- The slowest agency, {a_slow['agency']}, has {num(a_slow['pct_no_close_date'])}% of "
        f"requests with no close date, so even its median understates the delay.",
        "- Source: `00_overview.sql`, `02_by_agency.sql`, `03_by_borough.sql`, `04_by_season.sql`",
        "",
        "## 2. The season pattern reverses once NYPD is separated out (mix effect)",
        "",
        f"- All requests: {mix_all_slow} is slowest ({num(row(mix, mix_all_slow, 'median_hours_all'))} h "
        f"median) and {mix_all_fast} fastest ({num(row(mix, mix_all_fast, 'median_hours_all'))} h).",
        f"- All agencies except NYPD: {mix_other_slow} is slowest "
        f"({num(row(mix, mix_other_slow, 'median_hours_other_agencies'))} h) and {mix_other_fast} "
        f"fastest ({num(row(mix, mix_other_fast, 'median_hours_other_agencies'))} h).",
        f"- Why: two things lift {mix_all_slow}'s overall median. NYPD, the fastest agency, is a "
        f"smaller share of requests ({num(row(mix, mix_all_slow, 'pct_nypd'))}% in {mix_all_slow} vs "
        f"{num(row(mix, mix_all_fast, 'pct_nypd'))}% in {mix_all_fast}), and NYPD itself is slower "
        f"in {mix_all_slow} ({num(row(mix, mix_all_slow, 'median_hours_nypd'))} h vs "
        f"{num(row(mix, mix_all_fast, 'median_hours_nypd'))} h). Meanwhile, for all other agencies "
        f"combined, {mix_other_fast} is the fastest season.",
        "- Source: `04_by_season.sql`, `07_season_mix.sql`",
        "",
        "## 3. Borough differences depend on the complaint type",
        "",
        f"- Bronx vs citywide median for the same type: Illegal Parking "
        f"{bronx['Illegal Parking']:.2f}x, Noise - Residential {bronx['Noise - Residential']:.2f}x, "
        f"Abandoned Vehicle {bronx['Abandoned Vehicle']:.2f}x (slower), but HEAT/HOT WATER "
        f"{bronx['HEAT/HOT WATER']:.2f}x (faster).",
        f"- Manhattan: Illegal Parking {manhattan['Illegal Parking']:.2f}x (faster) but "
        f"UNSANITARY CONDITION {manhattan['UNSANITARY CONDITION']:.2f}x (slower).",
        "- So no borough is simply slow or fast; it depends on which agency and problem.",
        "- Source: `06_complaint_type_by_borough.sql`",
        "",
        "## Caveats",
        "",
        f"- **Open requests:** {num(ov['pct_no_close_date'])}% of all requests have no close date, "
        f"but it is concentrated: e.g. Noise - Helicopter {num(helicopter['pct_no_close_date'])}% "
        f"(median {num(helicopter['median_hours'])} h, {num(helicopter['pct_closed_within_30d'])}% "
        f"closed within 30 days). Medians for such types are too optimistic; % closed within 30 days "
        f"counts open requests and is the safer comparison. (`01`, `05`)",
        f"- **Possible automatic closures:** {len(sixty)} complaint types "
        f"({', '.join(sixty['complaint_type'])}) have medians between 1,440 and 1,441 hours "
        f"(60 days), with 90th percentiles of {num(sixty['p90_hours'].astype(float).min())}-"
        f"{num(sixty['p90_hours'].astype(float).max())} h. That pattern suggests a fixed 60-day "
        f"closing rule rather than real work time; the data can't confirm the process. (`05`)",
        "- **Not causal:** these are descriptive comparisons; complaint mix, staffing and how each "
        "agency closes requests are not separated out except where noted.",
    ]
    path = config.PROJECT_ROOT / "outputs" / "findings.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"saved {path.relative_to(config.PROJECT_ROOT)}")


def main():
    config.CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(config.get_database_url()) as conn:
        create_view(conn)
        results = {p.name[:2]: run_query_file(p, conn)
                   for p in sorted(ANALYSIS_DIR.glob("*.sql"))}
        print(f"ran {len(results)} query files")

    overview = results["00"].iloc[0]
    chart_distribution(results["08"], overview)
    chart_agency(results["02"])
    chart_slowest_types(results["05"])
    chart_season_mix(results["07"])
    chart_type_by_borough(results["06"])
    chart_open_share(results["01"], overview)
    write_findings(results)


if __name__ == "__main__":
    main()
