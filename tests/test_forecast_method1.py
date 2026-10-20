"""Tests for SCRUM-26: Time-Series Forecasting Model Implementation (src/forecast_method1.py)."""

from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import pytest

import config
import forecast_method1 as fm


@pytest.fixture
def synthetic_train_test_data() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Create small synthetic train and test panel datasets."""
    dates_train = pd.date_range("2026-01-01", periods=40, freq="D").strftime("%Y-%m-%d").tolist()
    dates_test = pd.date_range("2026-02-10", periods=10, freq="D").strftime("%Y-%m-%d").tolist()

    products = [
        ("P001", "Phone Alpha", "Apple", "RISING"),
        ("P002", "Phone Beta", "Samsung", "STABLE"),
    ]

    def make_rows(dates, is_test=False):
        rows = []
        for pid, pname, brand, ptype in products:
            base_val = 30.0 if ptype == "STABLE" else 10.0
            curr = base_val
            for dt in dates:
                curr = max(0.0, min(100.0, 0.8 * curr + np.random.normal(0, 2)))
                row = {
                    "product_id": pid,
                    "date": dt,
                    "product_name": pname,
                    "brand": brand,
                    "category": "Smartphones",
                    "product_type": ptype,
                    "search_interest": round(curr, 2),
                    "surge_label": 1 if curr > 35 else 0,
                    "search_lag_1": round(curr * 0.9, 2),
                    "search_lag_2": round(curr * 0.85, 2),
                    "search_lag_3": round(curr * 0.8, 2),
                    "search_lag_7": round(curr * 0.75, 2),
                    "growth_lag_1": 0.05,
                    "growth_lag_2": 0.04,
                    "growth_lag_3": 0.03,
                    "growth_lag_7": 0.02,
                    "accel_lag_1": 0.01,
                    "accel_lag_2": 0.01,
                    "accel_lag_3": 0.01,
                    "accel_lag_7": 0.01,
                    "videos_published_lag_1": 1.0,
                    "videos_published_lag_2": 1.0,
                    "videos_published_lag_3": 1.0,
                    "videos_published_lag_7": 1.0,
                    "comments_published_lag_1": 5.0,
                    "comments_published_lag_2": 4.0,
                    "comments_published_lag_3": 3.0,
                    "comments_published_lag_7": 2.0,
                    "rolling_mean_7d": round(curr, 2),
                    "rolling_std_7d": 1.5,
                    "brand_Apple": 1 if brand == "Apple" else 0,
                    "brand_Samsung": 1 if brand == "Samsung" else 0,
                    "product_type_RISING": 1 if ptype == "RISING" else 0,
                    "product_type_STABLE": 1 if ptype == "STABLE" else 0,
                }
                rows.append(row)
        return pd.DataFrame(rows)

    return make_rows(dates_train), make_rows(dates_test, is_test=True)


# -----------------------------------------------------------------------------
# Unit Tests
# -----------------------------------------------------------------------------
def test_wape_calculation_normal_and_zero_denom():
    """Verify Weighted Absolute Percentage Error formula."""
    y_true = np.array([10.0, 20.0, 30.0])
    y_pred = np.array([12.0, 18.0, 33.0])
    # sum(|err|) = 2 + 2 + 3 = 7, sum(y) = 60 -> 7/60 * 100 = 11.67%
    wape = fm.calculate_wape(y_true, y_pred)
    assert np.isclose(wape, 7.0 / 60.0 * 100.0, atol=1e-2)

    # Edge case: all zeros
    assert fm.calculate_wape(np.zeros(5), np.zeros(5)) == 0.0


def test_mda_directional_accuracy():
    """Verify Mean Directional Accuracy sign logic."""
    y_true = pd.Series([10.0, 15.0, 12.0, 20.0])
    lag_1 = pd.Series([9.0, 10.0, 14.0, 18.0])
    # Actual direction from lag: [+1, +1, -1, +1]
    # Pred: [+1, +1, +1, +1] -> 3/4 = 75%
    y_pred = pd.Series([12.0, 16.0, 16.0, 22.0])
    mda = fm.calculate_mda(y_true, y_pred, lag_1)
    assert np.isclose(mda, 75.0, atol=1e-2)


def test_naive_persistence_logic(synthetic_train_test_data):
    """Verify Naive 1-step persistence uses exact preceding values."""
    train_df, test_df = synthetic_train_test_data
    preds = fm.forecast_naive_persistence(test_df, train_df)

    assert len(preds) == len(test_df)
    # Day 0 of test should equal last train value
    p1_train_last = train_df[train_df["product_id"] == "P001"]["search_interest"].iloc[-1]
    p1_test_first_pred = preds.iloc[0]
    assert np.isclose(p1_test_first_pred, p1_train_last)


def test_seasonal_naive_logic(synthetic_train_test_data):
    """Verify 7-day seasonal naive lag mapping."""
    train_df, test_df = synthetic_train_test_data
    preds = fm.forecast_seasonal_naive(test_df, train_df, season=7)
    assert len(preds) == len(test_df)
    assert not preds.isna().any()


def test_rolling_mean_persistence_logic(synthetic_train_test_data):
    """Verify 7-day rolling mean baseline prediction."""
    train_df, test_df = synthetic_train_test_data
    preds = fm.forecast_rolling_mean(test_df, train_df, window=7)
    assert len(preds) == len(test_df)
    assert not preds.isna().any()


def test_arima_forecast_handles_degenerate_series():
    """Verify ARIMA gracefully handles zero-variance/constant series."""
    train_df = pd.DataFrame({
        "product_id": ["P003"] * 30,
        "date": pd.date_range("2026-01-01", periods=30).strftime("%Y-%m-%d"),
        "search_interest": [0.0] * 30,
    })
    test_df = pd.DataFrame({
        "product_id": ["P003"] * 10,
        "date": pd.date_range("2026-02-01", periods=10).strftime("%Y-%m-%d"),
        "search_interest": [0.0] * 10,
    })

    preds = fm.forecast_arima_per_product(test_df, train_df, order=(1, 1, 1))
    assert len(preds) == 10
    assert (preds == 0.0).all()


def test_exponential_smoothing_forecast(synthetic_train_test_data):
    """Verify Exponential Smoothing generates valid bounded forecasts."""
    train_df, test_df = synthetic_train_test_data
    preds = fm.forecast_exponential_smoothing_per_product(test_df, train_df)
    assert len(preds) == len(test_df)
    assert (preds >= 0.0).all()
    assert (preds <= 100.0).all()


def test_feature_column_extraction(synthetic_train_test_data):
    """Verify get_method1_feature_columns selects only valid predictor columns."""
    train_df, _ = synthetic_train_test_data
    feature_cols = fm.get_method1_feature_columns(train_df)

    assert "search_lag_1" in feature_cols
    assert "rolling_mean_7d" in feature_cols
    assert "brand_Apple" in feature_cols
    assert "search_interest" not in feature_cols
    assert "surge_label" not in feature_cols


def test_ml_regressors_fit_and_predict(synthetic_train_test_data):
    """Verify ML models train and predict within valid [0, 100] bounds."""
    train_df, test_df = synthetic_train_test_data
    feature_cols = fm.get_method1_feature_columns(train_df)

    X_train = train_df[feature_cols]
    y_train = train_df["search_interest"]
    X_test = test_df[feature_cols]

    preds, fitted = fm.train_and_predict_ml_models(X_train, y_train, X_test)

    assert "Ridge_Regression" in preds
    assert "Random_Forest" in preds
    assert "Gradient_Boosting" in preds

    for name, p_series in preds.items():
        assert len(p_series) == len(test_df)
        assert (p_series >= 0.0).all()
        assert (p_series <= 100.0).all()


def test_cohort_breakdown_metrics(synthetic_train_test_data):
    """Verify cohort breakdown produces metrics for Rising and Stable cohorts."""
    train_df, test_df = synthetic_train_test_data
    preds = {
        "Naive_Persistence": test_df["search_lag_1"],
        "Gradient_Boosting": test_df["search_lag_1"] * 0.98,
    }
    cohort_df = fm.compute_cohort_breakdown(test_df, preds, best_model_name="Gradient_Boosting")

    assert len(cohort_df) == 2
    assert "RISING" in cohort_df["cohort"].values
    assert "STABLE" in cohort_df["cohort"].values
    assert "best_model_mae" in cohort_df.columns
    assert "mae_reduction_vs_naive_pct" in cohort_df.columns


def test_feature_importance_ranking(synthetic_train_test_data):
    """Verify feature importances are extracted and normalized."""
    train_df, test_df = synthetic_train_test_data
    feature_cols = fm.get_method1_feature_columns(train_df)

    X_train = train_df[feature_cols]
    y_train = train_df["search_interest"]
    X_test = test_df[feature_cols]

    _, fitted = fm.train_and_predict_ml_models(X_train, y_train, X_test)
    feat_df = fm.extract_feature_importances(fitted, feature_cols)

    assert len(feat_df) == len(feature_cols)
    assert "gbr_importance" in feat_df.columns
    assert "rf_importance" in feat_df.columns
    assert np.isclose(feat_df["gbr_importance"].sum(), 1.0, atol=1e-2)


def test_missing_train_or_test_returns_error(tmp_path):
    """Verify exit code 1 when splits are missing."""
    empty_paths = config.Paths.from_root(tmp_path)
    rc = fm.main(paths=empty_paths, quiet=True)
    assert rc == 1


def test_pipeline_execution_end_to_end_on_real_data():
    """Verify full CLI execution on production train and test splits."""
    paths = config.PATHS
    assert paths.time_series_train_csv.exists()
    assert paths.time_series_test_csv.exists()

    rc = fm.main(paths=paths, generate_plots=False, quiet=True)
    assert rc == 0

    assert paths.method1_model_comparison_csv.exists()
    assert paths.method1_forecast_predictions_csv.exists()
    assert paths.method1_cohort_performance_csv.exists()
    assert paths.method1_feature_importance_csv.exists()

    comp_df = pd.read_csv(paths.method1_model_comparison_csv)
    pred_df = pd.read_csv(paths.method1_forecast_predictions_csv)
    cohort_df = pd.read_csv(paths.method1_cohort_performance_csv)

    assert len(comp_df) >= 7
    assert len(pred_df) == 3180
    assert len(cohort_df) == 3
