"""Stage 3: predict whether a request will be closed within 7 days (scikit-learn).

Run:  python analysis/prediction.py      (about 10 minutes)

Target (y): 1 = closed within 7 days (168 hours) of being created, 0 = not.
- Closed requests: y = 1 if resolution_hours <= 168.
- Still-open requests: y = 0. They've been open for 9+ months at the
  snapshot, so we know for certain they weren't closed within 7 days.
- Left out: requests whose closing time is unknown (status "Closed" with no
  close date, or closed before created), the same rule as Stages 1-2.

Features: only what is known the moment a request is created: agency,
complaint type, borough, month, day of week, hour, and channel (phone,
online, mobile app...). No leakage: nothing from closed_date, status or the
resolution text, which are only known after the fact.

Split by time, not at random: train on requests created Jan-Sep 2025, test on
Oct-Dec 2025. That's how the model would be used (trained on the past,
predicting the future), and it stops the test from leaking information from
days the model already saw.

Outputs: results/figures/3*.png, results/data/prediction.json, results/results.md
"""
import time

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (brier_score_loss, confusion_matrix, f1_score, precision_score,
                             recall_score, roc_auc_score, roc_curve)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

import common
import results_md
from common import BLUE, INK, INK_2, MUTED, ORANGE, PALETTE, SEED, plt

TARGET_HOURS = 7 * 24
CATEGORICAL = ["agency", "complaint_type", "borough", "channel"]
FEATURES = CATEGORICAL + ["month", "day_of_week", "hour"]
# Anything describing how or when a request was closed is off limits.
FORBIDDEN = {"closed_at", "closed_date", "status", "resolution_status", "resolution_hours",
             "resolution_description", "resolution_action_updated_date"}
TRAIN_MONTHS, TEST_MONTHS = range(1, 10), range(10, 13)
PERMUTATION_ROWS = 200_000  # test rows used for permutation importance (speed)
PERMUTATION_REPEATS = 5


# --- Data ----------------------------------------------------------------------

def build_target(df):
    """y = 1 if closed within 7 days, 0 if not; NaN where the outcome is unknown."""
    status = df["resolution_status"]
    still_open = (status == "no_close_date") & (df["status"].fillna("") != "Closed")
    y = pd.Series(np.nan, index=df.index)
    closed = status == "closed"
    y[closed] = (df.loc[closed, "resolution_hours"] <= TARGET_HOURS).astype(float)
    y[still_open] = 0.0
    return y


def creation_features(df):
    """Features known at the moment the request is created."""
    X = pd.DataFrame({
        "agency": df["agency"],
        "complaint_type": df["complaint_type"],
        "borough": df["borough"],
        "channel": df["open_data_channel_type"],
        "month": df["created_at"].dt.month,
        "day_of_week": df["created_at"].dt.dayofweek,  # 0 = Monday
        "hour": df["created_at"].dt.hour,
    })
    assert not FORBIDDEN & set(X.columns), "leakage: a post-creation column is a feature"
    return X


# --- Models --------------------------------------------------------------------

def make_models():
    """The three models, each a Pipeline so encoding is learned on training data only."""
    # Logistic regression needs numbers on a common scale: one 0/1 column per
    # category (hour and weekday too, since their effect isn't a straight line),
    # and month scaled. Rare complaint types (<100 training rows) share a column,
    # and categories never seen in training are ignored.
    logistic = Pipeline([
        ("encode", ColumnTransformer([
            ("onehot", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=100),
             CATEGORICAL + ["day_of_week", "hour"]),
            ("scale", StandardScaler(), ["month"]),
        ])),
        ("model", LogisticRegression(max_iter=1000)),
    ])
    # Gradient boosting handles categories natively: each category gets an
    # integer code, and categories unseen in training become "missing".
    boosting = Pipeline([
        ("encode", ColumnTransformer([
            ("codes", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=np.nan),
             CATEGORICAL),
        ], remainder="passthrough")),
        ("model", HistGradientBoostingClassifier(
            categorical_features=list(range(len(CATEGORICAL))), random_state=SEED)),
    ])
    return {
        "Majority-class baseline": DummyClassifier(strategy="most_frequent"),
        "Logistic regression": logistic,
        "Gradient boosting": boosting,
    }


def evaluate(y_true, prob, threshold=0.5):
    """Metrics for both classes. Class 1 = closed within 7 days, class 0 = slow."""
    pred = (prob >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    out = {"roc_auc": roc_auc_score(y_true, prob),
           "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}}
    for label, cls in [("within_7d", 1), ("slow", 0)]:
        out[label] = {
            "precision": precision_score(y_true, pred, pos_label=cls, zero_division=0),
            "recall": recall_score(y_true, pred, pos_label=cls, zero_division=0),
            "f1": f1_score(y_true, pred, pos_label=cls, zero_division=0),
        }
    return out


def permutation_drop(model, X, y, columns, rng, repeats):
    """How much ROC AUC drops when `columns` are shuffled (together, as one block).

    Shuffling breaks the link between those columns and the outcome while
    keeping everything else the same. A big drop = the model relies on them.
    """
    base = roc_auc_score(y, model.predict_proba(X)[:, 1])
    drops = []
    for _ in range(repeats):
        shuffled = X.copy()
        order = rng.permutation(len(X))
        for col in columns:  # same order for every column in the block
            shuffled[col] = X[col].to_numpy()[order]
        drops.append(base - roc_auc_score(y, model.predict_proba(shuffled)[:, 1]))
    return float(np.mean(drops)), float(np.std(drops))


# --- Figures -------------------------------------------------------------------

def plot_roc(curves):
    fig, ax = plt.subplots(figsize=(7, 6.5))
    colors = {"Majority-class baseline": MUTED, "Logistic regression": ORANGE,
              "Gradient boosting": BLUE}
    for name, (fpr, tpr, auc) in curves.items():
        ax.plot(fpr, tpr, color=colors[name], linewidth=2, label=f"{name} (AUC {auc:.4f})")
    ax.set_xlabel("False positive rate (slow requests wrongly predicted fast)")
    ax.set_ylabel("True positive rate (fast requests correctly predicted)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="lower right")
    ax.set_title("ROC curves: predicting closure within 7 days", pad=24)
    common.subtitle(ax, "Trained on Jan-Sep 2025, tested on Oct-Dec 2025")
    common.save_figure(fig, "3a_roc_curves.png")


def plot_confusion(results):
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.3))
    for ax, (name, r) in zip(axes, results.items()):
        c = r["confusion"]
        grid = np.array([[c["tn"], c["fp"]], [c["fn"], c["tp"]]])
        share = grid / grid.sum()
        ax.imshow(share, cmap=common.matplotlib.colors.LinearSegmentedColormap.from_list(
            "b", [common.SURFACE, BLUE]), vmin=0, vmax=1)
        for i in range(2):
            for j in range(2):
                ax.text(j, i, f"{grid[i, j]:,}\n({100 * share[i, j]:.2f}%)", ha="center",
                        va="center", fontsize=9.5, color="white" if share[i, j] > 0.5 else INK)
        ax.set_xticks([0, 1], ["Predicted slow", "Predicted\nwithin 7 days"])
        ax.set_yticks([0, 1], ["Actually slow", "Actually\nwithin 7 days"])
        ax.grid(False)
        ax.set_title(name, fontsize=11)
    fig.suptitle("Confusion matrices on the Oct-Dec 2025 test set (threshold 0.5)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    common.save_figure(fig, "3b_confusion_matrices.png")


def plot_calibration(name, y_true, prob, brier):
    frac_pos, mean_pred = calibration_curve(y_true, prob, n_bins=10, strategy="quantile")
    fig, ax = plt.subplots(figsize=(7, 6.5))
    ax.plot([0, 1], [0, 1], color=MUTED, linewidth=1, label="Perfectly calibrated")
    ax.plot(mean_pred, frac_pos, "o-", color=BLUE, linewidth=2, markersize=6, label=name)
    ax.set_xlabel("Predicted probability of closing within 7 days")
    ax.set_ylabel("Actual share closed within 7 days")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper left")
    ax.set_title(f"Calibration: {name}", pad=24)
    near_one = int((mean_pred > 0.98).sum())
    common.subtitle(ax, f"{len(frac_pos)} bins with equal numbers of requests; {near_one} of them "
                        f"sit above 0.98 and overlap. Brier score {brier:.4f} (lower is better).")
    common.save_figure(fig, "3c_calibration.png")


def plot_importance(importance):
    rows = sorted(importance, key=lambda r: r["auc_drop"])
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(9.5, 0.5 * len(rows) + 1.6))
    colors = [MUTED if "+" in r["feature"] else BLUE for r in rows]
    ax.barh(y, [r["auc_drop"] for r in rows], xerr=[r["std"] for r in rows], height=0.55,
            color=colors, error_kw={"ecolor": INK_2, "linewidth": 1})
    for yi, r in zip(y, rows):
        ax.text(r["auc_drop"] + r["std"] + 0.003, yi, f"{r['auc_drop']:.4f}", va="center",
                fontsize=9, color=INK_2)
    ax.set_yticks(y, [r["feature"] for r in rows])
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(r["auc_drop"] + r["std"] for r in rows) * 1.2)
    ax.set_xlabel("Drop in ROC AUC when the feature is shuffled (higher = more important)")
    ax.set_title("Permutation feature importance (gradient boosting)", pad=24)
    common.subtitle(ax, f"{PERMUTATION_ROWS:,} test rows, {PERMUTATION_REPEATS} shuffles each "
                        f"(seed {SEED}). Gray = agency and complaint type shuffled together.")
    common.save_figure(fig, "3d_permutation_importance.png")


# --- Main ------------------------------------------------------------------------

def main():
    print("Loading clean_311 ...")
    df = common.load_requests(["created_at", "agency", "complaint_type", "borough",
                               "open_data_channel_type", "status", "resolution_status",
                               "resolution_hours"])
    y_all = build_target(df)
    known = y_all.notna()
    X_all, y_all = creation_features(df[known]), y_all[known].astype(int)
    train = X_all["month"].isin(TRAIN_MONTHS)
    test = X_all["month"].isin(TEST_MONTHS)
    X_train, y_train, X_test, y_test = X_all[train], y_all[train], X_all[test], y_all[test]
    counts = {"rows_in_clean_table": len(df), "outcome_unknown_excluded": int((~known).sum()),
              "train_rows": len(X_train), "test_rows": len(X_test),
              "train_pct_within_7d": 100 * y_train.mean(), "test_pct_within_7d": 100 * y_test.mean(),
              "test_complaint_types_unseen_in_train":
                  int(len(set(X_test["complaint_type"]) - set(X_train["complaint_type"])))}
    print(counts)

    results, curves, probs, fit_seconds = {}, {}, {}, {}
    models = make_models()
    for name, model in models.items():
        start = time.time()
        model.fit(X_train, y_train)
        fit_seconds[name] = time.time() - start
        prob = model.predict_proba(X_test)[:, 1]
        probs[name] = prob
        results[name] = evaluate(y_test.to_numpy(), prob)
        fpr, tpr, _ = roc_curve(y_test, prob)
        curves[name] = (fpr, tpr, results[name]["roc_auc"])
        r = results[name]
        print(f"{name:24} AUC {r['roc_auc']:.4f} | slow class P {r['slow']['precision']:.4f} "
              f"R {r['slow']['recall']:.4f} F1 {r['slow']['f1']:.4f} | fit {fit_seconds[name]:.0f}s")

    best = max((n for n in results if n != "Majority-class baseline"),
               key=lambda n: results[n]["roc_auc"])
    brier = brier_score_loss(y_test, probs[best])
    frac_pos, mean_pred = calibration_curve(y_test, probs[best], n_bins=10, strategy="quantile")
    plot_roc(curves)
    plot_confusion(results)
    plot_calibration(best, y_test, probs[best], brier)

    print(f"Permutation importance for {best} on {PERMUTATION_ROWS:,} test rows ...")
    rng = np.random.default_rng(SEED)
    sample = rng.choice(len(X_test), size=PERMUTATION_ROWS, replace=False)
    Xs, ys = X_test.iloc[sample].reset_index(drop=True), y_test.iloc[sample].to_numpy()
    importance = []
    for cols in [[f] for f in FEATURES] + [["agency", "complaint_type"]]:
        mean, std = permutation_drop(models[best], Xs, ys, cols, rng, PERMUTATION_REPEATS)
        importance.append({"feature": " + ".join(cols), "auc_drop": mean, "std": std})
        print(f"  {' + '.join(cols):26} AUC drop {mean:.4f} (sd {std:.4f})")
    plot_importance(importance)

    common.save_json("prediction.json", {
        "target_hours": TARGET_HOURS, "features": FEATURES, "counts": counts,
        "split": {"train": "Jan-Sep 2025 (created)", "test": "Oct-Dec 2025 (created)"},
        "threshold": 0.5, "results": results, "fit_seconds": fit_seconds, "best_model": best,
        # ROC curves on a fixed grid of 101 false-positive rates (small enough for
        # the dashboard; the full curves have hundreds of thousands of points)
        "roc": {name: {"fpr": list(np.linspace(0, 1, 101)),
                       "tpr": list(np.interp(np.linspace(0, 1, 101), fpr, tpr))}
                for name, (fpr, tpr, _) in curves.items()},
        "calibration": {"brier": brier, "mean_predicted": list(mean_pred),
                        "fraction_positive": list(frac_pos)},
        "permutation": {"rows": PERMUTATION_ROWS, "repeats": PERMUTATION_REPEATS, "seed": SEED,
                        "importance": importance},
    })
    results_md.render()


if __name__ == "__main__":
    main()
