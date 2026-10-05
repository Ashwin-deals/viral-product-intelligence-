"""Encode categorical features, scale numerical features, and engineer the surge label.

Input:   data/processed/aligned_daily.csv  (src/build_aligned.py; never modified)
Output:  processed/features_encoded_scaled.csv

Steps:
1. Load aligned data and log its shape.
2. Engineer growth-rate and acceleration columns from search_interest.
3. Derive the surge_label from growth-rate percentiles (top 15 % of positive growth → 1).
4. Fill missing categoricals with "Unknown", then one-hot encode brand and product_type
   (drop_first=True to avoid multi-collinearity).  category is dropped (constant).
5. StandardScaler on continuous features; identifiers (product_id, product_name, date) and
   surge_label are never scaled.
6. Save to processed/features_encoded_scaled.csv.

Usage:  python3 src/feature_encoding_scaling.py
"""

import sys

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

import config

# ---------------------------------------------------------------------------
# Column groups
# ---------------------------------------------------------------------------
IDENTIFIER_COLS = ["product_id", "product_name", "date"]

CATEGORICAL_COLS = ["brand", "product_type"]
# "category" is constant ('Smartphones' for all 60 products) → dropped, not encoded.

NUMERICAL_COLS = [
    "search_interest",
    "video_views_total",
    "video_comments_total",
    "views_on_target_total",
    "comments_on_target_total",
    "videos_on_target",
    "on_target_share",
    "zero_share_product",
    "videos_published_7d",
    "videos_7d_on_target",
]

ENGINEERED_COLS = ["search_interest_growth", "search_interest_accel"]

# Columns that are operational flags / metadata and not modelling features.
DROP_COLS = [
    "category",
    "trends_observed",
    "is_below_threshold",
    "is_zero",
    "is_provisional",
    "low_signal_product",
    "youtube_observed",
    "youtube_query_version",
    "video_result_count",
    "low_on_target_share",
    "result_cap_hit",
    "youtube_recent_observed",
    "recent_cap_hit",
]

ALIGNED_IN = "aligned_daily.csv"
FEATURES_OUT = "features_encoded_scaled.csv"

# Surge-label threshold: growth values at or above this percentile of *positive*
# growth values are labelled as a surge (1).
SURGE_PERCENTILE = 85


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add search_interest_growth, search_interest_accel, and surge_label."""
    df = df.copy()
    df = df.sort_values(["product_id", "date"], kind="stable").reset_index(drop=True)

    # Day-over-day percentage change of search_interest, per product.
    df["search_interest_growth"] = (
        df.groupby("product_id")["search_interest"]
        .pct_change()
        .replace([np.inf, -np.inf], np.nan)
    )

    # Acceleration: day-over-day change of growth rate.
    df["search_interest_accel"] = (
        df.groupby("product_id")["search_interest_growth"]
        .diff()
    )

    # Surge label: top SURGE_PERCENTILE of *positive* growth values → 1, else 0.
    positive_growth = df.loc[df["search_interest_growth"] > 0, "search_interest_growth"]
    if len(positive_growth) > 0:
        threshold = np.nanpercentile(positive_growth, SURGE_PERCENTILE)
    else:
        threshold = np.inf  # no surges if there is no positive growth
    df["surge_label"] = (df["search_interest_growth"] >= threshold).astype(int)
    # Missing growth → no surge.
    df.loc[df["search_interest_growth"].isna(), "surge_label"] = 0

    return df


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """One-hot encode brand and product_type with drop_first=True.

    Missing values are filled with 'Unknown' before encoding.
    Returns a new DataFrame with the original categorical columns removed.
    """
    df = df.copy()
    for col in CATEGORICAL_COLS:
        if col in df.columns:
            df[col] = df[col].fillna("Unknown").replace("", "Unknown")

    encoded = pd.get_dummies(df[CATEGORICAL_COLS], drop_first=True, dtype=int)
    df = df.drop(columns=CATEGORICAL_COLS)
    df = pd.concat([df, encoded], axis=1)
    return df


# ---------------------------------------------------------------------------
# Scaling
# ---------------------------------------------------------------------------

def scale_numericals(df: pd.DataFrame) -> pd.DataFrame:
    """StandardScaler on numerical + engineered columns. NaNs remain NaN."""
    df = df.copy()
    scale_cols = [c for c in NUMERICAL_COLS + ENGINEERED_COLS if c in df.columns]
    scaler = StandardScaler()
    # fit_transform ignores NaN rows; we mask NaN → fill → transform → restore NaN
    values = df[scale_cols].values
    nan_mask = np.isnan(values)
    # Replace NaN with 0 for fitting, then restore after transform
    values_filled = np.where(nan_mask, 0, values)
    scaled = scaler.fit_transform(values_filled)
    scaled = np.where(nan_mask, np.nan, scaled)
    df[scale_cols] = scaled
    return df


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Full encoding + scaling pipeline.  Returns the transformed DataFrame."""
    rows_in = len(df)
    cols_in = len(df.columns)
    print(f"[encoding-scaling] Input shape: {rows_in:,} rows × {cols_in} columns")

    # 1. Drop operational / non-feature columns
    drop = [c for c in DROP_COLS if c in df.columns]
    df = df.drop(columns=drop)
    print(f"[encoding-scaling] Dropped {len(drop)} operational/flag columns: {drop}")

    # 2. Feature engineering (growth, acceleration, surge label)
    df = engineer_features(df)
    surge_counts = df["surge_label"].value_counts().to_dict()
    print(f"[encoding-scaling] Engineered features: search_interest_growth, "
          f"search_interest_accel, surge_label  (distribution: {surge_counts})")

    # 3. Encode categoricals
    cat_before = [c for c in CATEGORICAL_COLS if c in df.columns]
    df = encode_categoricals(df)
    encoded_cols = [c for c in df.columns if any(c.startswith(f"{cb}_") for cb in cat_before)]
    print(f"[encoding-scaling] Encoded {len(cat_before)} categorical column(s) → "
          f"{len(encoded_cols)} dummy column(s)")

    # 4. Scale numericals (surge_label and identifiers untouched)
    scale_cols = [c for c in NUMERICAL_COLS + ENGINEERED_COLS if c in df.columns]
    df = scale_numericals(df)
    print(f"[encoding-scaling] Scaled {len(scale_cols)} numerical column(s) with StandardScaler")

    # 5. Reorder: identifiers, scaled numericals, encoded categoricals, surge_label
    id_cols = [c for c in IDENTIFIER_COLS if c in df.columns]
    num_cols = [c for c in NUMERICAL_COLS + ENGINEERED_COLS if c in df.columns]
    other_cols = [c for c in df.columns if c not in id_cols + num_cols + encoded_cols + ["surge_label"]]
    final_order = id_cols + num_cols + encoded_cols + ["surge_label"]
    # Include any remaining columns not yet categorised (safety net)
    final_order += [c for c in other_cols if c not in final_order]
    df = df[final_order]

    print(f"[encoding-scaling] Output shape: {len(df):,} rows × {len(df.columns)} columns")
    return df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(paths=config.PATHS):
    in_path = paths.processed_dir / ALIGNED_IN
    if not in_path.exists() or in_path.stat().st_size == 0:
        print(f"Aligned data ({in_path}) not found. Run src/build_aligned.py first.",
              file=sys.stderr)
        return 1

    df = pd.read_csv(in_path)
    print(f"[encoding-scaling] Loaded {in_path}")

    result = build_features(df)

    out_path = paths.root / "processed" / FEATURES_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False, lineterminator="\n")
    print(f"[encoding-scaling] Saved → {out_path}")

    # Quick sanity checks
    assert "surge_label" in result.columns, "surge_label missing from output"
    for col in CATEGORICAL_COLS:
        assert col not in result.columns, f"Raw categorical {col} still in output"
    for col in IDENTIFIER_COLS:
        if col in result.columns:
            assert result[col].equals(df[col]), f"Identifier {col} was modified"
    print("[encoding-scaling] ✓ Sanity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
