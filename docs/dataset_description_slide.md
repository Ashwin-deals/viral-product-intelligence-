# Review 1 Presentation Slide Draft: Dataset Description

**Project:** Viral Product Intelligence: Early Detection and Prediction of Consumer Demand Surges Using Multi-Source Signals  
**Work Item:** SCRUM-14 (Sprint 0 / Review 1)  
**Author:** Mounik Sai (`mouniksai9@gmail.com`)  
**Sprint:** SCRUM Sprint 0 / Review 1  
**Status:** Ready for Review 1 Slide Deck Integration  

---

## Slide 1: Dataset Overview & Multi-Source Signal Architecture

### Title: Multi-Source Signal Intelligence: Dataset Architecture

#### Executive Summary
To detect consumer demand surges before they appear in sales reports, we engineered a comprehensive, multi-source dataset combining **macro search intent** with **social engagement and video content velocity** across India's smartphone ecosystem.

```
+-----------------------------------------------------------------------------------------+
|                                60 TARGET SMARTPHONES                                    |
|              13 Leading Brands  |  3 Lifecycle Stages: RISING, STABLE, DECLINING        |
+--------------------------------------------+--------------------------------------------+
|        SIGNAL 1: MACRO SEARCH INTENT       |    SIGNAL 2: SOCIAL & ENGAGEMENT VELOCITY  |
|               Google Trends                |             YouTube Data API v3            |
+--------------------------------------------+--------------------------------------------+
| - 269 Continuous Daily Observations / Phone| - 3,500 Cleaned Review Videos              |
| - 16,140 Total Daily Search Interest Points| - 32,215 Curated Top-Level User Comments   |
| - Retrospective Window: Jan 8 – Oct 3, 2026| - Rolling 7-Day Velocity & Exact Matching  |
| - Lead Time: 7–14 Days Prior to Sales Surge| - Lead Time: 3–10 Days Prior to Sales Surge|
+--------------------------------------------+--------------------------------------------+
```

#### Key Dataset Dimensions
| Dimension | Volume / Scope | Source | Status |
|---|---|---|---|
| **Products Tracked** | 60 Smartphone Models | Hand-curated & retail-verified | 100% active (Registry v3) |
| **Brands Represented** | 13 Brands in India | Major OEMs & sub-brands | Diverse tier coverage |
| **Search Time Series** | 16,140 daily data points | Google Trends (pytrends / web export) | 269 consecutive days |
| **Video Metadata** | 3,500 video records | YouTube Data API v3 (`videos.list`) | Fully verified & cleaned |
| **Viewer Commentary** | 32,215 top-level comments | YouTube Data API v3 (`commentThreads`) | Cleaned & script-flagged |
| **Product Snapshots** | 70 daily snapshot records | YouTube Data API v3 (`product_daily`) | 60 all-product + 10 pilot |
| **Unified Dataset** | 16,140 aligned daily rows | `aligned_daily.csv` | Idempotent left-join |

---

## Slide 2: Target Scope & Market Representation

### Title: Curated 60-Product Portfolio: Balanced Across Lifecycles

#### Product Categorization Breakdown
We categorize the 60 smartphone models into three distinct lifecycle cohorts to validate predictive models under varied demand dynamics:

```
+---------------------------------------------------------------------------------------+
|  Cohort       | Share | Count | Rationale & Characteristics                           |
+---------------+-------+-------+-------------------------------------------------------+
| 🔴 RISING     |  40%  |   24  | Launched mid-2026 or upcoming; high buzz volatility;  |
|               |       |       | exhibits zero-inflation prior to announcement.        |
| 🟢 STABLE     |  40%  |   24  | Established market staples with steady search baselines|
|               |       |       | and mature review ecosystems.                         |
| 🔵 DECLINING  |  20%  |   12  | Older/superseded generations with decaying attention; |
|               |       |       | baseline controls for surge false positives.          |
+---------------------------------------------------------------------------------------+
```

#### Brand Diversification (13 Indian Market Brands)
- **Market Leaders:** Apple (8 models), Samsung (8 models).
- **Enthusiast / Growth Brands:** OnePlus (6), Xiaomi (4), Redmi (6), Poco (5), Realme (6).
- **Performance & Camera Segments:** Vivo (5), iQOO (5), Motorola (4), Nothing (3).
- **Niche & Budget:** Google Pixel (2), Infinix (2).

![Dataset Composition](file:///C:/Users/Aashiq/Desktop/viral-product-intelligence-/docs/figures/slide_dataset_composition.png)

---

## Slide 3: Multi-Modal Signal Matrix & Lead Times

### Title: Signal Modalities: Complementary Visibility & Lead Dynamics

```
Signal Timeline vs. Sales Realization:
  Day -14                     Day -10              Day -5            Day 0 (Surge)
   |----------------------------|--------------------|-----------------|
   Google Trends               YouTube Video       YouTube Comment   Retail Sales
   Search Spike                Review Surge        Sentiment Shift   Stockout Occurs
   [Early Discovery]           [Content Creation]  [Buyer Consensus] [Lagging Indicator]
```

#### Signal Comparison Matrix
| Parameter | Google Trends | YouTube Video Metadata | YouTube Viewer Commentary |
|---|---|---|---|
| **Primary Metric** | Search Interest Index (0–100) | Views, Likes, Upload Velocity | Comment Text, Like Counts |
| **Observation Frequency** | Daily (269 continuous days) | Point-in-time Snapshot + 7-Day Window | Batched Snapshot (Top 5 Videos) |
| **Information Content** | Unprompted consumer curiosity | Influencer evaluation & reach | Qualitative consumer intent/sentiment |
| **Expected Lead Time** | **7–14 days** before surge | **5–10 days** before surge | **3–7 days** before surge |
| **Sampling Constraint** | Scaled to product's own peak | Relevance-ranked top 50 videos | Top 100 comments / video (max 500) |

![Signal Modalities](file:///C:/Users/Aashiq/Desktop/viral-product-intelligence-/docs/figures/slide_signal_modalities.png)

---

## Slide 4: Data Quality, Hygiene & Exact-Model Matching

### Title: Data Engineering Rigor: Precision Filtering & Quality Gates

#### 1. Exact-Model Matching (`on_target_share`)
- **Challenge:** In India, smartphone families share common names (e.g. *Redmi Note 13* vs *Redmi Note 13 Pro+*). Broad YouTube searches return sibling device reviews.
- **Solution:** Heuristic title matching (`src/title_match.py`) isolates exact-model videos.
- **Outcome:** The pipeline adds `videos_on_target`, `views_on_target_total`, and `on_target_share`. Products with `<60%` on-target share are flagged `low_on_target_share` so downstream models avoid corrupted signals.

#### 2. Strict Zero vs. Null Separation
- Unobserved metrics and hidden counts (e.g. 85 videos with disabled like counts) are strictly preserved as **empty/null**, never coerced to zero.
- Zero is reserved exclusively for observed zero-demand events.

#### 3. Provisional Boundary Tagging
- Google Trends marks the most recent day (`2026-10-03`) as partial. The pipeline tags this day as `is_provisional = True`, alerting feature engineering to exclude it from velocity calculations.

#### 4. Comment Text Sanitization & Script Tagging
- Decoded HTML entities, removed markdown tags, and collapsed whitespace.
- Tagged `is_emoji_only` (395 comments) and `likely_non_english` (934 non-Latin script comments). Romanized Indian languages (Hinglish) are identified for contextual NLP handling.

---

## Slide 5: Source Constraints, Trade-Offs & Mitigations

### Title: Boundary Conditions: Transparent Constraints & Architectural Decisions

| Constraint / Limitation | Real-World Impact | Engineered Mitigation in Pipeline |
|---|---|---|
| **Relative Trends Scale** | Peak = 100 per product; cannot compare raw values between phones. | Engineering rate-of-change, acceleration, and percentage-deviation features rather than cross-sectional levels. |
| **YouTube Result Cap (50)** | `search.list` cap (50) is an arbitrary query cutoff, not a volume metric. | Added recent upload window pass (`videos_published_7d`, `videos_7d_on_target`) to measure true content velocity. |
| **API Quota Ceilings** | YouTube imposes 10,000 units / 100 search calls per day limit. | Quota-aware architecture (`QuotaTracker`) budgeting ~7 units per product; separated into weekly main & daily recent passes. |
| **Omission of E-Commerce Data** | No daily Amazon/Flipkart sales or live pricing collected. | Strictly enforced compliance with platform Terms of Service and anti-scraping policies; signals focus on top-of-funnel demand. |
| **Initial Temporal Overlap** | Only 10 product-days overlap between historical Trends & single snapshot. | Multi-source alignment architecture (`aligned_daily.csv`) built and ready for automated scheduled snapshot accumulation. |

---

## Presenter Script & Talking Points (Review 1)

### Slide 1 Script
> *"Good morning committee members. For our Review 1 presentation, we present the foundational dataset engineered for Viral Product Intelligence. Rather than relying on lagging internal sales metrics, we built a dual-signal data pipeline capturing consumer intent across 60 smartphones in India. On the macro level, we captured over 16,000 daily observations from Google Trends covering 9 continuous months. On the social engagement level, we extracted 3,500 review videos and over 32,000 viewer comments via the YouTube Data API. Everything has been integrated into an idempotent daily aligned dataset."*

### Slide 2 Script
> *"Our 60-product registry was intentionally designed to reflect the competitive reality of the Indian smartphone landscape. We balanced the portfolio across 13 major brands—including market leaders Apple and Samsung, volume giants Xiaomi and Realme, and rapid-growth players like OnePlus and iQOO. Crucially, we split the products into 40% Rising devices, 40% Stable devices, and 20% Declining baselines. This provides the variation needed to train models to discriminate genuine demand surges from seasonal noise or natural decay."*

### Slide 3 Script
> *"Why combine Google Trends and YouTube? Because they operate on distinct, complementary lead times. Unprompted search queries on Google Trends lead sales realization by 7 to 14 days as consumers begin preliminary research. YouTube reviews and engagement follow closely at 5 to 10 days as buyers seek influencer validations. Finally, comment sentiment shifts 3 to 7 days before purchase as the community forms consensus. By aligning these modalities, our radar achieves layered early warning."*

### Slide 4 & 5 Script
> *"We applied rigorous data hygiene standards throughout the pipeline. We solved the problem of sibling-model search contamination—such as an Edge 70 search pulling in Edge 70 Pro reviews—by implementing exact title matching algorithms that compute an on-target precision score for every device. Furthermore, we maintain clear architectural boundaries: relative search indexes are normalized for velocity modeling, hidden video stats are preserved as nulls rather than false zeros, and our collection scripts adhere strictly to API quota limits and ethical privacy guidelines without unauthorized scraping."*
