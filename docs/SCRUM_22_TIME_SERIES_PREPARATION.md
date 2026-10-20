# SCRUM-22: Chronological Split & Time-Series Data Prep

**Work Item:** SCRUM-22 (Sprint 1 / Review 2 Preparation)  
**Status:** Completed  
**Objective:** Isolate time-series variables from the SCRUM-21 master dataset, perform rigorous time-index validation, conduct stationarity (ADF, KPSS) and autocorrelation diagnostics, and construct leak-free chronological train/test splits for Method 1 forecasting and demand surge evaluation.

---

## 1. Executive Summary

SCRUM-22 establishes the formal time-series preparation pipeline for the Viral Product Intelligence platform. Building directly on the validated master dataset produced in SCRUM-21 (`processed/master_dataset.csv`), this module implements:
1. **Time-Index Integrity Verification:** Audits timestamp parsing, monotonicity, uniform daily frequency, and calendar continuity across all 60 smartphone models over 269 days.
2. **Leakage-Free Feature Engineering:** Isolates historical signals (`search_interest`, `videos_published`, `comments_published`) and constructs lagged indicators ($t-1, t-2, t-3, t-7$) and 7-day rolling volatility statistics ($\mu_{7d}, \sigma_{7d}$) using strict backwards-looking shifts (`shift(1).rolling(7)`).
3. **Point-in-Time Leakage Elimination:** Systematically quarantines and excludes post-event operational flags and point-in-time snapshot columns (`yt_*`, collected on 2026-10-04) from the predictive feature matrix.
4. **Chronological Evaluation Split:** Enforces an 80/20 chronological partition with a global temporal boundary at `2026-08-12`, fitting all scalers and encoders exclusively on training observations.
5. **Stationarity & Autocorrelation Diagnostics:** Executes per-product Augmented Dickey-Fuller (ADF) and KPSS statistical tests on both raw levels and first differences, handling unreleased/zero-variance edge cases gracefully.

---

## 2. Input Dataset & Canonical Schema

The pipeline consumes the canonical master dataset produced by SCRUM-21:
- **Location:** `processed/master_dataset.csv`
- **Dimensions:** 16,140 rows × 43 columns (60 products × 269 contiguous days)
- **Time Window:** 2026-01-08 to 2026-10-03
- **Primary Grouping Entity:** `product_id` (P001 to P060)
- **Primary Timestamp:** `date` (ISO-8601 string `YYYY-MM-DD`, India Standard Time `Asia/Kolkata`)

### Variable Classification & Isolation Matrix

| Category | Columns | Role in SCRUM-22 | Handling / Treatment |
|---|---|---|---|
| **Identifiers** | `product_id`, `product_name`, `date` | Record identification & temporal sorting | Preserved in output splits for traceability; excluded from predictive features. |
| **Static Attributes** | `brand`, `product_type`, `category` | Product cohort categorization | `category` dropped (constant). `brand` (13 brands) & `product_type` (RISING, STABLE, DECLINING) one-hot encoded fitting on train partition only. |
| **Continuous Target** | `search_interest` (0–100) | Method 1 continuous demand forecasting target | Preserved unscaled in train and test splits; evaluated in stationarity diagnostics. |
| **Classification Target** | `surge_label` (0 or 1) | Method 1 viral demand surge alert target | Binary target (85th percentile of positive growth). Kept unscaled as classification ground truth. |
| **Historical Signals** | `videos_published`, `comments_published`, `search_interest_growth`, `search_interest_accel` | Time-varying predictors | Shifted backwards into leak-free lags ($t-1, t-2, t-3, t-7$). Contemporaneous values excluded. |
| **Burn-In Rolling Stats** | `rolling_mean_7d`, `rolling_std_7d` | Trend momentum & volatility | Formulated as `shift(1).rolling(7)`. Initial 7-day window dropped as burn-in period. |
| **Quarantined Leakage** | `yt_snapshot_date`, `yt_videos_on_target`, `yt_views_on_target_total`, `yt_on_target_share`, `yt_low_on_target_share`, `yt_videos_7d_on_target`, `usable_product` | Snapshot metadata (captured 2026-10-04) | **EXCLUDED** from predictive features. Using October 4 values across Jan–Oct 2026 represents severe lookahead leakage. |
| **Operational Flags** | `trends_observed`, `is_zero`, `low_signal_product`, `youtube_observed`, `result_cap_hit`, etc. | Data pipeline provenance flags | **EXCLUDED** from predictive model inputs. |

---

## 3. Time-Index Validation Findings

The pipeline executes 12 automated integrity checks against `processed/master_dataset.csv`. All checks pass with zero defects:

```
[Validation] Audited 12 integrity checks -> processed/time_series_validation_report.csv
  [PASS] dataset_not_empty: 16,140 rows, 43 columns
  [PASS] required_columns_present: Missing: none
  [PASS] timestamp_parsing: Dates parsed successfully (2026-01-08 to 2026-10-03)
  [PASS] product_count: 60 distinct products identified
  [PASS] no_duplicate_timestamps: 0 duplicate (product_id, date) pairs
  [PASS] chronological_order: Dates are monotonic increasing within each product
  [INFO] sampling_frequency: Uniform daily sampling frequency verified across 269 calendar days
  [PASS] calendar_continuity: All 60 products have complete contiguous history
  [PASS] expected_row_count: 16,140 rows == 60 products × 269 days
  [PASS] search_interest_valid: Search interest in [0, 100]; min=0.0, max=100.0, nulls=0
  [PASS] surge_label_valid: Surge labels in {0, 1}; 806 surge days (4.99% base rate)
  [INFO] null_value_audit: Unfilled values in required columns: {'search_interest_growth': 3688, 'search_interest_accel': 4060}. Note: growth/accel unobserved on day 1 or zero-days by design
```

### Key Quality Findings:
- **Calendar Continuity:** Zero missing dates across all 60 products over the 269-day window.
- **Sampling Frequency:** Strictly uniform daily intervals (`1 days`).
- **Target Distribution:** 806 surge days across 16,140 observations yields an empirical base rate of **4.99%**, establishing a realistic class-imbalance challenge for Review 2 predictive modeling.

---

## 4. Chronological Train / Test Split Strategy

### Partition Design
To evaluate future out-of-sample generalization across the smartphone market, the dataset is partitioned using a **common global chronological cutoff** across all 60 products:
- **Global Split Cutoff Date:** `2026-08-12`
- **Burn-In Period Dropped:** 2026-01-08 to 2026-01-14 (initial 7 days required for $t-7$ lag and 7-day rolling window initialization = 420 rows dropped).
- **Valid Modeling Population:** 15,720 rows across 262 active modeling days.

### Partition Metrics

| Metric | Training Partition | Held-Out Test Partition | Full Modeling Population |
|---|---|---|---|
| **Date Range** | `2026-01-15` to `2026-08-11` | `2026-08-12` to `2026-10-03` | `2026-01-15` to `2026-10-03` |
| **Calendar Days** | 209 days (79.8%) | 53 days (20.2%) | 262 days (100.0%) |
| **Observation Rows** | 12,540 rows | 3,180 rows | 15,720 rows |
| **Surge Events ($y=1$)** | 586 surges | 208 surges | 794 surges |
| **Surge Base Rate** | 4.67% | 6.54% | 5.05% |
| **Test Horizon** | — | **53 days (~7.5 weeks)** | — |

### Leakage Prevention Guarantees:
1. **Strict Temporal Partitioning:** $\max(\text{train\_date}) = \text{2026-08-11} < \min(\text{test\_date}) = \text{2026-08-12}$. Zero test observations exist on or before the training cutoff.
2. **Train-Only Scaling:** Continuous feature scaling (`StandardScaler`) is fitted strictly on the 12,540 training rows. The learned sample mean ($\mu_{\text{train}}$) and standard deviation ($\sigma_{\text{train}}$) are then applied to normalize the test partition.
3. **Train-Only Categorical Encoding:** One-hot dummy encoding columns for `brand` (13 brands) and `product_type` (3 cohorts) are fitted on training data (`drop_first=True`), preventing category leakage.

---

## 5. Stationarity and Lag Diagnostics

Statistical diagnostics were conducted on all 60 products using training observations ($N=209$ days/product) to inform model architecture and feature selection:

### Augmented Dickey-Fuller (ADF) Test
- **Raw Levels (`search_interest`):** **55.0%** of products reject the unit root null hypothesis at $\alpha = 0.05$. Because established models exhibit mean-reverting baseline chatter, while viral rising products exhibit non-stationary upward drift, raw search interest cannot be assumed stationary across the entire panel.
- **First Differences ($\Delta \text{search\_interest}$):** **95.0%** of products achieve stationarity ($p < 0.001$), confirming that the underlying consumer demand series is predominantly integrated of order one, $I(1)$.

### Kwiatkowski-Phillips-Schmidt-Shin (KPSS) Test
- **Trend/Level Stationarity:** KPSS tests confirm that differenced series achieve level stationarity without residual deterministic drift across $>90\%$ of active models.

### Degenerate Series Edge Case Handling
- **Product P003 (Apple iPhone Duo):** An upcoming unannounced concept device with zero search volume during the early months of 2026 ($S_t = 0.0, \sigma^2 = 0$).
- **Handling:** Automatically detected as `constant_zero_variance` and safely recorded as `SKIPPED` in `processed/stationarity_report.csv`, avoiding numerical division-by-zero crashes.

### Autocorrelation (ACF) & Lag Structure
Autocorrelations and cross-correlations with demand surges were evaluated for lags $k \in \{1, 2, 3, 7, 14\}$:

| Lag Horizon | Mean Autocorrelation ($\rho_k$) | Mean Cross-Correlation with Surge | Rationale for Feature Inclusion |
|---|---|---|---|
| **$t-1$** | **0.5512** | **0.2140** | Captures immediate day-over-day search momentum. |
| **$t-2$** | **0.4320** | **0.1650** | Captures 48-hour velocity decay. |
| **$t-3$** | **0.3780** | **0.1380** | Multi-day persistence tracking. |
| **$t-7$** | **0.2845** | **0.1090** | Weekly seasonality and weekend interest cadence. |
| **$t-14$** | 0.2010 | 0.0760 | Long-range baseline indicator (evaluated as benchmark). |

---

## 6. Generated Artifact Inventory

All generated artifacts adhere strictly to the repository architecture:

| Artifact Path | Format | Dimensions | Purpose |
|---|---|---|---|
| `processed/train.csv` | CSV | 12,540 rows × 80 cols | Normalized training partition with leak-free lags and dummy encodings. |
| `processed/test.csv` | CSV | 3,180 rows × 80 cols | Held-out out-of-sample test partition for Review 2 model evaluation. |
| `processed/split_metadata.json` | JSON | 124 lines | Complete machine-readable audit trail of partition parameters and feature sets. |
| `processed/time_series_validation_report.csv` | CSV | 12 checks | Pass/fail status of all time-index validation criteria. |
| `processed/stationarity_report.csv` | CSV | 120 test rows | Product-level ADF and KPSS statistics for raw and differenced series. |
| `processed/lag_diagnostics.csv` | CSV | 300 rows | Autocorrelation and surge cross-correlation metrics across 5 lag horizons. |
| `docs/figures/ts_target_behaviour.png` | PNG | 1200 × 1000 px | Comparative target trajectories across Rising, Stable, and Declining cohorts. |
| `docs/figures/ts_temporal_split.png` | PNG | 1000 × 500 px | Visual timeline of the 80/20 chronological train/test split. |
| `docs/figures/ts_lag_relationships.png` | PNG | 800 × 450 px | Bar chart comparing ACF persistence and surge correlation across lag horizons. |

---

## 7. Reproduction Commands

To reproduce the time-series preparation pipeline and regenerate all splits and diagnostic plots:

```bash
# Execute end-to-end time series prep (default 80/20 split)
python3 src/prep_time_series.py

# Specify an explicit calendar cutoff date
python3 src/prep_time_series.py --split-date 2026-08-12

# Execute without rendering visualization figures
python3 src/prep_time_series.py --no-plots

# Run the complete test suite (143 unit tests)
python3 -m pytest tests/test_prep_time_series.py -v
python3 -m pytest
```

---

## 8. Automated Test Coverage

A dedicated test suite in `tests/test_prep_time_series.py` contains 16 automated tests verifying all functional requirements:

1. `test_validation_passes_on_clean_panel`: Validates synthetic panel integrity.
2. `test_validation_detects_duplicate_timestamps`: Fails on duplicate `(product_id, date)` keys.
3. `test_validation_detects_missing_dates`: Fails on calendar gaps.
4. `test_validation_detects_missing_required_columns`: Fails on missing input fields.
5. `test_validation_detects_invalid_search_interest`: Validates bounds $[0, 100]$.
6. `test_validation_detects_invalid_surge_labels`: Validates binary target $\{0, 1\}$.
7. `test_feature_engineering_lags_and_rolling`: Verifies numerical lag values match shifted source.
8. `test_rolling_statistics_strict_shift_no_leakage`: Confirms rolling window excludes current observation.
9. `test_chronological_split_strict_temporal_boundary`: Confirms $\max(\text{train}) < \min(\text{test})$.
10. `test_scaler_and_encoder_fit_on_train_only`: Verifies train normalization properties.
11. `test_exclusion_of_leakage_columns`: Asserts snapshot and future columns are excluded from features.
12. `test_stationarity_diagnostics_normal_series`: Verifies ADF test on AR(1) stationary series.
13. `test_stationarity_handles_constant_zero_variance`: Verifies safe skipping of zero-variance series.
14. `test_stationarity_handles_insufficient_sample_size`: Verifies safe skipping of short series.
15. `test_pipeline_missing_master_file_returns_error`: Confirms error code on missing inputs.
16. `test_pipeline_execution_on_master_dataset`: Executes end-to-end on canonical production data.

**Test Suite Results:**
- `tests/test_prep_time_series.py`: **16 passed in 4.08s**
- Entire Project Test Suite: **143 passed in 6.50s (100% pass rate)**
