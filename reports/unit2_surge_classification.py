"""Unit 2: leak-free next-day surge classification on the SCRUM-22 chronological split.

Reads processed/train.csv and processed/test.csv (features already lagged and scaled on train
only), tunes models with expanding-window date folds on train, picks the decision threshold on
out-of-fold train predictions, and scores once on the held-out test window.

Outputs (reports/outputs/): unit2_model_comparison.csv, unit2_cv_results.csv,
unit2_cohort_performance.csv, unit2_feature_importance.csv, unit2_label_audit.csv and figures.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, f1_score, precision_recall_curve,
                             precision_score, recall_score, roc_auc_score, confusion_matrix)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "outputs"
OUT.mkdir(parents=True, exist_ok=True)
SEED = 42
TARGET = "surge_label"
LAGS = [f"{base}_lag_{k}" for k in (1, 2, 3, 7)
        for base in ("search", "growth", "accel", "videos_published", "comments_published")]
FEATURES = LAGS + ["rolling_mean_7d", "rolling_std_7d"]


def date_folds(dates, n_folds=5):
    """Expanding-window folds on calendar dates: train on earlier dates, validate on the next block."""
    unique = np.sort(dates.unique())
    blocks = np.array_split(unique, n_folds + 1)
    for i in range(1, n_folds + 1):
        train_dates = np.concatenate(blocks[:i])
        yield dates.isin(train_dates).to_numpy(), dates.isin(blocks[i]).to_numpy()


def scores(y, prob, threshold):
    pred = (prob >= threshold).astype(int)
    return {
        "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
        "f1": f1_score(y, pred, zero_division=0),
        "roc_auc": roc_auc_score(y, prob) if len(np.unique(y)) > 1 else np.nan,
        "pr_auc": average_precision_score(y, prob) if len(np.unique(y)) > 1 else np.nan,
        "alerts": int(pred.sum()),
    }


def best_threshold(y, prob):
    p, r, t = precision_recall_curve(y, prob)
    f1 = 2 * p * r / np.clip(p + r, 1e-12, None)
    return float(t[np.nanargmax(f1[:-1])])


def main():
    train = pd.read_csv(ROOT / "processed" / "train.csv", parse_dates=["date"])
    test = pd.read_csv(ROOT / "processed" / "test.csv", parse_dates=["date"])
    brand_cols = [c for c in train.columns if c.startswith("brand_") or c.startswith("product_type_")]
    feats = FEATURES + brand_cols
    Xtr, ytr, Xte, yte = train[feats], train[TARGET], test[feats], test[TARGET]

    candidates = {
        "Logistic Regression (baseline)": [
            LogisticRegression(C=c, class_weight="balanced", max_iter=2000) for c in (0.1, 1.0)],
        "Random Forest": [
            RandomForestClassifier(n_estimators=300, max_depth=d, min_samples_leaf=5,
                                   class_weight="balanced_subsample", n_jobs=-1, random_state=SEED)
            for d in (6, 10)],
        "HistGradientBoosting": [
            HistGradientBoostingClassifier(learning_rate=lr, max_depth=d, max_iter=300,
                                           class_weight="balanced", random_state=SEED)
            for lr, d in ((0.05, 4), (0.1, 6))],
    }

    cv_rows, chosen = [], {}
    for name, models in candidates.items():
        best = None
        for model in models:
            oof = np.full(len(train), np.nan)
            fold_ap = []
            for tr_idx, va_idx in date_folds(train["date"]):
                model.fit(Xtr[tr_idx], ytr[tr_idx])
                oof[va_idx] = model.predict_proba(Xtr[va_idx])[:, 1]
                fold_ap.append(average_precision_score(ytr[va_idx], oof[va_idx]))
            mask = ~np.isnan(oof)
            thr = best_threshold(ytr[mask], oof[mask])
            params = {k: v for k, v in model.get_params().items()
                      if k in ("C", "max_depth", "learning_rate", "n_estimators", "max_iter")}
            row = {"model": name, "params": str(params), "cv_pr_auc_mean": np.mean(fold_ap),
                   "cv_pr_auc_std": np.std(fold_ap), "oof_threshold": thr,
                   "cv_f1_at_threshold": f1_score(ytr[mask], (oof[mask] >= thr).astype(int))}
            cv_rows.append(row)
            if best is None or row["cv_pr_auc_mean"] > best[1]["cv_pr_auc_mean"]:
                best = (model, row)
        chosen[name] = best
    pd.DataFrame(cv_rows).round(4).to_csv(OUT / "unit2_cv_results.csv", index=False)

    results, probs = [], {}
    # Rule baseline: yesterday's growth above the train-only surge cut-off -> surge today.
    rule_prob = test["growth_lag_1"].to_numpy()
    rule_thr = float(np.nanpercentile(train.loc[ytr == 1, "growth_lag_1"], 50))
    results.append({"model": "Persistence rule (baseline)", "params": "growth_lag_1 >= train median of surge days",
                    "threshold": rule_thr, **scores(yte, rule_prob, rule_thr)})
    probs["Persistence rule (baseline)"] = rule_prob
    for name, (model, row) in chosen.items():
        model.fit(Xtr, ytr)
        prob = model.predict_proba(Xte)[:, 1]
        probs[name] = prob
        results.append({"model": name, "params": row["params"], "threshold": row["oof_threshold"],
                        **scores(yte, prob, row["oof_threshold"])})
    res = pd.DataFrame(results)
    res.round(4).to_csv(OUT / "unit2_model_comparison.csv", index=False)
    print(res.round(3).to_string())

    # Select on cross-validated train PR-AUC only; the test window is scored once, never used to choose.
    best_name = max(chosen, key=lambda n: chosen[n][1]["cv_pr_auc_mean"])
    best_model, best_row = chosen[best_name]
    thr = best_row["oof_threshold"]
    test[["product_id", "date", "product_type", TARGET]].assign(
        model=best_name, probability=probs[best_name], threshold=thr).to_csv(
        OUT / "unit2_test_predictions.csv", index=False)
    cm = confusion_matrix(yte, (probs[best_name] >= thr).astype(int))
    pd.DataFrame(cm, index=["actual_no_surge", "actual_surge"],
                 columns=["pred_no_surge", "pred_surge"]).to_csv(OUT / "unit2_confusion_matrix.csv")

    cohort_rows = []
    for cohort, idx in test.groupby("product_type").groups.items():
        y, p = yte.loc[idx], probs[best_name][test.index.get_indexer(idx)]
        cohort_rows.append({"cohort": cohort, "rows": len(idx), "surge_rate": y.mean(),
                            **scores(y, p, thr)})
    pd.DataFrame(cohort_rows).round(4).to_csv(OUT / "unit2_cohort_performance.csv", index=False)
    print(pd.DataFrame(cohort_rows).round(3).to_string())

    pi = permutation_importance(best_model, Xte, yte, scoring="average_precision",
                                n_repeats=5, random_state=SEED, n_jobs=-1)
    imp = (pd.DataFrame({"feature": feats, "pr_auc_drop_mean": pi.importances_mean,
                         "pr_auc_drop_std": pi.importances_std})
           .sort_values("pr_auc_drop_mean", ascending=False))
    imp.round(5).to_csv(OUT / "unit2_feature_importance.csv", index=False)

    # Label audit: the surge cut-off is a global percentile; check how labels change with a train-only cut-off.
    master = pd.read_csv(ROOT / "processed" / "master_dataset.csv", parse_dates=["date"])
    pos = master.loc[(master["date"] < "2026-08-12") & (master["search_interest_growth"] > 0),
                     "search_interest_growth"]
    cut_train = np.nanpercentile(pos, 85)
    cut_all = np.nanpercentile(master.loc[master["search_interest_growth"] > 0, "search_interest_growth"], 85)
    relabel = (master["search_interest_growth"] >= cut_train).astype(int)
    relabel[master["search_interest_growth"].isna()] = 0
    agree = (relabel == master["surge_label"]).mean()
    pd.DataFrame([{"global_cutoff": cut_all, "train_only_cutoff": cut_train, "label_agreement": agree,
                   "labels_changed": int((relabel != master["surge_label"]).sum())}]).round(4).to_csv(
        OUT / "unit2_label_audit.csv", index=False)
    print("label audit", cut_all, cut_train, agree)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    for name, prob in probs.items():
        if name.startswith("Persistence"):
            continue
        p, r, _ = precision_recall_curve(yte, prob)
        axes[0].plot(r, p, label=f"{name} (AP={average_precision_score(yte, prob):.3f})")
    axes[0].axhline(yte.mean(), ls="--", c="grey", label=f"No-skill ({yte.mean():.3f})")
    axes[0].set(xlabel="Recall", ylabel="Precision", title="Precision-recall on held-out test (3,180 rows)")
    axes[0].legend(fontsize=8)
    top = imp.head(10).iloc[::-1]
    axes[1].barh(top["feature"], top["pr_auc_drop_mean"], xerr=top["pr_auc_drop_std"], color="#1f4e79")
    axes[1].set(xlabel="Drop in PR-AUC when permuted", title=f"Permutation importance: {best_name}")
    fig.tight_layout()
    fig.savefig(OUT / "unit2_classification.png", dpi=170)
    print("best", best_name)


if __name__ == "__main__":
    main()
