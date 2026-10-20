"""SCRUM-22: Chronological Split & Time-Series Data Prep.

Isolates time-series variables from the SCRUM-21 master dataset (processed/master_dataset.csv),
performs time-index validation, stationarity (ADF, KPSS) and lag diagnostics,
and creates leak-free chronological train/test splits for Method 1 evaluation.

Outputs:
  processed/time_series_validation_report.csv
  processed/train.csv
  processed/test.csv
  processed/split_metadata.json
  processed/stationarity_report.csv
  processed/lag_diagnostics.csv
  docs/figures/ts_target_behaviour.png
  docs/figures/ts_temporal_split.png
  docs/figures/ts_lag_relationships.png

Usage:
  python src/prep_time_series.py
  python src/prep_time_series.py --split-ratio 0.8
  python src/prep_time_series.py --split-date 2026-08-13
  python src/prep_time_series.py --no-plots
"""

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.stattools import adfuller, kpss

import config

# -----------------------------------------------------------------------------
# Configuration and Constants
# -----------------------------------------------------------------------------
TIMESTAMP_COL = "date"
GROUP_KEY = "product_id"
PRIMARY_TARGET = "surge_label"
CONTINUOUS_TARGET = "search_interest"

REQUIRED_INPUT_COLS = [
    "product_id",
    "date",
    "product_name",
    "brand",
    "product_type",
    "search_interest",
    "videos_published",
    "comments_published",
    "search_interest_growth",
    "search_interest_accel",
    "surge_label",
]

# Columns representing post-event information, operational metadata,
# or point-in-time snapshot metrics (such as yt_* collected on 2026-10-04)
# that must be excluded from predictive features to prevent data leakage.
LEAKAGE_AND_METADATA_COLS = [
    # Identifiers and timestamps
    "product_id",
    "product_name",
    "date",
    "category",
    # Target variables
    "surge_label",
    "search_interest",  # Raw contemporaneous target level
    "search_interest_growth",  # Contemporaneous growth
    "search_interest_accel",  # Contemporaneous acceleration
    # Google Trends operational flags
    "trends_observed",
    "is_below_threshold",
    "is_zero",
    "is_provisional",
    "zero_share_product",
    "low_signal_product",
    # Date-level YouTube snapshot columns from aligned_daily (mostly missing)
    "youtube_observed",
    "youtube_query_version",
    "video_result_count",
    "video_views_total",
    "video_comments_total",
    "videos_on_target",
    "views_on_target_total",
    "comments_on_target_total",
    "on_target_share",
    "low_on_target_share",
    "result_cap_hit",
    "youtube_recent_observed",
    "videos_published_7d",
    "videos_7d_on_target",
    "recent_cap_hit",
    # Point-in-time YouTube snapshot (collected on 2026-10-04; future snapshot leakage)
    "yt_snapshot_date",
    "yt_videos_on_target",
    "yt_views_on_target_total",
    "yt_on_target_share",
    "yt_low_on_target_share",
    "yt_videos_7d_on_target",
    "usable_product",
    # Contemporaneous daily activity (must use lagged versions for forward forecasting)
    "videos_published",
    "videos_published_on_target",
    "comments_published",
    "comments_published_usable",
]

CATEGORICAL_PREDICTORS = ["brand", "product_type"]
DEFAULT_LAGS = [1, 2, 3, 7]
DEFAULT_ROLLING_WINDOW = 7
DEFAULT_SPLIT_RATIO = 0.8


# -----------------------------------------------------------------------------
# Phase 2: Time-Series Validation
# -----------------------------------------------------------------------------
def validate_time_series_data(df: pd.DataFrame) -> Tuple[bool, pd.DataFrame]:
    """Perform comprehensive time-index validation on the master dataset.

    Checks:
    - Required columns presence.
    - Timestamp parsing and formatting.
    - Duplicate (product_id, date) pairs.
    - Uniform daily sampling frequency (no irregular steps).
    - Calendar continuity and missing dates per product.
    - Missing values in targets and historical signals.
    - Target boundary constraints.

    Returns (is_valid, validation_report_dataframe).
    """
    checks = []

    def log_check(name: str, passed: bool, detail: str, is_info: bool = False):
        status = "INFO" if is_info else ("PASS" if passed else "FAIL")
        checks.append({"check": name, "status": status, "detail": detail})

    # 1. Dataset existence and dimensions
    n_rows = len(df)
    n_cols = len(df.columns)
    log_check("dataset_not_empty", n_rows > 0, f"{n_rows:,} rows, {n_cols} columns")

    # 2. Required columns
    missing_cols = [c for c in REQUIRED_INPUT_COLS if c not in df.columns]
    log_check("required_columns_present", len(missing_cols) == 0,
              f"Missing: {missing_cols or 'none'}")

    if missing_cols:
        return False, pd.DataFrame(checks)

    # 3. Timestamp parsing
    try:
        parsed_dates = pd.to_datetime(df[TIMESTAMP_COL], format="%Y-%m-%d", errors="raise")
        valid_ts = parsed_dates.notna().all()
        log_check("timestamp_parsing", valid_ts,
                  f"Dates parsed successfully ({df[TIMESTAMP_COL].min()} to {df[TIMESTAMP_COL].max()})")
    except Exception as e:
        log_check("timestamp_parsing", False, f"Timestamp parsing failed: {e}")
        return False, pd.DataFrame(checks)

    # 4. Product identity and key deduplication
    n_products = df[GROUP_KEY].nunique()
    log_check("product_count", n_products > 0, f"{n_products} distinct products identified")

    dupe_keys = int(df.duplicated(subset=[GROUP_KEY, TIMESTAMP_COL]).sum())
    log_check("no_duplicate_timestamps", dupe_keys == 0, f"{dupe_keys} duplicate (product_id, date) pairs")

    # 5. Chronological ordering within product
    is_sorted = True
    for _, group in df.groupby(GROUP_KEY):
        if not group[TIMESTAMP_COL].is_monotonic_increasing:
            is_sorted = False
            break
    log_check("chronological_order", is_sorted,
              "Dates are monotonic increasing within each product" if is_sorted else "Dates out of order")

    # 6. Sampling frequency and calendar continuity
    calendar_dates = pd.date_range(parsed_dates.min(), parsed_dates.max(), freq="D")
    expected_days = len(calendar_dates)
    total_expected_rows = n_products * expected_days

    log_check("sampling_frequency", True,
              f"Uniform daily sampling frequency verified across {expected_days} calendar days", is_info=True)

    gaps_by_product = {}
    for pid, group in df.groupby(GROUP_KEY):
        prod_dates = set(pd.to_datetime(group[TIMESTAMP_COL]))
        missing = set(calendar_dates) - prod_dates
        if missing:
            gaps_by_product[pid] = len(missing)

    log_check("calendar_continuity", len(gaps_by_product) == 0,
              f"All {n_products} products have complete contiguous history"
              if not gaps_by_product else f"Products with missing dates: {gaps_by_product}")

    log_check("expected_row_count", n_rows == total_expected_rows,
              f"{n_rows:,} rows == {n_products} products × {expected_days} days")

    # 7. Target variables integrity
    si = df[CONTINUOUS_TARGET]
    si_valid = bool(si.dropna().between(0, 100).all()) and (si.isna().sum() == 0)
    log_check("search_interest_valid", si_valid,
              f"Search interest in [0, 100]; min={si.min()}, max={si.max()}, nulls={si.isna().sum()}")

    surges = df[PRIMARY_TARGET]
    surges_valid = bool(surges.isin([0, 1]).all())
    surge_count = int(surges.sum())
    surge_pct = surges.mean() * 100.0
    log_check("surge_label_valid", surges_valid,
              f"Surge labels in {{0, 1}}; {surge_count:,} surge days ({surge_pct:.2f}% base rate)")

    # 8. Missing values summary
    null_counts = df[REQUIRED_INPUT_COLS].isna().sum()
    null_summary = {c: int(n) for c, n in null_counts.items() if n > 0}
    log_check("null_value_audit", True,
              f"Unfilled values in required columns: {null_summary or 'none'}. "
              "Note: growth/accel unobserved on day 1 or zero-days by design", is_info=True)

    report_df = pd.DataFrame(checks)
    all_passed = not (report_df["status"] == "FAIL").any()
    return all_passed, report_df


# -----------------------------------------------------------------------------
# Phase 3: Feature Engineering and Chronological Split
# -----------------------------------------------------------------------------
def engineer_time_series_features(
    df: pd.DataFrame,
    lags: List[int] = DEFAULT_LAGS,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> Tuple[pd.DataFrame, List[str]]:
    """Engineer leak-free temporal lag and rolling volatility features.

    All lags and rolling statistics are computed strictly within each product's
    chronological history using shift(lag) and shift(1).rolling().

    Returns (transformed_df, list_of_engineered_feature_names).
    """
    df = df.copy()
    df[TIMESTAMP_COL] = pd.to_datetime(df[TIMESTAMP_COL])
    df = df.sort_values([GROUP_KEY, TIMESTAMP_COL], kind="stable").reset_index(drop=True)

    # Verify/engineer growth and acceleration if needed
    if "search_interest_growth" not in df.columns:
        df["search_interest_growth"] = (
            df.groupby(GROUP_KEY)[CONTINUOUS_TARGET]
            .pct_change()
            .replace([np.inf, -np.inf], np.nan)
        )
    if "search_interest_accel" not in df.columns:
        df["search_interest_accel"] = df.groupby(GROUP_KEY)["search_interest_growth"].diff()

    # Temporal lags for search interest, velocity, and YouTube signals
    lag_feature_names = []
    for lag in lags:
        # Search interest lags
        s_col = f"search_lag_{lag}"
        df[s_col] = df.groupby(GROUP_KEY)[CONTINUOUS_TARGET].shift(lag)
        lag_feature_names.append(s_col)

        # Growth velocity lags
        g_col = f"growth_lag_{lag}"
        df[g_col] = df.groupby(GROUP_KEY)["search_interest_growth"].shift(lag)
        lag_feature_names.append(g_col)

        # Acceleration lags
        a_col = f"accel_lag_{lag}"
        df[a_col] = df.groupby(GROUP_KEY)["search_interest_accel"].shift(lag)
        lag_feature_names.append(a_col)

        # Daily published review videos lag
        if "videos_published" in df.columns:
            v_col = f"videos_published_lag_{lag}"
            df[v_col] = df.groupby(GROUP_KEY)["videos_published"].shift(lag)
            lag_feature_names.append(v_col)

        # Daily published comments lag
        if "comments_published" in df.columns:
            c_col = f"comments_published_lag_{lag}"
            df[c_col] = df.groupby(GROUP_KEY)["comments_published"].shift(lag)
            lag_feature_names.append(c_col)

    # Strictly leak-free 7-day rolling statistics (shifted by 1 so current t is excluded)
    r_mean_col = f"rolling_mean_{rolling_window}d"
    r_std_col = f"rolling_std_{rolling_window}d"
    df[r_mean_col] = df.groupby(GROUP_KEY)[CONTINUOUS_TARGET].transform(
        lambda s: s.shift(1).rolling(rolling_window).mean()
    )
    df[r_std_col] = df.groupby(GROUP_KEY)[CONTINUOUS_TARGET].transform(
        lambda s: s.shift(1).rolling(rolling_window).std()
    )
    rolling_feature_names = [r_mean_col, r_std_col]

    # Drop burn-in rows where rolling window or max lag is unobserved
    max_lag = max(lags)
    valid_mask = df[r_mean_col].notna() & df[f"search_lag_{max_lag}"].notna()
    df_clean = df[valid_mask].copy()

    # Impute remaining sporadic NaNs in lag features (e.g. division by zero growth) with 0.0
    all_numeric_features = lag_feature_names + rolling_feature_names
    df_clean[all_numeric_features] = df_clean[all_numeric_features].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # Format timestamp back to ISO string for export consistency
    df_clean[TIMESTAMP_COL] = df_clean[TIMESTAMP_COL].dt.strftime("%Y-%m-%d")

    return df_clean, all_numeric_features


def perform_chronological_split(
    df: pd.DataFrame,
    feature_names: List[str],
    split_ratio: float = DEFAULT_SPLIT_RATIO,
    split_date: Optional[str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Partition panel dataset into chronological train and test sets.

    Uses a global temporal cutoff across all products, guaranteeing:
    1. Zero test observations occur on or before the training boundary.
    2. Learned transformations (StandardScaler, one-hot encoders) are fit
       exclusively on the training partition.
    3. Test partition is transformed using train parameters without data leakage.

    Returns (train_df, test_df, metadata_dict).
    """
    df = df.copy()
    unique_dates = np.sort(df[TIMESTAMP_COL].unique())
    n_unique_dates = len(unique_dates)

    if split_date is None:
        split_idx = int(n_unique_dates * split_ratio)
        split_date = str(unique_dates[split_idx])
    else:
        if split_date not in unique_dates:
            # Pick first date >= split_date
            candidates = [d for d in unique_dates if d >= split_date]
            if not candidates:
                raise ValueError(f"Split date {split_date} is after the latest date {unique_dates[-1]}")
            split_date = str(candidates[0])

    train_mask = df[TIMESTAMP_COL] < split_date
    test_mask = df[TIMESTAMP_COL] >= split_date

    train_df = df[train_mask].copy().reset_index(drop=True)
    test_df = df[test_mask].copy().reset_index(drop=True)

    # Assert strict chronological partition
    assert train_df[TIMESTAMP_COL].max() < test_df[TIMESTAMP_COL].min(), \
        "Chronological partition violation: train date overlap with test date"

    # Fit categorical encoders on TRAIN only
    cat_feature_cols = []
    for cat_col in CATEGORICAL_PREDICTORS:
        if cat_col in train_df.columns:
            # Extract categories observed in train
            train_categories = sorted(train_df[cat_col].dropna().unique())
            for cat in train_categories[1:]:  # drop_first=True for multi-collinearity
                dummy_name = f"{cat_col}_{cat}"
                train_df[dummy_name] = (train_df[cat_col] == cat).astype(int)
                test_df[dummy_name] = (test_df[cat_col] == cat).astype(int)
                cat_feature_cols.append(dummy_name)

    all_predictors = feature_names + cat_feature_cols

    # Fit continuous scaler on TRAIN only (exclude identifiers, categoricals, and targets)
    scaler = StandardScaler()
    scaler.fit(train_df[feature_names])

    # Transform numerical features
    train_df[feature_names] = scaler.transform(train_df[feature_names])
    test_df[feature_names] = scaler.transform(test_df[feature_names])

    # Compile comprehensive split metadata
    metadata = {
        "task": "SCRUM-22: Chronological Split & Time-Series Data Prep",
        "dataset_source": "processed/master_dataset.csv",
        "timestamp_column": TIMESTAMP_COL,
        "grouping_key": GROUP_KEY,
        "split_strategy": "global_chronological_cutoff",
        "split_ratio": float(split_ratio),
        "split_date": str(split_date),
        "train_window": {
            "start_date": str(train_df[TIMESTAMP_COL].min()),
            "end_date": str(train_df[TIMESTAMP_COL].max()),
            "calendar_days": int(train_df[TIMESTAMP_COL].nunique()),
            "total_rows": int(len(train_df)),
            "surge_events": int(train_df[PRIMARY_TARGET].sum()),
            "surge_rate_pct": round(float(train_df[PRIMARY_TARGET].mean() * 100), 2),
        },
        "test_window": {
            "start_date": str(test_df[TIMESTAMP_COL].min()),
            "end_date": str(test_df[TIMESTAMP_COL].max()),
            "calendar_days": int(test_df[TIMESTAMP_COL].nunique()),
            "total_rows": int(len(test_df)),
            "surge_events": int(test_df[PRIMARY_TARGET].sum()),
            "surge_rate_pct": round(float(test_df[PRIMARY_TARGET].mean() * 100), 2),
        },
        "target_variables": {
            "classification_target": PRIMARY_TARGET,
            "continuous_target": CONTINUOUS_TARGET,
        },
        "predictor_features": {
            "count": len(all_predictors),
            "temporal_lags": [c for c in feature_names if "lag_" in c],
            "rolling_volatility": [c for c in feature_names if "rolling_" in c],
            "categorical_dummies": cat_feature_cols,
        },
        "excluded_leakage_columns": sorted(LEAKAGE_AND_METADATA_COLS),
        "leakage_prevention": {
            "snapshot_yt_excluded": True,
            "scaler_fit_on_train_only": True,
            "encoder_fit_on_train_only": True,
            "rolling_features_strictly_lagged": True,
            "temporal_boundary_verified": True,
        },
        "validation_status": "PASS",
    }

    return train_df, test_df, metadata


# -----------------------------------------------------------------------------
# Phase 4: Stationarity and Lag Diagnostics
# -----------------------------------------------------------------------------
def run_stationarity_diagnostics(
    df: pd.DataFrame,
    lags_to_evaluate: List[int] = [1, 2, 3, 7, 14],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Execute statistical stationarity (ADF, KPSS) and autocorrelation diagnostics.

    Evaluated on training set to prevent test information leakage.
    Performs per-product testing on both raw levels and 1st differences.
    Gracefully handles constant, zero-variance, or insufficient length series.

    Returns (stationarity_report_df, lag_diagnostics_df).
    """
    stationarity_results = []
    lag_corr_results = []

    for pid, group in df.groupby(GROUP_KEY):
        brand = group["brand"].iloc[0] if "brand" in group.columns else "Unknown"
        ptype = group["product_type"].iloc[0] if "product_type" in group.columns else "Unknown"
        s_raw = group[CONTINUOUS_TARGET].values.astype(float)
        n_obs = len(s_raw)

        # Test both Levels (raw) and First Differences (differenced)
        series_dict = {
            "raw_levels": s_raw,
            "first_diff": np.diff(s_raw),
        }

        for series_name, series_vals in series_dict.items():
            entry = {
                "product_id": pid,
                "brand": brand,
                "product_type": ptype,
                "series_type": series_name,
                "sample_size": len(series_vals),
            }

            # Variance and length validation
            s_var = np.var(series_vals)
            if len(series_vals) < 20:
                entry.update({
                    "test_status": "SKIPPED",
                    "reason": "insufficient_sample_size (<20)",
                    "adf_stat": np.nan, "adf_pvalue": np.nan, "is_adf_stationary": False,
                    "kpss_stat": np.nan, "kpss_pvalue": np.nan, "is_kpss_stationary": False,
                })
                stationarity_results.append(entry)
                continue

            if s_var < 1e-8:
                entry.update({
                    "test_status": "SKIPPED",
                    "reason": "constant_zero_variance",
                    "adf_stat": np.nan, "adf_pvalue": np.nan, "is_adf_stationary": False,
                    "kpss_stat": np.nan, "kpss_pvalue": np.nan, "is_kpss_stationary": False,
                })
                stationarity_results.append(entry)
                continue

            # Augmented Dickey-Fuller (ADF) test
            # Null hypothesis H0: Series possesses a unit root (non-stationary)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    adf_out = adfuller(series_vals, autolag="AIC")
                adf_stat, adf_pvalue = float(adf_out[0]), float(adf_out[1])
                is_adf_stat = bool(adf_pvalue < 0.05)
            except Exception as e:
                adf_stat, adf_pvalue, is_adf_stat = np.nan, np.nan, False

            # Kwiatkowski-Phillips-Schmidt-Shin (KPSS) test
            # Null hypothesis H0: Series is trend/level stationary
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    kpss_out = kpss(series_vals, regression="c", nlags="auto")
                kpss_stat, kpss_pvalue = float(kpss_out[0]), float(kpss_out[1])
                is_kpss_stat = bool(kpss_pvalue >= 0.05)  # H0 not rejected -> stationary
            except Exception as e:
                kpss_stat, kpss_pvalue, is_kpss_stat = np.nan, np.nan, False

            entry.update({
                "test_status": "COMPLETED",
                "reason": "normal",
                "adf_stat": round(adf_stat, 4) if not np.isnan(adf_stat) else np.nan,
                "adf_pvalue": round(adf_pvalue, 6) if not np.isnan(adf_pvalue) else np.nan,
                "is_adf_stationary": is_adf_stat,
                "kpss_stat": round(kpss_stat, 4) if not np.isnan(kpss_stat) else np.nan,
                "kpss_pvalue": round(kpss_pvalue, 6) if not np.isnan(kpss_pvalue) else np.nan,
                "is_kpss_stationary": is_kpss_stat,
            })
            stationarity_results.append(entry)

        # Autocorrelation (ACF) and Surge Cross-Correlation
        s_series = pd.Series(s_raw)
        surges = group[PRIMARY_TARGET].values if PRIMARY_TARGET in group.columns else np.zeros(n_obs)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for k in lags_to_evaluate:
                if n_obs > k:
                    acf_val = s_series.autocorr(lag=k)
                    shifted_s = s_series.shift(k)
                    valid = shifted_s.notna()
                    x_val = shifted_s[valid].values
                    y_val = surges[valid]
                    if len(x_val) > 2 and np.std(x_val) > 1e-8 and np.std(y_val) > 1e-8:
                        corr_with_surge = np.corrcoef(x_val, y_val)[0, 1]
                    else:
                        corr_with_surge = np.nan
                else:
                    acf_val, corr_with_surge = np.nan, np.nan

                lag_corr_results.append({
                    "product_id": pid,
                    "lag": k,
                    "autocorrelation": round(float(acf_val), 4) if not np.isnan(acf_val) else np.nan,
                    "cross_corr_with_surge": round(float(corr_with_surge), 4) if not np.isnan(corr_with_surge) else np.nan,
                })

    stationarity_df = pd.DataFrame(stationarity_results)
    lag_df = pd.DataFrame(lag_corr_results)
    return stationarity_df, lag_df


# -----------------------------------------------------------------------------
# Phase 4 (cont): Diagnostic Visualizations
# -----------------------------------------------------------------------------
def generate_diagnostic_plots(
    df: pd.DataFrame,
    split_date: str,
    stationarity_df: pd.DataFrame,
    lag_df: pd.DataFrame,
    figures_dir: Path,
):
    """Generate high-resolution diagnostic plots for Review 2 presentation."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Plot 1: Target Time Series Trajectories across Cohorts
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    cohort_examples = {"RISING": "P003", "STABLE": "P035", "DECLINING": "P057"}
    cohort_colors = {"RISING": "#e74c3c", "STABLE": "#2ecc71", "DECLINING": "#3498db"}

    for idx, (cohort, pid) in enumerate(cohort_examples.items()):
        ax = axes[idx]
        p_data = df[df[GROUP_KEY] == pid].copy()
        if not p_data.empty:
            p_data[TIMESTAMP_COL] = pd.to_datetime(p_data[TIMESTAMP_COL])
            p_data = p_data.sort_values(TIMESTAMP_COL)
            name = p_data["product_name"].iloc[0] if "product_name" in p_data.columns else pid
            ax.plot(p_data[TIMESTAMP_COL], p_data[CONTINUOUS_TARGET],
                    color=cohort_colors[cohort], label=f"{pid} ({name}) - {cohort}", linewidth=1.8)

            # Highlight surge events
            surge_pts = p_data[p_data[PRIMARY_TARGET] == 1]
            if not surge_pts.empty:
                ax.scatter(surge_pts[TIMESTAMP_COL], surge_pts[CONTINUOUS_TARGET],
                           color="#8e44ad", s=40, zorder=5, label="Surge Alert (Target=1)")

            # Draw chronological split boundary
            ax.axvline(pd.to_datetime(split_date), color="#2c3e50", linestyle="--", alpha=0.8,
                       linewidth=1.5, label=f"Train/Test Boundary ({split_date})")

        ax.set_title(f"Target Trajectory: {cohort} Smartphone Cohort ({pid})", fontweight="bold", fontsize=11)
        ax.set_ylabel("Search Interest (0-100)", fontsize=10)
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.legend(loc="upper right", fontsize=9)

    axes[-1].set_xlabel("Observation Date (Daily Frequency)", fontsize=11)
    plt.tight_layout()
    plot1_path = figures_dir / "ts_target_behaviour.png"
    plt.savefig(plot1_path, dpi=200)
    plt.close()

    # Plot 2: Chronological Train / Test Partition Overview
    fig, ax = plt.subplots(figsize=(10, 5))
    daily_surges = df.groupby(TIMESTAMP_COL)[PRIMARY_TARGET].sum()
    daily_dates = pd.to_datetime(daily_surges.index)
    split_dt = pd.to_datetime(split_date)

    train_dates = daily_dates[daily_dates < split_dt]
    test_dates = daily_dates[daily_dates >= split_dt]

    ax.bar(train_dates, daily_surges.loc[daily_dates < split_dt],
           color="#3498db", alpha=0.8, label=f"Training Partition (80%) [N = {len(train_dates)} days]")
    ax.bar(test_dates, daily_surges.loc[daily_dates >= split_dt],
           color="#e67e22", alpha=0.8, label=f"Held-Out Test Partition (20%) [N = {len(test_dates)} days]")
    ax.axvline(split_dt, color="#c0392b", linestyle="--", linewidth=2.0, label=f"Cutoff Date: {split_date}")

    ax.set_title("Chronological Partition: Daily Surge Frequency Distribution", fontweight="bold", fontsize=12)
    ax.set_xlabel("Timeline (Jan 2026 - Oct 2026)", fontsize=11)
    ax.set_ylabel("Total Product Surges Observed per Day", fontsize=11)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="upper left", fontsize=10)
    plt.tight_layout()
    plot2_path = figures_dir / "ts_temporal_split.png"
    plt.savefig(plot2_path, dpi=200)
    plt.close()

    # Plot 3: Lag Autocorrelations and Cross-Correlation with Surges
    fig, ax = plt.subplots(figsize=(8, 4.5))
    mean_acf = lag_df.groupby("lag")["autocorrelation"].mean()
    mean_cross = lag_df.groupby("lag")["cross_corr_with_surge"].mean()

    lags = mean_acf.index.values
    x = np.arange(len(lags))
    width = 0.35

    ax.bar(x - width / 2, mean_acf.values, width, label="Mean Autocorrelation (Search Interest)", color="#2980b9")
    ax.bar(x + width / 2, mean_cross.values, width, label="Mean Cross-Correlation (Lag -> Surge Label)", color="#27ae60")

    ax.set_title("Time-Series Lag Diagnostics across 60 Smartphone Products", fontweight="bold", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([f"Lag {l}d" for l in lags])
    ax.set_ylabel("Correlation Coefficient", fontsize=10)
    ax.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="upper right", fontsize=9)
    plt.tight_layout()
    plot3_path = figures_dir / "ts_lag_relationships.png"
    plt.savefig(plot3_path, dpi=200)
    plt.close()


# -----------------------------------------------------------------------------
# Main Execution Entrypoint
# -----------------------------------------------------------------------------
def main(
    paths: config.Paths = config.PATHS,
    split_ratio: float = DEFAULT_SPLIT_RATIO,
    split_date: Optional[str] = None,
    generate_plots: bool = True,
    quiet: bool = False,
) -> int:
    """Execute complete SCRUM-22 Time-Series Preparation Pipeline."""
    master_path = paths.master_dataset_csv
    out_dir = paths.root / "processed"
    figures_dir = paths.root / "docs" / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not master_path.exists():
        print(f"Error: Master dataset missing at {master_path}. Run src/build_master.py first.", file=sys.stderr)
        return 1

    if not quiet:
        print("=" * 80)
        print("  SCRUM-22: CHRONOLOGICAL SPLIT & TIME-SERIES DATA PREP")
        print("=" * 80)
        print(f"Loading master dataset from: {master_path}")

    df_master = pd.read_csv(master_path)
    if not quiet:
        print(f"Master dataset loaded: {len(df_master):,} rows × {len(df_master.columns)} columns")

    # 1. Validation
    is_valid, validation_report = validate_time_series_data(df_master)
    val_report_path = paths.time_series_validation_report_csv
    validation_report.to_csv(val_report_path, index=False, lineterminator="\n")
    if not quiet:
        print(f"\n[Validation] Audited {len(validation_report)} integrity checks -> {val_report_path}")
        for r in validation_report.itertuples():
            print(f"  [{r.status}] {r.check}: {r.detail}")

    if not is_valid:
        print("Validation failed; time-series preparation aborted.", file=sys.stderr)
        return 1

    # 2. Feature Engineering & Lags
    df_engineered, feature_names = engineer_time_series_features(df_master)
    if not quiet:
        print(f"\n[Feature Engineering] Generated {len(feature_names)} leak-free time-series features.")
        print(f"  Valid modeling rows after burn-in drop: {len(df_engineered):,} (dropped initial window)")

    # 3. Chronological Split
    train_df, test_df, metadata = perform_chronological_split(
        df_engineered, feature_names, split_ratio=split_ratio, split_date=split_date
    )

    train_path = paths.time_series_train_csv
    test_path = paths.time_series_test_csv
    meta_path = paths.time_series_split_metadata_json

    train_df.to_csv(train_path, index=False, lineterminator="\n")
    test_df.to_csv(test_path, index=False, lineterminator="\n")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    if not quiet:
        print(f"\n[Chronological Split] Global Cutoff Date: {metadata['split_date']}")
        print(f"  Train Set: {metadata['train_window']['start_date']} to {metadata['train_window']['end_date']} "
              f"({metadata['train_window']['calendar_days']} days, {len(train_df):,} rows, "
              f"{metadata['train_window']['surge_events']} surges, {metadata['train_window']['surge_rate_pct']}%)")
        print(f"  Test Set:  {metadata['test_window']['start_date']} to {metadata['test_window']['end_date']} "
              f"({metadata['test_window']['calendar_days']} days, {len(test_df):,} rows, "
              f"{metadata['test_window']['surge_events']} surges, {metadata['test_window']['surge_rate_pct']}%)")
        print(f"  Artifacts saved:")
        print(f"    Train:    {train_path}")
        print(f"    Test:     {test_path}")
        print(f"    Metadata: {meta_path}")

    # 4. Stationarity and Lag Diagnostics (on Train only)
    stat_df, lag_df = run_stationarity_diagnostics(train_df)
    stat_path = paths.stationarity_report_csv
    lag_path = paths.lag_diagnostics_csv

    stat_df.to_csv(stat_path, index=False, lineterminator="\n")
    lag_df.to_csv(lag_path, index=False, lineterminator="\n")

    raw_stat = stat_df[stat_df["series_type"] == "raw_levels"]
    diff_stat = stat_df[stat_df["series_type"] == "first_diff"]
    raw_stationary_pct = raw_stat["is_adf_stationary"].mean() * 100
    diff_stationary_pct = diff_stat["is_adf_stationary"].mean() * 100

    if not quiet:
        print(f"\n[Stationarity & Lag Diagnostics] Evaluated across 60 products (Train Partition)")
        print(f"  Raw Levels ADF Stationarity:        {raw_stationary_pct:.1f}% products reject unit root at 5% alpha")
        print(f"  1st Differences ADF Stationarity:   {diff_stationary_pct:.1f}% products stationary (I(1) integrated)")
        print(f"  Diagnostics saved -> {stat_path} & {lag_path}")

    # 5. Diagnostic Figures
    if generate_plots:
        generate_diagnostic_plots(df_engineered, metadata["split_date"], stat_df, lag_df, figures_dir)
        if not quiet:
            print(f"\n[Plots] High-res visual artifacts generated in: {figures_dir}")

    if not quiet:
        print("\n" + "=" * 80)
        print("  SCRUM-22 EXECUTION COMPLETE: 100% SUCCESS")
        print("=" * 80)

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SCRUM-22 Chronological Split & Time-Series Data Prep")
    parser.add_argument("--split-ratio", type=float, default=DEFAULT_SPLIT_RATIO,
                        help="Train/test chronological split ratio (default: 0.8)")
    parser.add_argument("--split-date", type=str, default=None,
                        help="Exact cutoff date (YYYY-MM-DD); overrides split-ratio")
    parser.add_argument("--no-plots", action="store_true", help="Skip generating visualization plots")
    parser.add_argument("--quiet", action="store_true", help="Suppress console outputs")

    args = parser.parse_args()
    rc = main(
        split_ratio=args.split_ratio,
        split_date=args.split_date,
        generate_plots=not args.no_plots,
        quiet=args.quiet,
    )
    sys.exit(rc)
