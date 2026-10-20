"""Tests for SCRUM-22: Chronological Split & Time-Series Data Prep (src/prep_time_series.py)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config
import prep_time_series as pts


@pytest.fixture
def synthetic_panel_df() -> pd.DataFrame:
    """Creates a small, deterministic synthetic panel dataset for testing."""
    dates = pd.date_range("2026-01-01", periods=60, freq="D").strftime("%Y-%m-%d").tolist()
    products = [
        ("P001", "Phone Alpha", "BrandA", "RISING"),
        ("P002", "Phone Beta", "BrandB", "STABLE"),
        ("P003", "Phone Gamma", "BrandA", "DECLINING"),
    ]
    rows = []
    np.random.seed(42)

    for pid, pname, brand, ptype in products:
        base_val = 20.0 if ptype == "STABLE" else (5.0 if ptype == "RISING" else 40.0)
        curr = base_val
        for i, dt in enumerate(dates):
            # AR(1) process with drift
            curr = max(0.0, min(100.0, 0.7 * curr + 0.3 * base_val + np.random.normal(0, 3)))
            growth = 0.0 if i == 0 else (curr - base_val) / max(base_val, 1.0)
            accel = 0.0 if i < 2 else growth * 0.1
            surge = 1 if growth > 0.5 else 0
            rows.append({
                "product_id": pid,
                "date": dt,
                "product_name": pname,
                "brand": brand,
                "category": "Smartphones",
                "product_type": ptype,
                "search_interest": round(curr, 2),
                "videos_published": int(np.random.poisson(1)),
                "comments_published": int(np.random.poisson(5)),
                "search_interest_growth": round(growth, 4),
                "search_interest_accel": round(accel, 4),
                "surge_label": surge,
                "yt_snapshot_date": "2026-10-04",
                "yt_videos_on_target": 10,
                "yt_views_on_target_total": 500,
            })
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Validation Tests
# -----------------------------------------------------------------------------
def test_validation_passes_on_clean_panel(synthetic_panel_df):
    is_valid, report = pts.validate_time_series_data(synthetic_panel_df)
    assert is_valid is True
    assert not (report["status"] == "FAIL").any()


def test_validation_detects_duplicate_timestamps(synthetic_panel_df):
    dupe_df = pd.concat([synthetic_panel_df, synthetic_panel_df.iloc[[0]]], ignore_index=True)
    is_valid, report = pts.validate_time_series_data(dupe_df)
    assert is_valid is False
    dupe_check = report[report["check"] == "no_duplicate_timestamps"].iloc[0]
    assert dupe_check["status"] == "FAIL"


def test_validation_detects_missing_dates(synthetic_panel_df):
    # Drop one date for product P001
    gapped_df = synthetic_panel_df[~((synthetic_panel_df["product_id"] == "P001") &
                                     (synthetic_panel_df["date"] == "2026-01-15"))].copy()
    is_valid, report = pts.validate_time_series_data(gapped_df)
    assert is_valid is False
    cal_check = report[report["check"] == "calendar_continuity"].iloc[0]
    assert cal_check["status"] == "FAIL"


def test_validation_detects_missing_required_columns(synthetic_panel_df):
    broken_df = synthetic_panel_df.drop(columns=["search_interest"])
    is_valid, report = pts.validate_time_series_data(broken_df)
    assert is_valid is False
    req_check = report[report["check"] == "required_columns_present"].iloc[0]
    assert req_check["status"] == "FAIL"


def test_validation_detects_invalid_search_interest(synthetic_panel_df):
    bad_val_df = synthetic_panel_df.copy()
    bad_val_df.loc[0, "search_interest"] = 150.0  # Above 100
    is_valid, report = pts.validate_time_series_data(bad_val_df)
    assert is_valid is False
    si_check = report[report["check"] == "search_interest_valid"].iloc[0]
    assert si_check["status"] == "FAIL"


def test_validation_detects_invalid_surge_labels(synthetic_panel_df):
    bad_label_df = synthetic_panel_df.copy()
    bad_label_df.loc[0, "surge_label"] = 2  # Must be 0 or 1
    is_valid, report = pts.validate_time_series_data(bad_label_df)
    assert is_valid is False
    surge_check = report[report["check"] == "surge_label_valid"].iloc[0]
    assert surge_check["status"] == "FAIL"


# -----------------------------------------------------------------------------
# Feature Engineering and Leakage Prevention Tests
# -----------------------------------------------------------------------------
def test_feature_engineering_lags_and_rolling(synthetic_panel_df):
    df_feat, feature_names = pts.engineer_time_series_features(synthetic_panel_df, lags=[1, 2, 7], rolling_window=7)

    # Check expected features are generated
    assert "search_lag_1" in feature_names
    assert "search_lag_7" in feature_names
    assert "rolling_mean_7d" in feature_names
    assert "rolling_std_7d" in feature_names

    # Check lag alignment: for a given product, search_lag_1 at row t must equal search_interest at row t-1
    p1 = df_feat[df_feat["product_id"] == "P001"].sort_values("date").reset_index(drop=True)
    orig_p1 = synthetic_panel_df[synthetic_panel_df["product_id"] == "P001"].sort_values("date").reset_index(drop=True)

    # The first row of p1 corresponds to day index 7 in orig_p1 due to rolling window burn-in
    first_date = p1["date"].iloc[0]
    orig_idx = orig_p1[orig_p1["date"] == first_date].index[0]

    # Verify lag 1 equality
    assert p1.loc[0, "search_lag_1"] == orig_p1.loc[orig_idx - 1, "search_interest"]
    assert p1.loc[0, "search_lag_7"] == orig_p1.loc[orig_idx - 7, "search_interest"]


def test_rolling_statistics_strict_shift_no_leakage(synthetic_panel_df):
    """Ensure rolling mean uses shift(1), so current date value does NOT affect rolling mean."""
    df_feat, _ = pts.engineer_time_series_features(synthetic_panel_df, lags=[1], rolling_window=3)
    p1 = df_feat[df_feat["product_id"] == "P001"].sort_values("date").reset_index(drop=True)
    orig_p1 = synthetic_panel_df[synthetic_panel_df["product_id"] == "P001"].sort_values("date").reset_index(drop=True)

    # For any row i, rolling_mean_3d must equal the mean of the 3 PRECEDING days (not including current day)
    curr_date = p1.loc[2, "date"]
    orig_idx = orig_p1[orig_p1["date"] == curr_date].index[0]
    expected_mean = orig_p1.loc[orig_idx - 3:orig_idx - 1, "search_interest"].mean()

    assert np.isclose(p1.loc[2, "rolling_mean_3d"], expected_mean, atol=1e-3)


# -----------------------------------------------------------------------------
# Chronological Split Tests
# -----------------------------------------------------------------------------
def test_chronological_split_strict_temporal_boundary(synthetic_panel_df):
    df_feat, feature_names = pts.engineer_time_series_features(synthetic_panel_df, lags=[1, 2, 7], rolling_window=7)
    train_df, test_df, metadata = pts.perform_chronological_split(df_feat, feature_names, split_ratio=0.8)

    # Assert zero date overlap: max(train_date) < min(test_date)
    assert train_df["date"].max() < test_df["date"].min()
    assert metadata["train_window"]["end_date"] < metadata["test_window"]["start_date"]

    # Verify all products exist in both train and test
    assert set(train_df["product_id"].unique()) == set(synthetic_panel_df["product_id"].unique())
    assert set(test_df["product_id"].unique()) == set(synthetic_panel_df["product_id"].unique())


def test_scaler_and_encoder_fit_on_train_only(synthetic_panel_df):
    df_feat, feature_names = pts.engineer_time_series_features(synthetic_panel_df, lags=[1, 2], rolling_window=7)
    train_df, test_df, metadata = pts.perform_chronological_split(df_feat, feature_names, split_ratio=0.8)

    # Scaled continuous features in train must have mean close to 0 and std close to 1
    for feat in ["search_lag_1", "rolling_mean_7d"]:
        assert np.isclose(train_df[feat].mean(), 0.0, atol=1e-2)
        assert np.isclose(train_df[feat].std(ddof=0), 1.0, atol=1e-2)

    # Test features are transformed with train parameters, so their mean won't be identically 0
    # but must have finite, valid transformed values
    assert test_df["search_lag_1"].notna().all()
    assert not np.isinf(test_df["search_lag_1"]).any()


def test_exclusion_of_leakage_columns(synthetic_panel_df):
    df_feat, feature_names = pts.engineer_time_series_features(synthetic_panel_df, lags=[1], rolling_window=7)
    train_df, test_df, metadata = pts.perform_chronological_split(df_feat, feature_names, split_ratio=0.8)

    excluded = metadata["excluded_leakage_columns"]
    predictor_set = set(metadata["predictor_features"]["temporal_lags"] +
                        metadata["predictor_features"]["rolling_volatility"] +
                        metadata["predictor_features"]["categorical_dummies"])

    for leak_col in ["yt_snapshot_date", "yt_videos_on_target", "surge_label", "search_interest", "category"]:
        assert leak_col in excluded
        assert leak_col not in predictor_set


# -----------------------------------------------------------------------------
# Stationarity & Lag Diagnostics Tests
# -----------------------------------------------------------------------------
def test_stationarity_diagnostics_normal_series():
    # AR(1) stationary series: x_t = 0.5 * x_{t-1} + e_t
    np.random.seed(123)
    n = 100
    x = [0.0]
    for _ in range(n - 1):
        x.append(0.3 * x[-1] + np.random.normal(0, 1))

    df = pd.DataFrame({
        "product_id": ["P001"] * n,
        "date": pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
        "search_interest": x,
        "surge_label": [0] * n,
        "brand": ["Apple"] * n,
        "product_type": ["STABLE"] * n,
    })

    stat_df, lag_df = pts.run_stationarity_diagnostics(df, lags_to_evaluate=[1, 2])
    assert len(stat_df) == 2  # raw_levels and first_diff
    raw_res = stat_df[stat_df["series_type"] == "raw_levels"].iloc[0]
    assert raw_res["test_status"] == "COMPLETED"
    assert bool(raw_res["is_adf_stationary"]) is True  # Stationary AR(1) rejects unit root


def test_stationarity_handles_constant_zero_variance():
    # Constant series (e.g. unreleased smartphone with 0 search volume)
    n = 100
    df = pd.DataFrame({
        "product_id": ["P_CONST"] * n,
        "date": pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
        "search_interest": [0.0] * n,
        "surge_label": [0] * n,
        "brand": ["Unknown"] * n,
        "product_type": ["RISING"] * n,
    })

    stat_df, _ = pts.run_stationarity_diagnostics(df)
    assert len(stat_df) == 2
    for _, row in stat_df.iterrows():
        assert row["test_status"] == "SKIPPED"
        assert row["reason"] == "constant_zero_variance"
        assert row["is_adf_stationary"] is False


def test_stationarity_handles_insufficient_sample_size():
    # Length < 20 observations
    n = 10
    df = pd.DataFrame({
        "product_id": ["P_SHORT"] * n,
        "date": pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
        "search_interest": list(range(n)),
        "surge_label": [0] * n,
        "brand": ["Test"] * n,
        "product_type": ["STABLE"] * n,
    })

    stat_df, _ = pts.run_stationarity_diagnostics(df)
    for _, row in stat_df.iterrows():
        assert row["test_status"] == "SKIPPED"
        assert "insufficient_sample_size" in row["reason"]


# -----------------------------------------------------------------------------
# End-to-End Pipeline Execution Tests
# -----------------------------------------------------------------------------
def test_pipeline_missing_master_file_returns_error(tmp_path):
    mock_paths = config.Paths.from_root(tmp_path)
    rc = pts.main(paths=mock_paths, quiet=True)
    assert rc == 1


def test_pipeline_execution_on_master_dataset():
    """Verify execution of prep_time_series against the actual project master dataset."""
    rc = pts.main(quiet=True, generate_plots=True)
    assert rc == 0

    paths = config.PATHS
    assert paths.time_series_train_csv.exists()
    assert paths.time_series_test_csv.exists()
    assert paths.time_series_split_metadata_json.exists()
    assert paths.time_series_validation_report_csv.exists()
    assert paths.stationarity_report_csv.exists()
    assert paths.lag_diagnostics_csv.exists()

    # Verify row counts and temporal split matching Review 1 benchmark
    train_df = pd.read_csv(paths.time_series_train_csv)
    test_df = pd.read_csv(paths.time_series_test_csv)
    with open(paths.time_series_split_metadata_json, encoding="utf-8") as f:
        meta = json.load(f)

    assert len(train_df) == 12540
    assert len(test_df) == 3180
    assert train_df["surge_label"].sum() == 586
    assert test_df["surge_label"].sum() == 208
    assert meta["train_window"]["start_date"] == "2026-01-15"
    assert meta["train_window"]["end_date"] == "2026-08-11"
    assert meta["test_window"]["start_date"] == "2026-08-12"
    assert meta["test_window"]["end_date"] == "2026-10-03"
