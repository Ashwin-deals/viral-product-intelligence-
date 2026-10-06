# Review 1 LaTeX Presentation Guide

**Course:** 23CSE452 Business Analytics — Data Analysis and Predictive Modelling  
**Project:** Viral Product Intelligence: Early Detection & Predictive Modeling of Consumer Demand Surges  
**Team:** Group 13  

---

## Files in this Directory

- `review1_presentation.tex`: Complete LaTeX Beamer presentation source code (10 slides matching the Review 1 rubric).
- `README.md`: Compilation guide, structure documentation, and slide overview.

---

## Slide Structure (100% Aligned with Review 1 Rubric)

| Slide # | Slide Title | Core Rubric Elements Addressed |
|:---:|:---|:---|
| **01** | **Title Slide** | Project Title, Course Code (23CSE452), Review 1, Team (Group 13), Members and Register Numbers. |
| **02** | **Problem Statement & Research Gap** | Lagging POS sales data, stockout risk, limitations of ARIMA/manual monitoring, multimodal radar approach, business objectives. |
| **03** | **Dataset Description** | Sources (Google Trends, YouTube Data API), dataset size (60 phones, 16,140 rows, 3,500 videos, 32,215 comments), key features, target variable (`surge_label`). |
| **04** | **Data Cleaning & Preprocessing** | No-forward-fill policy, principled zero imputation, query version control, exact title matching (>80% accuracy), One-Hot Encoding, StandardScaler, before/after evidence. |
| **05** | **Exploratory Analysis & Visualization** | Finding 1 (Search leads engagement by 7–14 days), Finding 2 (On-target specificity isolates true demand), Finding 3 (Cohort dynamics across Rising, Stable, Declining). |
| **06** | **Feature Engineering & Dimensionality Reduction** | Mathematical formulation of velocity ($g_t$) and acceleration ($a_t$), surge label percentile threshold, temporal lags (1, 2, 3, 7 days) and rolling volatility to prevent leakage. |
| **07** | **Predictive Model Development** | Binary classification task, Logistic Regression baseline, Random Forest, HistGradientBoosting (GBDT), chronological 80/20 train/test split, class imbalance weighting. |
| **08** | **Model Evaluation & Result Interpretation** | Benchmark comparison table (Accuracy, Precision, Recall, F1, ROC-AUC), tree superiority (+19% AUC), top features, cost-sensitive thresholding for retail stockouts. |
| **09** | **Project Tracking & Progress** | Jira SCRUM board tracking (SCRUM-5 through SCRUM-20), workflow stages, task assignees and statuses, 10 merged PRs, 124 passing unit tests. |
| **10** | **Individual Contributions & Next Steps** | Individual member contributions with evidence, Review 2 roadmap (Hinglish sentiment, TFT/LSTM, Viral Product Radar dashboard), deliverable links. |

---

## How to Compile

### Option 1: Overleaf (Recommended)
1. Open [Overleaf](https://www.overleaf.com/).
2. Create a **New Project** $\rightarrow$ **Upload Project** (or **Blank Project**).
3. Copy and paste the contents of `review1_presentation.tex` into `main.tex`.
4. Click **Recompile**.
5. The PDF will compile cleanly with zero external asset dependencies.

### Option 2: Local Command Line (macOS / Linux / Windows)
If TeX Live or MacTeX is installed:
```bash
pdflatex docs/presentation/review1_presentation.tex
pdflatex docs/presentation/review1_presentation.tex
```
*(Run twice to ensure cross-references and page numbers resolve properly).*

### Option 3: Visual Studio Code
1. Install the **LaTeX Workshop** extension.
2. Open `review1_presentation.tex`.
3. Press `Cmd+Alt+B` (macOS) or `Ctrl+Alt+B` (Windows) to build the PDF.
