# Review 1 LaTeX Presentation Guide

**Course:** 23CSE452 Business Analytics — Data Analysis and Predictive Modelling  
**Project:** Viral Product Intelligence: Early Detection & Predictive Modeling of Consumer Demand Surges  
**Team:** Group 13  

---

## Files in this Directory

- `review1_presentation.tex`: Complete, robust LaTeX Beamer presentation source code.
- `figures/`: Local folder containing the high-resolution charts used in the presentation:
  - `eda_full_03_mean_interest_by_type.png` (Macro Trends by Cohort)
  - `eda_full_04_top10_peaks.png` (Top 10 Viral Peaks in 2026)
  - `youtube_views_comments.png` (Video Views vs Comments by Cohort)
- `README.md`: Compilation guide, image upload guide, and slide overview.

---

## 🖼️ Image Files to Upload to Overleaf

To see the real visualization plots rendered in the presentation, drag and drop these 3 image files from your local machine into the root of your Overleaf project:

| Image File Name | Local File Path in Repository | Used on Slide | What It Visualizes |
|:---|:---|:---:|:---|
| **`eda_full_03_mean_interest_by_type.png`** | [`docs/presentation/figures/eda_full_03_mean_interest_by_type.png`](file:///Users/mouniksai/Documents/viral-product-intelligence-/docs/presentation/figures/eda_full_03_mean_interest_by_type.png) | **Slide 5** | Mean daily Google Search Interest over time by cohort (RISING vs STABLE vs DECLINING). |
| **`eda_full_04_top10_peaks.png`** | [`docs/presentation/figures/eda_full_04_top10_peaks.png`](file:///Users/mouniksai/Documents/viral-product-intelligence-/docs/presentation/figures/eda_full_04_top10_peaks.png) | **Slide 6** | Top 10 viral search peaks across all 60 smartphones in 2026. |
| **`youtube_views_comments.png`** | [`docs/presentation/figures/youtube_views_comments.png`](file:///Users/mouniksai/Documents/viral-product-intelligence-/docs/presentation/figures/youtube_views_comments.png) | **Slide 7** | YouTube Total Views vs Comments scatter plot across product cohorts. |

> **Note on Fallback:** The LaTeX code includes `\safeincludeplot`. If you compile before uploading the images, Overleaf will **not crash**; it will display a neat placeholder card instructing which file is needed. Once uploaded, the charts automatically render into the slides.

---

## Slide Structure & Layout

| Slide # | Slide Title | Visual / Content Layout |
|:---:|:---|:---|
| **01** | **Title Slide** | Project Title, Course Code (23CSE452), Review 1, Group 13, Members and Reg Numbers. |
| **02** | **Problem Statement & Research Gap** | 2-column card layout: Lagging POS sales data, stockouts, ARIMA limitations vs Multimodal Radar. |
| **03** | **Dataset Description** | Compact table (60 phones, 16,140 rows, 3,500 videos, 32,215 comments) + Variables card. Zero collision. |
| **04** | **Data Cleaning & Preprocessing** | 2-column card layout: No-forward-fill, principled zeroes, query versioning, One-Hot Encoding, StandardScaler, audit evidence. |
| **05** | **Exploratory Analysis (Finding 1)** | Dedicated slide: `eda_full_03_mean_interest_by_type.png` (56% width) + Finding 1 card (Search leads engagement). |
| **06** | **Exploratory Analysis (Finding 2)** | Dedicated slide: `eda_full_04_top10_peaks.png` (50% width) + Finding 2 card (Viral peak inflection dynamics). |
| **07** | **Exploratory Analysis (Finding 3)** | Dedicated slide: `youtube_views_comments.png` (56% width) + Finding 3 card (Quality, >80% on-target precision). |
| **08** | **Feature Engineering & Dimensions** | Differential formulation bar ($g_t, a_t$) + 2-column layout (Lags, rolling momentum, bimodal separation). |
| **09** | **Predictive Model Development** | 2-column layout: Binary surge classification, LR baseline, Random Forest, HistGBM, chronological 80/20 split. |
| **10** | **Model Evaluation & Interpretation** | Benchmark comparison table (Acc, Prec, Rec, F1, AUC) + Technical interpretation + Business recommendations. |
| **11** | **Project Tracking & Progress** | Compact SCRUM board table (SCRUM-5 to 20, Done statuses) + Quality assurance (124 unit tests, 10 PRs). |
| **12** | **Individual Contributions & Next Steps**| Team member contribution matrix + Review 2 roadmap (Hinglish NLP, TFT/LSTM, Viral Radar dashboard). |

---

## How to Compile in Overleaf

1. Open your project on [Overleaf](https://www.overleaf.com/).
2. Paste the contents of `review1_presentation.tex` into `main.tex`.
3. In Overleaf's file tree on the left, click **Upload** and upload the 3 PNG files from `docs/presentation/figures/`.
4. Click **Recompile** (or `Ctrl+Enter` / `Cmd+Enter`).
