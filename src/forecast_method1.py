"""SCRUM-26: Time-Series Forecasting Model Implementation (Method 1).

Implements Time-Series Analysis (Method 1) on validated product search interest.
Trains baseline and advanced time-series forecasting models (Naive Persistence,
ARIMA, Exponential Smoothing, Ridge, ElasticNet, Random Forest, Gradient Boosting,
and LightGBM) on the held-out chronological split (processed/train.csv and test.csv).

Outputs:
  processed/method1_model_comparison.csv
  processed/method1_forecast_predictions.csv
  processed/method1_cohort_performance.csv
  processed/method1_feature_importance.csv
  docs/figures/method1_model_benchmark.png
  docs/figures/method1_forecast_trajectories.png
  docs/figures/method1_feature_importance.png
  docs/figures/method1_cohort_comparison.png

Usage:
  python src/forecast_method1.py
  python src/forecast_method1.py --no-plots
  python src/forecast_method1.py --quiet
"""

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing

import config

# Optional LightGBM import
try:
    import lightgbm as lgb  # type: ignore
    LIGHTGBM_AVAILABLE = True
except ImportError:
    lgb = None
    LIGHTGBM_AVAILABLE = False


TIMESTAMP_COL = "date"
GROUP_KEY = "product_id"
TARGET_COL = "search_interest"
PRIMARY_TARGET = "surge_label"


# -----------------------------------------------------------------------------
# Metric Calculations
# -----------------------------------------------------------------------------
def calculate_wape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Weighted Absolute Percentage Error (WAPE = sum(|y - y_hat|) / sum(y) * 100)."""
    denom = np.sum(np.abs(y_true))
    if denom == 0:
        return 0.0
    return float(np.sum(np.abs(y_true - y_pred)) / denom * 100.0)


def calculate_mda(y_true: pd.Series, y_pred: pd.Series, lag_1: pd.Series) -> float:
    """Mean Directional Accuracy (percentage of correct day-over-day sign predictions)."""
    actual_diff = y_true.values - lag_1.values
    pred_diff = y_pred.values - lag_1.values
    valid_mask = (actual_diff != 0) | (pred_diff != 0)
    if valid_mask.sum() == 0:
        return 50.0
    correct = (np.sign(actual_diff) == np.sign(pred_diff)).astype(float)
    correct[pred_diff == 0] = 0.5
    return float(np.mean(correct) * 100.0)


def evaluate_forecast(
    y_true: pd.Series,
    y_pred: pd.Series,
    lag_1: pd.Series,
    model_name: str,
    model_family: str,
) -> Dict[str, Any]:
    """Compute comprehensive regression metrics on forecast predictions."""
    y_t = y_true.values
    y_p = y_pred.values

    mae = float(mean_absolute_error(y_t, y_p))
    rmse = float(root_mean_squared_error(y_t, y_p))
    r2 = float(r2_score(y_t, y_p))
    wape = calculate_wape(y_t, y_p)
    mda = calculate_mda(y_true, y_pred, lag_1)

    return {
        "model_name": model_name,
        "model_family": model_family,
        "test_mae": round(mae, 4),
        "test_rmse": round(rmse, 4),
        "test_wape_pct": round(wape, 2),
        "test_r2": round(r2, 4),
        "test_mda_pct": round(mda, 2),
    }


# -----------------------------------------------------------------------------
# Baseline Forecasting Models
# -----------------------------------------------------------------------------
def forecast_naive_persistence(test_df: pd.DataFrame, train_df: pd.DataFrame) -> pd.Series:
    """Naive 1-day persistence: y_hat(t) = y(t-1)."""
    preds = []
    for pid in test_df[GROUP_KEY].unique():
        p_train = train_df[train_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)
        p_test = test_df[test_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)
        # First test day uses last train day
        last_train_val = p_train[TARGET_COL].iloc[-1]
        test_lag1 = p_test[TARGET_COL].shift(1).fillna(last_train_val)
        preds.extend(test_lag1.values)
    return pd.Series(preds, index=test_df.index)


def forecast_seasonal_naive(test_df: pd.DataFrame, train_df: pd.DataFrame, season: int = 7) -> pd.Series:
    """Seasonal 7-day persistence: y_hat(t) = y(t-7)."""
    preds = []
    for pid in test_df[GROUP_KEY].unique():
        p_train = train_df[train_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)
        p_test = test_df[test_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)
        train_tail = p_train[TARGET_COL].iloc[-season:].values
        combined = pd.concat([pd.Series(train_tail), p_test[TARGET_COL]], ignore_index=True)
        shifted = combined.shift(season).iloc[season:].values
        preds.extend(shifted)
    return pd.Series(preds, index=test_df.index)


def forecast_rolling_mean(test_df: pd.DataFrame, train_df: pd.DataFrame, window: int = 7) -> pd.Series:
    """7-day rolling mean persistence: y_hat(t) = mean(y(t-7) to y(t-1))."""
    preds = []
    for pid in test_df[GROUP_KEY].unique():
        p_train = train_df[train_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)
        p_test = test_df[test_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)
        train_tail = p_train[TARGET_COL].iloc[-window:].values
        combined = pd.concat([pd.Series(train_tail), p_test[TARGET_COL]], ignore_index=True)
        rolling_m = combined.shift(1).rolling(window).mean().iloc[window:].values
        preds.extend(rolling_m)
    return pd.Series(preds, index=test_df.index)


# -----------------------------------------------------------------------------
# Statistical Time-Series Models
# -----------------------------------------------------------------------------
def forecast_arima_per_product(
    test_df: pd.DataFrame,
    train_df: pd.DataFrame,
    order: Tuple[int, int, int] = (1, 1, 1),
) -> pd.Series:
    """Fit per-product ARIMA model on train and forecast over test split using 1-step updates."""
    preds_dict = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for pid in test_df[GROUP_KEY].unique():
            p_train = train_df[train_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)[TARGET_COL].values
            p_test = test_df[test_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)[TARGET_COL].values
            test_indices = test_df[test_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL).index

            try:
                # If zero-variance series (e.g. P003 unreleased), predict constant zero
                if np.var(p_train) == 0:
                    prod_preds = np.zeros(len(p_test))
                else:
                    model = ARIMA(p_train, order=order)
                    res = model.fit()
                    res_test = res.apply(p_test)
                    prod_preds = res_test.fittedvalues
                    # Clip bounds [0, 100]
                    prod_preds = np.clip(prod_preds, 0.0, 100.0)
            except Exception:
                # Fallback to persistence
                last_train = p_train[-1] if len(p_train) > 0 else 0.0
                prod_preds = np.roll(p_test, 1)
                prod_preds[0] = last_train

            for idx, pred in zip(test_indices, prod_preds):
                preds_dict[idx] = float(pred)

    return pd.Series([preds_dict[i] for i in test_df.index], index=test_df.index)


def forecast_exponential_smoothing_per_product(
    test_df: pd.DataFrame,
    train_df: pd.DataFrame,
) -> pd.Series:
    """Fit per-product Exponential Smoothing (Holt-Winters level/trend) on train series."""
    preds_dict = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for pid in test_df[GROUP_KEY].unique():
            p_train = train_df[train_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)[TARGET_COL].values
            p_test = test_df[test_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL)[TARGET_COL].values
            test_indices = test_df[test_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL).index

            try:
                if np.var(p_train) == 0:
                    prod_preds = np.zeros(len(p_test))
                else:
                    # Holt's linear trend with additive smoothing
                    model = ExponentialSmoothing(p_train, trend="add", initialization_method="estimated")
                    res = model.fit(smoothing_level=0.6, smoothing_trend=0.2)
                    res_test = res.apply(p_test)
                    prod_preds = np.clip(res_test.fittedvalues, 0.0, 100.0)
            except Exception:
                last_train = p_train[-1] if len(p_train) > 0 else 0.0
                prod_preds = np.roll(p_test, 1)
                prod_preds[0] = last_train

            for idx, pred in zip(test_indices, prod_preds):
                preds_dict[idx] = float(pred)

    return pd.Series([preds_dict[i] for i in test_df.index], index=test_df.index)


# -----------------------------------------------------------------------------
# Lagged Machine Learning Regressors
# -----------------------------------------------------------------------------
def get_method1_feature_columns(train_df: pd.DataFrame) -> List[str]:
    """Extract the validated Method 1 feature subset (lags, rolling stats, dummy indicators)."""
    feature_cols = []
    # 1. Temporal lags
    for prefix in ["search_lag_", "growth_lag_", "accel_lag_", "videos_published_lag_", "comments_published_lag_"]:
        feature_cols.extend([c for c in train_df.columns if c.startswith(prefix)])

    # 2. Rolling stats
    feature_cols.extend([c for c in train_df.columns if c.startswith("rolling_mean_") or c.startswith("rolling_std_")])

    # 3. Categorical dummies
    feature_cols.extend([c for c in train_df.columns if c.startswith("brand_") or c.startswith("product_type_")])

    return sorted(list(set(feature_cols)))


def train_and_predict_ml_models(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
) -> Tuple[Dict[str, pd.Series], Dict[str, Any]]:
    """Train regularized linear and tree-based ensemble models on panel lag features."""
    models = {
        "Ridge_Regression": Ridge(alpha=10.0, random_state=42),
        "ElasticNet": ElasticNet(alpha=0.1, l1_ratio=0.5, random_state=42),
        "Random_Forest": RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1),
        "Gradient_Boosting": GradientBoostingRegressor(n_estimators=100, max_depth=4, random_state=42),
    }

    if LIGHTGBM_AVAILABLE:
        models["LightGBM"] = lgb.LGBMRegressor(
            n_estimators=100, max_depth=4, learning_rate=0.05,
            random_state=42, verbose=-1, n_jobs=-1
        )
    else:
        models["Extra_Trees"] = ExtraTreesRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1)

    predictions = {}
    fitted_models = {}

    for name, model in models.items():
        model.fit(X_train, y_train)
        pred = model.predict(X_test)
        # Demand search interest cannot be negative
        pred_clipped = np.clip(pred, 0.0, 100.0)
        predictions[name] = pd.Series(pred_clipped, index=X_test.index)
        fitted_models[name] = model

    return predictions, fitted_models


# -----------------------------------------------------------------------------
# Cohort Analysis & Feature Importance
# -----------------------------------------------------------------------------
def compute_cohort_breakdown(
    test_df: pd.DataFrame,
    predictions: Dict[str, pd.Series],
    best_model_name: str,
) -> pd.DataFrame:
    """Evaluate performance across RISING, STABLE, and DECLINING smartphone cohorts."""
    cohorts = ["RISING", "STABLE", "DECLINING"]
    records = []

    y_true = test_df[TARGET_COL]
    best_pred = predictions[best_model_name]
    naive_pred = predictions["Naive_Persistence"]

    for cohort in cohorts:
        mask = test_df["product_type"] == cohort
        if mask.sum() == 0:
            continue

        sub_true = y_true[mask]
        sub_best = best_pred[mask]
        sub_naive = naive_pred[mask]

        mae_best = mean_absolute_error(sub_true, sub_best)
        rmse_best = root_mean_squared_error(sub_true, sub_best)
        r2_best = r2_score(sub_true, sub_best)
        wape_best = calculate_wape(sub_true.values, sub_best.values)

        mae_naive = mean_absolute_error(sub_true, sub_naive)
        rmse_naive = root_mean_squared_error(sub_true, sub_naive)
        wape_naive = calculate_wape(sub_true.values, sub_naive.values)

        mae_improvement = ((mae_naive - mae_best) / mae_naive) * 100.0

        records.append({
            "cohort": cohort,
            "sample_rows": int(mask.sum()),
            "best_model": best_model_name,
            "best_model_mae": round(mae_best, 4),
            "best_model_rmse": round(rmse_best, 4),
            "best_model_wape_pct": round(wape_best, 2),
            "best_model_r2": round(r2_best, 4),
            "naive_mae": round(mae_naive, 4),
            "naive_rmse": round(rmse_naive, 4),
            "mae_reduction_vs_naive_pct": round(mae_improvement, 2),
        })

    return pd.DataFrame(records)


def extract_feature_importances(
    fitted_models: Dict[str, Any],
    feature_names: List[str],
) -> pd.DataFrame:
    """Extract feature importances from tree models and coefficients from Ridge."""
    records = []

    gbr = fitted_models.get("Gradient_Boosting")
    rf = fitted_models.get("Random_Forest")
    ridge = fitted_models.get("Ridge_Regression")

    gbr_imp = gbr.feature_importances_ if gbr else [0.0] * len(feature_names)
    rf_imp = rf.feature_importances_ if rf else [0.0] * len(feature_names)
    ridge_coef = np.abs(ridge.coef_) if ridge else [0.0] * len(feature_names)
    # Normalize ridge coefs to sum to 1 for relative comparison
    ridge_norm = ridge_coef / (np.sum(ridge_coef) + 1e-9)

    for i, feat in enumerate(feature_names):
        records.append({
            "feature_name": feat,
            "feature_category": "temporal_lag" if "lag" in feat else ("rolling_stat" if "rolling" in feat else "categorical_dummy"),
            "gbr_importance": round(float(gbr_imp[i]), 5),
            "rf_importance": round(float(rf_imp[i]), 5),
            "ridge_abs_coef_norm": round(float(ridge_norm[i]), 5),
            "ensemble_mean_importance": round(float((gbr_imp[i] + rf_imp[i] + ridge_norm[i]) / 3.0), 5),
        })

    return pd.DataFrame(records).sort_values(by="gbr_importance", ascending=False).reset_index(drop=True)


# -----------------------------------------------------------------------------
# Visualization Artifacts
# -----------------------------------------------------------------------------
def generate_method1_plots(
    test_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    predictions: Dict[str, pd.Series],
    cohort_df: pd.DataFrame,
    feat_imp_df: pd.DataFrame,
    figures_dir: Path,
):
    """Generate high-resolution visual benchmark figures for Method 1 time-series models."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")

    # 1. Model Benchmark Comparison Bar Chart
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5), dpi=150)
    sorted_comp = comparison_df.sort_values(by="test_mae", ascending=True)

    y_pos = np.arange(len(sorted_comp))
    bars1 = ax1.barh(y_pos, sorted_comp["test_mae"], color="#3498DB", height=0.55)
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(sorted_comp["model_name"], fontsize=9.5)
    ax1.invert_yaxis()
    ax1.set_xlabel("Mean Absolute Error (MAE) — Lower is Better", fontsize=10, fontweight="bold")
    ax1.set_title("Method 1 Model Benchmark: Test MAE", fontsize=11, fontweight="bold", pad=10)

    for bar in bars1:
        w = bar.get_width()
        ax1.text(w + 0.08, bar.get_y() + bar.get_height() / 2, f"{w:.2f}",
                 va="center", fontsize=8.5, fontweight="bold")

    # R2 Comparison
    r2_colors = ["#2ECC71" if r >= 0.70 else "#F39C12" for r in sorted_comp["test_r2"]]
    bars2 = ax2.barh(y_pos, sorted_comp["test_r2"], color=r2_colors, height=0.55)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(sorted_comp["model_name"], fontsize=9.5)
    ax2.invert_yaxis()
    ax2.set_xlabel("Out-of-Sample R² Score — Higher is Better", fontsize=10, fontweight="bold")
    ax2.set_title("Method 1 Model Benchmark: Test R²", fontsize=11, fontweight="bold", pad=10)
    ax2.set_xlim(0, 0.85)

    for bar in bars2:
        w = bar.get_width()
        ax2.text(w + 0.015, bar.get_y() + bar.get_height() / 2, f"{w:.3f}",
                 va="center", fontsize=8.5, fontweight="bold")

    fig.tight_layout()
    fig.savefig(figures_dir / "method1_model_benchmark.png")
    plt.close(fig)

    # 2. Real vs Forecast Trajectories for Representative Products
    fig, axes = plt.subplots(3, 1, figsize=(13, 9), dpi=150, sharex=True)
    rep_products = [
        ("P001", "Apple iPhone 18 Pro", "RISING"),
        ("P035", "Samsung Galaxy S24", "STABLE"),
        ("P057", "Xiaomi 13 Pro", "DECLINING"),
    ]

    for ax, (pid, pname, cohort) in zip(axes, rep_products):
        sub = test_df[test_df[GROUP_KEY] == pid].sort_values(TIMESTAMP_COL).copy()
        sub["dt"] = pd.to_datetime(sub[TIMESTAMP_COL])

        ax.plot(sub["dt"], sub[TARGET_COL], color="#2C3E50", linewidth=2.2, label="Actual Search Interest")
        if "Gradient_Boosting" in predictions:
            ax.plot(sub["dt"], predictions["Gradient_Boosting"].loc[sub.index],
                    color="#E74C3C", linestyle="-", linewidth=1.8, label="Gradient Boosting Forecast")
        if "Ridge_Regression" in predictions:
            ax.plot(sub["dt"], predictions["Ridge_Regression"].loc[sub.index],
                    color="#2980B9", linestyle="--", linewidth=1.5, label="Ridge Regression Forecast")
        if "Naive_Persistence" in predictions:
            ax.plot(sub["dt"], predictions["Naive_Persistence"].loc[sub.index],
                    color="#95A5A6", linestyle=":", linewidth=1.2, label="Naive Persistence (t-1)")

        ax.set_title(f"[{cohort}] {pname} ({pid}) — 53-Day Held-Out Test Forecast Horizon",
                     fontsize=10.5, fontweight="bold", pad=8)
        ax.set_ylabel("Search Interest (0-100)", fontsize=9.5)
        ax.legend(loc="upper left", frameon=True, fontsize=8.5)
        ax.grid(True, linestyle="--", alpha=0.5)

    axes[-1].set_xlabel("Date (Held-Out Test Partition: 2026-08-12 to 2026-10-03)", fontsize=10, fontweight="bold")
    fig.tight_layout()
    fig.savefig(figures_dir / "method1_forecast_trajectories.png")
    plt.close(fig)

    # 3. Feature Importance Bar Chart
    fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
    top_feats = feat_imp_df.head(10).iloc[::-1]
    y_pos = np.arange(len(top_feats))

    ax.barh(y_pos, top_feats["gbr_importance"] * 100.0, color="#16A085", height=0.55)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(top_feats["feature_name"], fontsize=9.5)
    ax.set_xlabel("Gradient Boosting Relative Feature Importance (%)", fontsize=10, fontweight="bold")
    ax.set_title("Method 1: Top 10 Temporal Predictors for Consumer Search Demand",
                 fontsize=11, fontweight="bold", pad=12)

    for i, val in enumerate(top_feats["gbr_importance"]):
        ax.text(val * 100.0 + 0.8, i, f"{val*100.0:.1f}%", va="center", fontsize=8.5, fontweight="bold")

    ax.set_xlim(0, 75)
    fig.tight_layout()
    fig.savefig(figures_dir / "method1_feature_importance.png")
    plt.close(fig)

    # 4. Cohort Error Reduction Comparison
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    x = np.arange(len(cohort_df))
    width = 0.35

    ax.bar(x - width / 2, cohort_df["naive_mae"], width, label="Naive Persistence MAE", color="#95A5A6")
    ax.bar(x + width / 2, cohort_df["best_model_mae"], width, label="Gradient Boosting MAE", color="#E67E22")

    ax.set_ylabel("Mean Absolute Error (MAE)", fontsize=10, fontweight="bold")
    ax.set_title("Forecasting Error Reduction Across Product Cohorts (SCRUM-26)", fontsize=11, fontweight="bold", pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels(cohort_df["cohort"], fontsize=10, fontweight="bold")
    ax.legend(frameon=True)

    for i in range(len(cohort_df)):
        red = cohort_df["mae_reduction_vs_naive_pct"].iloc[i]
        ax.text(i, cohort_df["best_model_mae"].iloc[i] + 0.25, f"-{red:.1f}% Error",
                ha="center", fontsize=8.5, fontweight="bold", color="#C0392B")

    fig.tight_layout()
    fig.savefig(figures_dir / "method1_cohort_comparison.png")
    plt.close(fig)


# -----------------------------------------------------------------------------
# Main Orchestrator
# -----------------------------------------------------------------------------
def main(
    paths: Optional[config.Paths] = None,
    generate_plots: bool = True,
    quiet: bool = False,
) -> int:
    """Execute end-to-end Method 1 time-series forecasting benchmarks."""
    if paths is None:
        paths = config.PATHS

    train_path = paths.time_series_train_csv
    test_path = paths.time_series_test_csv
    figures_dir = paths.root / "docs" / "figures"

    if not train_path.exists() or not test_path.exists():
        print(f"Error: train/test splits missing at {train_path} / {test_path}. Run prep_time_series.py first.", file=sys.stderr)
        return 1

    if not quiet:
        print("=" * 80)
        print("  SCRUM-26: TIME-SERIES FORECASTING MODEL IMPLEMENTATION (METHOD 1)")
        print("=" * 80)
        print(f"Train Partition Input: {train_path}")
        print(f"Test Partition Input:  {test_path}")

    # Load chronological splits
    train_df = pd.read_csv(train_path)
    test_df = pd.read_csv(test_path)

    if not quiet:
        print(f"\n[Data Ingestion] Loaded 60-product chronological train/test partitions:")
        print(f"  Train Set: {train_df[TIMESTAMP_COL].min()} to {train_df[TIMESTAMP_COL].max()} ({len(train_df):,} rows)")
        print(f"  Test Set:  {test_df[TIMESTAMP_COL].min()} to {test_df[TIMESTAMP_COL].max()} ({len(test_df):,} rows)")

    # Extract feature matrix
    feature_cols = get_method1_feature_columns(train_df)
    X_train = train_df[feature_cols]
    y_train = train_df[TARGET_COL]
    X_test = test_df[feature_cols]
    y_test = test_df[TARGET_COL]

    # Reference lag 1 values for Directional Accuracy (MDA)
    last_train_pids = train_df.groupby(GROUP_KEY)[TARGET_COL].last().to_dict()
    lag_1_series = test_df.groupby(GROUP_KEY)[TARGET_COL].shift(1).fillna(test_df[GROUP_KEY].map(last_train_pids))

    all_predictions: Dict[str, pd.Series] = {}
    eval_records: List[Dict[str, Any]] = []

    # 1. Baseline Models
    if not quiet:
        print(f"\n[Phase 1: Baseline Forecasting Models]")

    naive_pred = forecast_naive_persistence(test_df, train_df)
    all_predictions["Naive_Persistence"] = naive_pred
    eval_records.append(evaluate_forecast(y_test, naive_pred, lag_1_series, "Naive_Persistence", "Baseline"))

    snaive_pred = forecast_seasonal_naive(test_df, train_df, season=7)
    all_predictions["Seasonal_Naive_7d"] = snaive_pred
    eval_records.append(evaluate_forecast(y_test, snaive_pred, lag_1_series, "Seasonal_Naive_7d", "Baseline"))

    roll_pred = forecast_rolling_mean(test_df, train_df, window=7)
    all_predictions["Rolling_Mean_7d"] = roll_pred
    eval_records.append(evaluate_forecast(y_test, roll_pred, lag_1_series, "Rolling_Mean_7d", "Baseline"))

    for r in eval_records:
        if not quiet:
            print(f"  {r['model_name']:<25} | MAE: {r['test_mae']:.3f} | RMSE: {r['test_rmse']:.3f} | R²: {r['test_r2']:+.4f} | WAPE: {r['test_wape_pct']:.2f}%")

    # 2. Statistical Time-Series Models
    if not quiet:
        print(f"\n[Phase 2: Statistical Time-Series Models (ARIMA & Exponential Smoothing)]")

    arima_pred = forecast_arima_per_product(test_df, train_df, order=(1, 1, 1))
    all_predictions["ARIMA(1,1,1)"] = arima_pred
    eval_arima = evaluate_forecast(y_test, arima_pred, lag_1_series, "ARIMA(1,1,1)", "Statistical")
    eval_records.append(eval_arima)
    if not quiet:
        print(f"  {eval_arima['model_name']:<25} | MAE: {eval_arima['test_mae']:.3f} | RMSE: {eval_arima['test_rmse']:.3f} | R²: {eval_arima['test_r2']:+.4f} | WAPE: {eval_arima['test_wape_pct']:.2f}%")

    ets_pred = forecast_exponential_smoothing_per_product(test_df, train_df)
    all_predictions["Exponential_Smoothing"] = ets_pred
    eval_ets = evaluate_forecast(y_test, ets_pred, lag_1_series, "Exponential_Smoothing", "Statistical")
    eval_records.append(eval_ets)
    if not quiet:
        print(f"  {eval_ets['model_name']:<25} | MAE: {eval_ets['test_mae']:.3f} | RMSE: {eval_ets['test_rmse']:.3f} | R²: {eval_ets['test_r2']:+.4f} | WAPE: {eval_ets['test_wape_pct']:.2f}%")

    # 3. Lagged Machine Learning Regressors
    if not quiet:
        print(f"\n[Phase 3: Lagged Machine Learning Regressors ({len(feature_cols)} Predictors)]")

    ml_preds, fitted_ml = train_and_predict_ml_models(X_train, y_train, X_test)
    for model_name, preds in ml_preds.items():
        all_predictions[model_name] = preds
        eval_ml = evaluate_forecast(y_test, preds, lag_1_series, model_name, "Machine_Learning")
        eval_records.append(eval_ml)
        if not quiet:
            print(f"  {eval_ml['model_name']:<25} | MAE: {eval_ml['test_mae']:.3f} | RMSE: {eval_ml['test_rmse']:.3f} | R²: {eval_ml['test_r2']:+.4f} | WAPE: {eval_ml['test_wape_pct']:.2f}%")

    # Benchmark comparison DataFrame
    comparison_df = pd.DataFrame(eval_records).sort_values(by="test_mae", ascending=True).reset_index(drop=True)
    comp_path = paths.method1_model_comparison_csv
    comparison_df.to_csv(comp_path, index=False, lineterminator="\n")

    best_model_name = comparison_df["model_name"].iloc[0]
    if not quiet:
        print(f"\n[Benchmark Summary] Best Model: {best_model_name} (MAE: {comparison_df['test_mae'].iloc[0]:.3f}, R²: {comparison_df['test_r2'].iloc[0]:.4f})")
        print(f"  Model comparisons saved -> {comp_path}")

    # 4. Out-of-sample Forecast Predictions Table
    pred_df = test_df[[TIMESTAMP_COL, GROUP_KEY, "product_name", "brand", "product_type", TARGET_COL, PRIMARY_TARGET]].copy()
    for name, s_pred in all_predictions.items():
        pred_df[f"pred_{name}"] = s_pred.values

    pred_path = paths.method1_forecast_predictions_csv
    pred_df.to_csv(pred_path, index=False, lineterminator="\n")
    if not quiet:
        print(f"\n[Predictions Export] Exported 53-day test horizon forecasts ({len(pred_df):,} rows) -> {pred_path}")

    # 5. Cohort Analysis Breakdown
    cohort_df = compute_cohort_breakdown(test_df, all_predictions, best_model_name=best_model_name)
    cohort_path = paths.method1_cohort_performance_csv
    cohort_df.to_csv(cohort_path, index=False, lineterminator="\n")
    if not quiet:
        print(f"\n[Cohort Performance Breakdown] Across Product Life-Cycle Categories:")
        for r in cohort_df.itertuples():
            print(f"  {r.cohort:<12} | Best MAE: {r.best_model_mae:.3f} | Naive MAE: {r.naive_mae:.3f} | Error Reduction: {r.mae_reduction_vs_naive_pct:.1f}%")
        print(f"  Cohort breakdown saved -> {cohort_path}")

    # 6. Feature Importance Extraction
    feat_imp_df = extract_feature_importances(fitted_ml, feature_cols)
    feat_imp_path = paths.method1_feature_importance_csv
    feat_imp_df.to_csv(feat_imp_path, index=False, lineterminator="\n")
    if not quiet:
        print(f"\n[Feature Importance] Top 5 Temporal Lags for Demand Forecasting:")
        for r in feat_imp_df.head(5).itertuples():
            print(f"  {r.feature_name:<25} | GBR: {r.gbr_importance*100:.1f}% | RF: {r.rf_importance*100:.1f}%")
        print(f"  Feature importances saved -> {feat_imp_path}")

    # 7. Diagnostic Visual Figures
    if generate_plots:
        generate_method1_plots(test_df, comparison_df, all_predictions, cohort_df, feat_imp_df, figures_dir)
        if not quiet:
            print(f"\n[Visual Artifacts] Generated high-res diagnostic plots in: {figures_dir}")

    if not quiet:
        print("\n" + "=" * 80)
        print("  SCRUM-26 EXECUTION COMPLETE: 100% SUCCESS")
        print("=" * 80)

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SCRUM-26 Time-Series Forecasting Model Implementation")
    parser.add_argument("--no-plots", action="store_true", help="Skip generating visualization plots")
    parser.add_argument("--quiet", action="store_true", help="Suppress console outputs")

    args = parser.parse_args()
    rc = main(
        generate_plots=not args.no_plots,
        quiet=args.quiet,
    )
    sys.exit(rc)
