# SCRUM-26: Time-Series Forecasting Model Implementation (Method 1)

**Work Item:** SCRUM-26 (Sprint 26 / Method 1 Benchmark & Predictive Forecasting)  
**Status:** Completed  
**Objective:** Implement Time-Series Analysis (Method 1) on validated product search interest. Train baseline and advanced time-series forecasting models (Naive Persistence, Seasonal Naive, Rolling Mean, ARIMA, Exponential Smoothing, Ridge, ElasticNet, Random Forest, Gradient Boosting, and LightGBM) on the held-out chronological split (`processed/train.csv` and `processed/test.csv`).

---

## 1. Executive Summary

SCRUM-26 delivers the formal Time-Series Forecasting Benchmark (Method 1) for the Viral Product Intelligence platform. Building directly on the leak-free chronological partitions and stationarity diagnostics established in SCRUM-22, this module implements:
1. **Univariate & Statistical Baselines:** Evaluates Naive Persistence ($\hat{y}_t = y_{t-1}$), 7-day Seasonal Naive ($\hat{y}_t = y_{t-7}$), 7-day Rolling Mean, per-product ARIMA(1,1,1), and Holt-Winters Exponential Smoothing.
2. **Lagged Machine Learning Regressors:** Evaluates Ridge Regression, ElasticNet, Random Forest, Gradient Boosting, and LightGBM on the 36 leak-free lag and rolling features.
3. **Out-of-Sample Chronological Evaluation:** Evaluates models across the 53-day held-out test window (`2026-08-12` to `2026-10-03`, 3,180 observations across all 60 products).
4. **Cohort Life-Cycle Diagnostics:** Disaggregates forecast error across RISING (24 models), STABLE (24 models), and DECLINING (12 models) cohorts, demonstrating model resilience under non-stationary demand spikes.
5. **Feature Importance Decomposition:** Unpacks the relative predictive contributions of short-term lags ($t-1, t-2$), weekly seasonality ($t-7$), and 7-day volatility.

---

## 2. Problem Formulation & Chronological Partition

### Target Variable
- **Target:** `search_interest` (continuous 0–100 scale from Google Trends).
- **Task:** 1-step-ahead dynamic daily forecasting across a held-out 53-day forward horizon.

### Evaluation Partition
- **Train Window:** `2026-01-15` to `2026-08-11` (209 days × 60 products = 12,540 rows).
- **Held-Out Test Window:** `2026-08-12` to `2026-10-03` (53 days × 60 products = 3,180 rows).
- **Global Cutoff Date:** `2026-08-12` (strict boundary: $\max(\text{train}) < \min(\text{test})$).
- **Burn-In Dropped:** Initial 7 days (`2026-01-08` to `2026-01-14`) dropped to initialize $t-7$ lags and 7-day rolling statistics.

---

## 3. Evaluated Model Architectures

### 3.1 Baselines
1. **Naive Persistence ($t-1$):**
   $$\hat{y}_{p,t} = y_{p,t-1}$$
   The standard benchmark in daily demand forecasting.
2. **Seasonal Naive ($t-7$):**
   $$\hat{y}_{p,t} = y_{p,t-7}$$
   Captures weekend and day-of-week demand seasonality.
3. **7-Day Rolling Mean:**
   $$\hat{y}_{p,t} = \frac{1}{7} \sum_{i=1}^7 y_{p,t-i}$$

### 3.2 Statistical Time-Series Models
4. **Per-Product ARIMA(1,1,1):**
   $$(1 - \phi_1 B)(1 - B) y_t = c + (1 + \theta_1 B) \epsilon_t$$
   Fitted individually per product on training observations ($N=209$). Verified stationary on first differences in SCRUM-22 ($95\%$ of products $I(1)$ integrated).
5. **Exponential Smoothing (Holt's Linear Trend):**
   Additive level and trend smoothing updated dynamically across the test split.

### 3.3 Lagged Machine Learning Regressors
Models trained on 36 leak-free predictors:
- Temporal lags ($t-1, t-2, t-3, t-7$) for `search_interest`, `growth`, `accel`, `videos_published`, and `comments_published`.
- 7-day rolling statistics: $\mu_{7d}$ and $\sigma_{7d}$ (`shift(1).rolling(7)`).
- One-hot categorical dummies: `brand_*` (13 brands) and `product_type_*` (3 cohorts).

6. **Ridge Regression:** $L_2$ regularized linear model ($\alpha=10.0$).
7. **ElasticNet:** $L_1 + L_2$ regularized linear model ($\alpha=0.1, \rho=0.5$).
8. **Random Forest Regressor:** 100 trees, $\text{max\_depth}=6$.
9. **Gradient Boosting Regressor:** 100 sequential boosting stages, learning rate $= 0.1$, $\text{max\_depth}=4$.
10. **LightGBM Regressor:** Histogram-based gradient boosting trees with leaf-wise splitting ($\text{learning\_rate}=0.05$).

---

## 4. Benchmark Performance Results

Performance evaluated on the 3,180 held-out test rows across all 60 products:

| Model | Model Family | Test MAE | Test RMSE | Test WAPE (%) | Test $R^2$ | Test MDA (%) |
|---|---|---|---|---|---|---|
| **LightGBM** | Machine Learning | **5.177** | **10.047** | **21.53%** | **0.7344** | **67.8%** |
| **Gradient Boosting** | Machine Learning | 5.288 | 10.200 | 21.99% | 0.7263 | 66.4% |
| **Random Forest** | Machine Learning | 5.294 | 10.108 | 22.01% | 0.7312 | 65.9% |
| **Ridge Regression** | Machine Learning | 5.304 | 10.349 | 22.05% | 0.7182 | 65.2% |
| **ElasticNet** | Machine Learning | 5.363 | 10.406 | 22.30% | 0.7151 | 64.8% |
| **Naive Persistence ($t-1$)** | Baseline | 5.544 | 11.204 | 23.05% | 0.6697 | 50.0% |
| **Exponential Smoothing** | Statistical | 5.601 | 11.291 | 23.29% | 0.6646 | 54.1% |
| **ARIMA(1,1,1)** | Statistical | 6.002 | 11.763 | 24.95% | 0.6360 | 52.8% |
| **Rolling Mean (7d)** | Baseline | 6.293 | 11.839 | 26.17% | 0.6313 | 51.5% |
| **Seasonal Naive ($t-7$)** | Baseline | 8.995 | 16.783 | 37.40% | 0.2590 | 48.2% |

### Key Benchmark Findings:
1. **Machine Learning Dominance:** All regularized linear and ensemble ML models comfortably outperform statistical ARIMA and Naive baselines. LightGBM achieves the lowest test MAE of **5.177** and highest $R^2$ of **0.7344**, reducing error by **6.6%** over Naive persistence and **13.7%** over ARIMA.
2. **Mean Directional Accuracy:** LightGBM achieves **67.8%** directional accuracy, accurately forecasting whether demand will rise or fall tomorrow.
3. **Statistical Model Limitations:** Pure univariate ARIMA struggles with multi-day viral spikes because it lacks cross-modal signals (YouTube review activity counts) and cross-product panel transfer learning.

---

## 5. Cohort-Level Performance Breakdown

Evaluating the champion model (LightGBM) across life-cycle cohorts:

| Cohort | Products | Sample Rows | LightGBM MAE | Naive MAE | MAE Error Reduction (%) | LightGBM $R^2$ |
|---|---|---|---|---|---|---|
| **STABLE** | 24 | 1,272 | **4.458** | 5.018 | **-11.2%** | **0.785** |
| **DECLINING** | 12 | 636 | **5.564** | 6.050 | **-8.0%** | **0.708** |
| **RISING** | 24 | 1,272 | **5.702** | 5.816 | **-2.0%** | **0.627** |

### Insights:
- **STABLE Products:** Exhibit regular mean-reversion around steady-state baseline search volume, yielding the highest forecast accuracy ($R^2 = 0.785, \text{MAE} = 4.458$).
- **RISING Products:** Viral demand surges and sudden influencer review spikes introduce sudden non-linear shifts. While more difficult to forecast ($R^2 = 0.627$), Gradient Boosting and LightGBM avoid catastrophic overshoot errors common to autoregressive models.

---

## 6. Feature Importance Decomposition

Decomposition of Gradient Boosting and Random Forest feature importances across the 36 predictors:

| Rank | Feature Name | Category | GBR Importance (%) | RF Importance (%) | Role in Demand Prediction |
|---|---|---|---|---|---|
| **1** | `search_lag_1` | Temporal Lag | **60.9%** | **76.6%** | Immediate 24-hour demand persistence. |
| **2** | `rolling_mean_7d` | Rolling Volatility | **33.8%** | **20.2%** | Medium-term baseline momentum. |
| **3** | `search_lag_2` | Temporal Lag | **1.2%** | **0.6%** | 48-hour velocity decay. |
| **4** | `search_lag_3` | Temporal Lag | **0.6%** | **0.3%** | Multi-day persistence tracking. |
| **5** | `rolling_std_7d` | Rolling Volatility | **0.6%** | **0.3%** | Volatility clustering indicator. |
| **6** | `accel_lag_1` | Velocity Derivative | **0.4%** | **0.2%** | Rate-of-change inflection. |
| **7** | `growth_lag_1` | Velocity Derivative | **0.3%** | **0.2%** | Day-over-day growth velocity. |
| **8** | `comments_published_lag_1`| YouTube Modality | **0.3%** | **0.1%** | Social comment volume activity. |
| **9** | `videos_published_lag_1` | YouTube Modality | **0.2%** | **0.1%** | Review upload activity. |
| **10** | `brand_Samsung` | Categorical Dummy | **0.2%** | **0.1%** | Brand baseline volume calibration. |

---

## 7. Generated Artifact Inventory

All generated artifacts adhere strictly to repository conventions:

| Artifact Path | Format | Dimensions | Purpose |
|---|---|---|---|
| `processed/method1_model_comparison.csv` | CSV | 10 models × 7 cols | Complete benchmark evaluation table (MAE, RMSE, WAPE, $R^2$, MDA). |
| `processed/method1_forecast_predictions.csv` | CSV | 3,180 rows × 17 cols | Out-of-sample held-out test predictions for all 60 products across 53 days. |
| `processed/method1_cohort_performance.csv` | CSV | 3 cohorts × 10 cols | Cohort-level performance breakdown across Rising, Stable, Declining cohorts. |
| `processed/method1_feature_importance.csv` | CSV | 36 features × 6 cols | Feature importance and coefficient rankings across tree and linear models. |
| `docs/figures/method1_model_benchmark.png` | PNG | 2100 × 825 px | Comparative bar chart of test MAE and $R^2$ across all 10 models. |
| `docs/figures/method1_forecast_trajectories.png` | PNG | 1950 × 1350 px | 53-day forecast trajectories vs actuals for representative products. |
| `docs/figures/method1_feature_importance.png` | PNG | 1500 × 750 px | Top 10 temporal predictor importances. |
| `docs/figures/method1_cohort_comparison.png` | PNG | 1200 × 675 px | Error reduction across product cohorts vs Naive persistence. |
| `notebooks/scrum26_time_series_forecasting.ipynb` | Jupyter | Executed notebook | Interactive walkthrough and verification notebook. |

---

## 8. Reproduction Instructions

To reproduce the Method 1 forecasting benchmark and regenerate all tables and figures:

```bash
# Execute end-to-end Method 1 forecasting benchmark
python3 src/forecast_method1.py

# Execute without rendering visualization figures
python3 src/forecast_method1.py --no-plots

# Run the complete test suite (179 unit tests, 100% pass rate)
python3 -m pytest tests/test_forecast_method1.py -v
python3 -m pytest
```
