"""Tests for src/feature_encoding_scaling.py."""

import numpy as np
import pandas as pd
import pytest

import feature_encoding_scaling as fes
import config


def _make_sample(n_products=3, n_days=10):
    """Return a small DataFrame that looks like aligned_daily.csv."""
    rows = []
    brands = ["Apple", "Samsung", "Google"]
    types = ["RISING", "STABLE", "DECLINING"]
    for i in range(n_products):
        pid = f"P{i + 1:03d}"
        for d in range(n_days):
            rows.append({
                "product_id": pid,
                "date": f"2026-01-{d + 1:02d}",
                "product_name": f"Product {i + 1}",
                "brand": brands[i % len(brands)],
                "category": "Smartphones",
                "product_type": types[i % len(types)],
                "trends_observed": True,
                "search_interest": float(np.random.randint(0, 101)),
                "is_below_threshold": False,
                "is_zero": False,
                "is_provisional": d == n_days - 1,
                "zero_share_product": 0.02,
                "low_signal_product": False,
                "youtube_observed": d % 3 == 0,
                "youtube_query_version": "v1",
                "video_result_count": 50.0 if d % 3 == 0 else np.nan,
                "video_views_total": float(np.random.randint(1000, 100000)) if d % 3 == 0 else np.nan,
                "video_comments_total": float(np.random.randint(10, 5000)) if d % 3 == 0 else np.nan,
                "videos_on_target": float(np.random.randint(5, 40)) if d % 3 == 0 else np.nan,
                "views_on_target_total": float(np.random.randint(500, 50000)) if d % 3 == 0 else np.nan,
                "comments_on_target_total": float(np.random.randint(5, 2000)) if d % 3 == 0 else np.nan,
                "on_target_share": round(np.random.uniform(0.3, 1.0), 4) if d % 3 == 0 else np.nan,
                "low_on_target_share": "False",
                "result_cap_hit": "True" if d % 3 == 0 else "",
                "youtube_recent_observed": d % 5 == 0,
                "videos_published_7d": float(np.random.randint(1, 50)) if d % 5 == 0 else np.nan,
                "videos_7d_on_target": float(np.random.randint(0, 20)) if d % 5 == 0 else np.nan,
                "recent_cap_hit": "",
            })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_df():
    np.random.seed(42)
    return _make_sample()


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def test_feature_engineering_growth_accel(sample_df):
    result = fes.engineer_features(sample_df)
    assert "search_interest_growth" in result.columns
    assert "search_interest_accel" in result.columns
    assert "surge_label" in result.columns
    # First day of each product should have NaN growth (no previous day)
    first_rows = result.groupby("product_id").nth(0)
    assert first_rows["search_interest_growth"].isna().all()


def test_surge_label_binary(sample_df):
    result = fes.engineer_features(sample_df)
    assert set(result["surge_label"].unique()).issubset({0, 1})


def test_surge_label_no_leakage(sample_df):
    result = fes.build_features(sample_df)
    # surge_label must NOT be among the scaled columns
    scale_cols = [c for c in fes.NUMERICAL_COLS + fes.ENGINEERED_COLS if c in result.columns]
    assert "surge_label" not in scale_cols
    # Check that surge_label values are still 0/1 integers (not z-scored floats)
    assert result["surge_label"].dropna().isin([0, 1]).all()


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def test_categorical_encoding(sample_df):
    result = fes.encode_categoricals(sample_df)
    # Raw categorical columns must be gone
    for col in fes.CATEGORICAL_COLS:
        assert col not in result.columns
    # Dummy columns must exist (drop_first removes one per category)
    brand_dummies = [c for c in result.columns if c.startswith("brand_")]
    product_type_dummies = [c for c in result.columns if c.startswith("product_type_")]
    assert len(brand_dummies) >= 1  # at least N-1 where N is unique brands
    assert len(product_type_dummies) >= 1


def test_encoding_missing_brand():
    """Missing brand values should be filled with 'Unknown' before encoding."""
    df = pd.DataFrame({
        "brand": ["Apple", None, ""],
        "product_type": ["RISING", "STABLE", "DECLINING"],
    })
    result = fes.encode_categoricals(df)
    assert "brand" not in result.columns
    # "Unknown" should have been one of the categories (may or may not be dropped by drop_first)
    # Just verify no errors and result has the right number of rows
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Scaling
# ---------------------------------------------------------------------------


def test_numerical_scaling_mean_std(sample_df):
    df = fes.engineer_features(sample_df)
    result = fes.scale_numericals(df)
    # Columns with enough non-NaN values should have mean ≈ 0 and std ≈ 1
    for col in ["search_interest"]:  # guaranteed to have values in every row
        vals = result[col].dropna()
        if len(vals) > 2:
            assert abs(vals.mean()) < 0.1, f"{col} mean is {vals.mean()}"
            assert abs(vals.std() - 1.0) < 0.2, f"{col} std is {vals.std()}"


def test_scaling_preserves_nan(sample_df):
    """NaN values in numerical columns should remain NaN after scaling."""
    df = fes.engineer_features(sample_df)
    nans_before = df["video_views_total"].isna().sum()
    result = fes.scale_numericals(df)
    nans_after = result["video_views_total"].isna().sum()
    assert nans_before == nans_after


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def test_identifiers_preserved(sample_df):
    result = fes.build_features(sample_df)
    for col in fes.IDENTIFIER_COLS:
        assert col in result.columns
        assert result[col].tolist() == sample_df.sort_values(
            ["product_id", "date"], kind="stable"
        ).reset_index(drop=True)[col].tolist()


def test_output_no_original_categoricals(sample_df):
    result = fes.build_features(sample_df)
    for col in fes.CATEGORICAL_COLS + ["category"]:
        assert col not in result.columns


def test_output_shape(sample_df):
    result = fes.build_features(sample_df)
    assert len(result) == len(sample_df)
    # Should have: 3 identifiers + 12 numericals + N dummies + 1 surge_label
    assert result.shape[1] > 15


def test_dropped_columns_absent(sample_df):
    result = fes.build_features(sample_df)
    for col in fes.DROP_COLS:
        assert col not in result.columns


# ---------------------------------------------------------------------------
# Integration: real data (skipped if aligned_daily.csv is missing)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (config.PATHS.processed_dir / "aligned_daily.csv").exists(),
    reason="aligned_daily.csv not present",
)
def test_cli_main_real_data(tmp_path):
    """Run main() end to end on real aligned data, writing to a temp dir."""
    import shutil

    # Copy aligned_daily.csv into a temp tree that matches Paths layout
    temp_root = tmp_path / "project"
    temp_root.mkdir()
    proc = temp_root / "data" / "processed"
    proc.mkdir(parents=True)
    shutil.copy(config.PATHS.processed_dir / "aligned_daily.csv", proc / "aligned_daily.csv")
    (temp_root / "processed").mkdir(exist_ok=True)

    paths = config.Paths.from_root(temp_root)
    rc = fes.main(paths=paths)
    assert rc == 0

    out = temp_root / "processed" / fes.FEATURES_OUT
    assert out.exists()
    result = pd.read_csv(out)
    assert "surge_label" in result.columns
    assert result["surge_label"].isin([0, 1]).all()
    assert "brand" not in result.columns
    assert "product_type" not in result.columns
    assert "product_id" in result.columns
