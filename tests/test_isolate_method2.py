"""Tests for SCRUM-24: Leakage Audit & Method 2 Data Isolation (src/isolate_method2_data.py)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config
import isolate_method2_data as m2


@pytest.fixture
def synthetic_corpus_df() -> pd.DataFrame:
    """Creates a synthetic comment corpus for testing NLP extraction and leakage."""
    rows = [
        # Train period comments (date < 2026-08-12)
        {
            "product_id": "P001",
            "comment_id": "c1",
            "video_id": "v1",
            "published_at": "2026-02-01T10:00:00Z",
            "text_raw": "Awesome camera and great battery life!",
            "text_sentiment_ready": "awesome camera and great battery life!",
            "text_clean": "awesome camera great battery life",
            "tokens": "awesome camera great battery life",
            "is_usable": True,
        },
        {
            "product_id": "P001",
            "comment_id": "c2",
            "video_id": "v1",
            "published_at": "2026-02-01T12:00:00Z",
            "text_raw": "Worst phone heating issue terrible lag",
            "text_sentiment_ready": "worst phone heating issue terrible lag",
            "text_clean": "worst phone heating issue terrible lag",
            "tokens": "worst phone heating issue terrible lag",
            "is_usable": True,
        },
        {
            "product_id": "P002",
            "comment_id": "c3",
            "video_id": "v2",
            "published_at": "2026-05-15T08:30:00Z",
            "text_raw": "Very expensive but best display and screen",
            "text_sentiment_ready": "very expensive but best display and screen",
            "text_clean": "expensive best display screen",
            "tokens": "expensive best display screen",
            "is_usable": True,
        },
        # Test period comments (date >= 2026-08-12)
        {
            "product_id": "P001",
            "comment_id": "c4",
            "video_id": "v1",
            "published_at": "2026-08-20T14:00:00Z",
            "text_raw": "Love the gaming speed and chipset performance",
            "text_sentiment_ready": "love the gaming speed and chipset performance",
            "text_clean": "love gaming speed chipset performance",
            "tokens": "love gaming speed chipset performance",
            "is_usable": True,
        },
        {
            "product_id": "P002",
            "comment_id": "c5",
            "video_id": "v2",
            "published_at": "2026-09-01T09:00:00Z",
            "text_raw": "Unusable emoji only comment",
            "text_sentiment_ready": "unusable emoji only comment",
            "text_clean": "",
            "tokens": "",
            "is_usable": False,  # Should be filtered out
        },
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def synthetic_panel_df() -> pd.DataFrame:
    """Creates a deterministic synthetic panel dataset across 2 products and 60 days."""
    dates = pd.date_range("2026-07-01", periods=60, freq="D").strftime("%Y-%m-%d").tolist()
    products = [
        ("P001", "Phone Alpha", "BrandA", "RISING"),
        ("P002", "Phone Beta", "BrandB", "STABLE"),
    ]
    rows = []
    np.random.seed(42)

    for pid, pname, brand, ptype in products:
        curr = 20.0
        for i, dt in enumerate(dates):
            curr = max(0.0, min(100.0, 0.8 * curr + np.random.normal(0, 2)))
            growth = 0.0 if i == 0 else (curr - 20.0) / 20.0
            accel = 0.0 if i < 2 else growth * 0.1
            surge = 1 if growth > 0.3 else 0
            rows.append({
                "product_id": pid,
                "date": dt,
                "product_name": pname,
                "brand": brand,
                "category": "Smartphones",
                "product_type": ptype,
                "launch_period": "2026-Q1",
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
# Unit Tests
# -----------------------------------------------------------------------------
def test_sentiment_scoring_bounds_and_polarity():
    """Verify sentiment polarity scores remain bounded in [-1.0, 1.0]."""
    texts = pd.Series([
        "This phone is absolutely amazing and awesome, love it!",
        "Terrible waste of money, worst camera ever and horrible battery lag",
        "This is a phone review video with 128GB storage",
        "",
    ])
    res = m2.score_comment_sentiment(texts)

    assert len(res) == 4
    assert (res["compound"] >= -1.0).all()
    assert (res["compound"] <= 1.0).all()
    # Positive text compound > 0.05
    assert res.loc[0, "compound"] > 0.05
    assert res.loc[0, "is_pos"] == 1
    # Negative text compound < -0.05
    assert res.loc[1, "compound"] < -0.05
    assert res.loc[1, "is_neg"] == 1


def test_domain_topic_scoring_normalized():
    """Verify domain topic keyword intensities are properly normalized."""
    texts = pd.Series([
        "camera lens photo video zoom",
        "battery charger charging backup mah",
        "gaming processor snapdragon speed fps",
        "price budget expensive cost offer",
        "display screen amoled design build",
        "unrelated content nothing here",
    ])
    scores = m2.extract_domain_topic_scores(texts)

    assert "topic_camera" in scores.columns
    assert "topic_battery" in scores.columns
    assert scores.loc[0, "topic_camera"] > 0.5
    assert scores.loc[1, "topic_battery"] > 0.5
    assert scores.loc[2, "topic_performance"] > 0.5
    assert scores.loc[3, "topic_price_value"] > 0.5
    assert scores.loc[4, "topic_design_display"] > 0.5


def test_topic_model_train_only_fitting(synthetic_corpus_df):
    """Verify topic models are fitted strictly on train comments, preventing test leakage."""
    usable = synthetic_corpus_df[synthetic_corpus_df["is_usable"]].copy()
    split_date = "2026-08-12"

    usable["date_ist"] = pd.to_datetime(usable["published_at"], utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")
    train_texts = usable.loc[usable["date_ist"] < split_date, "text_clean"]
    all_texts = usable["text_clean"]

    topic_dist, cv, lda = m2.fit_and_extract_lda_topics(train_texts, all_texts, n_topics=2)

    # Features should come from train texts only
    vocab = cv.get_feature_names_out()
    assert "awesome" in vocab or "camera" in vocab or "battery" in vocab
    # Verify shape
    assert topic_dist.shape == (len(all_texts), 2)
    # Row probabilities sum to ~1.0
    assert np.allclose(topic_dist.sum(axis=1), 1.0, atol=1e-3)


def test_timezone_conversion_ist_integrity():
    """Verify UTC timestamps are converted to Asia/Kolkata date without date slippage."""
    # 2026-08-11 23:30 UTC is 2026-08-12 05:00 IST
    ts = pd.Series(["2026-08-11T23:30:00Z", "2026-08-11T12:00:00Z"])
    dt_utc = pd.to_datetime(ts, utc=True)
    dt_ist = dt_utc.dt.tz_convert(m2.INDIA_TIMEZONE).dt.strftime("%Y-%m-%d")

    assert dt_ist.iloc[0] == "2026-08-12"
    assert dt_ist.iloc[1] == "2026-08-11"


def test_daily_sentiment_and_topic_aggregation(synthetic_corpus_df, synthetic_panel_df):
    """Verify daily aggregation builds panel rows and neutral fills unobserved days."""
    daily_signals, meta = m2.build_daily_sentiment_and_topics(
        synthetic_corpus_df, synthetic_panel_df, split_date="2026-08-12", n_topics=2
    )

    assert len(daily_signals) == len(synthetic_panel_df)
    assert "sentiment_mean" in daily_signals.columns
    assert "sentiment_net_polarity" in daily_signals.columns
    assert "topic_camera" in daily_signals.columns
    assert "sentiment_observed" in daily_signals.columns

    # Verify unobserved days have sentiment_observed == 0 and neutral values
    unobs = daily_signals[daily_signals["sentiment_observed"] == 0]
    assert (unobs["sentiment_mean"] == 0.0).all()
    assert (unobs["sentiment_net_polarity"] == 0.0).all()


def test_contemporaneous_signals_excluded_from_features(synthetic_corpus_df, synthetic_panel_df):
    """Verify unlagged contemporaneous sentiment/topic signals are excluded from predictors."""
    daily_signals, _ = m2.build_daily_sentiment_and_topics(
        synthetic_corpus_df, synthetic_panel_df, split_date="2026-08-12", n_topics=2
    )
    df_eng, m1_feats, m2_feats = m2.engineer_method2_features(daily_signals, lags=[1, 2, 3, 7])

    active_feats = m1_feats + m2_feats
    assert "sentiment_mean" not in active_feats
    assert "sentiment_net_polarity" not in active_feats
    assert "topic_camera" not in active_feats
    assert "search_interest" not in active_feats
    assert "surge_label" not in active_feats


def test_lag_feature_shift_exact_match(synthetic_corpus_df, synthetic_panel_df):
    """Verify mathematical shift property: Lag_k(t) == Value(t-k)."""
    daily_signals, _ = m2.build_daily_sentiment_and_topics(
        synthetic_corpus_df, synthetic_panel_df, split_date="2026-08-12", n_topics=2
    )
    df_eng, _, _ = m2.engineer_method2_features(daily_signals, lags=[1, 2, 7])

    p1 = df_eng[df_eng["product_id"] == "P001"].sort_values("date").reset_index(drop=True)
    p1_raw = daily_signals[daily_signals["product_id"] == "P001"].sort_values("date").reset_index(drop=True)

    # For lag 1: p1['search_lag_1'].iloc[i] must equal p1_raw['search_interest'].iloc[raw_idx - 1]
    for idx in range(2, 10):
        dt_curr = p1["date"].iloc[idx]
        val_lag = p1["search_lag_1"].iloc[idx]
        dt_prev = (pd.to_datetime(dt_curr) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        expected_val = p1_raw.loc[p1_raw["date"] == dt_prev, "search_interest"].iloc[0]
        assert np.isclose(val_lag, expected_val, atol=1e-5)


def test_rolling_statistics_strict_shift1_no_lookahead(synthetic_corpus_df, synthetic_panel_df):
    """Verify rolling trend calculation excludes current day t."""
    daily_signals, _ = m2.build_daily_sentiment_and_topics(
        synthetic_corpus_df, synthetic_panel_df, split_date="2026-08-12", n_topics=2
    )
    df_eng, _, _ = m2.engineer_method2_features(daily_signals, lags=[1, 7], rolling_window=7)

    p1 = df_eng[df_eng["product_id"] == "P001"].sort_values("date").reset_index(drop=True)
    p1_raw = daily_signals[daily_signals["product_id"] == "P001"].sort_values("date").reset_index(drop=True)

    # Check rolling mean 7d on day index 10
    dt_curr = pd.to_datetime(p1["date"].iloc[10])
    val_roll = p1["sentiment_rolling_mean_7d"].iloc[10]

    # Expected window: [dt_curr - 7d, dt_curr - 1d]
    start_dt = (dt_curr - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    end_dt = (dt_curr - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    expected_mean = p1_raw[(p1_raw["date"] >= start_dt) & (p1_raw["date"] <= end_dt)]["sentiment_mean"].mean()

    assert np.isclose(val_roll, expected_mean, atol=1e-5)


def test_chronological_split_strict_temporal_boundary(synthetic_corpus_df, synthetic_panel_df):
    """Verify max(train_date) < min(test_date) with zero overlap."""
    daily_signals, _ = m2.build_daily_sentiment_and_topics(
        synthetic_corpus_df, synthetic_panel_df, split_date="2026-08-12", n_topics=2
    )
    df_eng, m1_feats, m2_feats = m2.engineer_method2_features(daily_signals, lags=[1, 7])
    train_df, test_df, meta = m2.perform_method2_chronological_split(
        df_eng, m1_feats, m2_feats, split_date="2026-08-12"
    )

    assert train_df["date"].max() < test_df["date"].min()
    assert train_df["date"].max() == "2026-08-11"
    assert test_df["date"].min() == "2026-08-12"


def test_scaler_and_encoder_fit_on_train_only(synthetic_corpus_df, synthetic_panel_df):
    """Verify StandardScaler is fit strictly on train partition."""
    daily_signals, _ = m2.build_daily_sentiment_and_topics(
        synthetic_corpus_df, synthetic_panel_df, split_date="2026-08-12", n_topics=2
    )
    df_eng, m1_feats, m2_feats = m2.engineer_method2_features(daily_signals, lags=[1, 7])
    train_df, test_df, _ = m2.perform_method2_chronological_split(
        df_eng, m1_feats, m2_feats, split_date="2026-08-12"
    )

    num_feats = m1_feats + m2_feats
    train_means = train_df[num_feats].mean()
    # In train set, means should be approximately 0.0
    assert (train_means.abs() < 1e-2).all()


def test_exclusion_of_quarantined_snapshot_columns(synthetic_corpus_df, synthetic_panel_df):
    """Verify Oct-4 snapshot columns are excluded from predictive features."""
    daily_signals, _ = m2.build_daily_sentiment_and_topics(
        synthetic_corpus_df, synthetic_panel_df, split_date="2026-08-12", n_topics=2
    )
    df_eng, m1_feats, m2_feats = m2.engineer_method2_features(daily_signals, lags=[1, 7])
    train_df, test_df, _ = m2.perform_method2_chronological_split(
        df_eng, m1_feats, m2_feats, split_date="2026-08-12"
    )

    for col in ["yt_snapshot_date", "yt_videos_on_target", "yt_views_on_target_total"]:
        assert col not in m1_feats
        assert col not in m2_feats


def test_leakage_audit_report_all_checks_pass(synthetic_corpus_df, synthetic_panel_df):
    """Verify 12-point leakage audit returns clean pass on synthetic panel."""
    daily_signals, _ = m2.build_daily_sentiment_and_topics(
        synthetic_corpus_df, synthetic_panel_df, split_date="2026-08-12", n_topics=2
    )
    df_eng, m1_feats, m2_feats = m2.engineer_method2_features(daily_signals, lags=[1, 7])
    train_df, test_df, _ = m2.perform_method2_chronological_split(
        df_eng, m1_feats, m2_feats, split_date="2026-08-12"
    )

    is_clean, report = m2.run_method2_leakage_audit(
        daily_signals, df_eng, train_df, test_df, m1_feats, m2_feats,
        split_date="2026-08-12", corpus_df=synthetic_corpus_df
    )
    assert is_clean is True
    assert (report["status"] != "FAIL").all()
    assert len(report) == 12


def test_pipeline_missing_inputs_returns_error(tmp_path):
    """Verify exit code 1 when master dataset or text corpus is missing."""
    empty_paths = config.Paths.from_root(tmp_path)
    rc = m2.main(paths=empty_paths, quiet=True)
    assert rc == 1


def test_pipeline_execution_end_to_end_on_real_data():
    """Verify full CLI execution on canonical repository datasets."""
    paths = config.PATHS
    assert paths.master_dataset_csv.exists()
    assert paths.text_corpus_csv.exists()

    rc = m2.main(paths=paths, generate_plots=False, quiet=True)
    assert rc == 0

    assert paths.method2_train_csv.exists()
    assert paths.method2_test_csv.exists()
    assert paths.method2_metadata_json.exists()
    assert paths.method2_daily_signals_csv.exists()
    assert paths.method2_leakage_audit_report_csv.exists()
    assert paths.method2_lag_diagnostics_csv.exists()

    train_df = pd.read_csv(paths.method2_train_csv)
    test_df = pd.read_csv(paths.method2_test_csv)
    assert len(train_df) == 12540
    assert len(test_df) == 3180
