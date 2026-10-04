# Pilot go/no-go: Google Trends + YouTube (10 pilot products)

Status as of 2026-10-04. Data:
- **Google Trends:** collected with pytrends on 2026-10-03 21:11–21:22 UTC. Batch window 2026-01-08 to 2026-10-03, India, daily, one term per request.
- **YouTube:** snapshot 2026-10-03 (main pass and 7-day recent pass).

Every number below comes from these outputs:
- `data/trends_collection_manifest.csv` and `data/processed/cleaning_report_trends.csv`
- `data/processed/alignment_report.csv` and `data/processed/aligned_daily.csv`
- `python3 src/pilot_report.py`

## Recommendation: **GO-WITH-FIXES**

The pipeline works end to end on real data:
- all 10 pilot series are collected and validated;
- they load and clean without errors;
- they join with YouTube on product and date.

Scaling to 60 products is justified. But four things must be fixed or decided first, or the data will not support time-series modelling (see **Fixes before scaling**).

## Google Trends per product

| Product | Type | Trends days | Zero days | Zero share | Zero share since launch month | isPartial rows | Granularity |
|---|---|--:|--:|--:|--:|--:|---|
| P003 iPhone Duo | RISING | 269 | 244 | 91% | 24% since Sep (all 8 zeros before the 9 Sep announcement) | 1 | daily |
| P006 Galaxy Z Flip 8 | RISING | 269 | 231 | 86% | **62% since Jul** | 1 | daily |
| P016 Poco X8 Power | RISING | 269 | 231 | 86% | 0% since Sep | 1 | daily |
| P018 Vivo T5 5G | RISING | 269 | 11 | 4% | 0% since Sep | 1 | daily |
| P035 OnePlus 15R | STABLE | 269 | 0 | 0% | – | 1 | daily |
| P043 Realme GT 8 Pro | STABLE | 269 | 27 | 10% | – | 1 | daily |
| P044 Motorola Edge 70 | STABLE | 269 | 0 | 0% | – | 1 | daily |
| P046 Oppo Reno 15 | STABLE | 269 | 0 | 0% | – | 1 | daily |
| P057 Google Pixel 9 | DECLINING | 269 | 0 | 0% | – | 1 | daily |
| P059 Redmi Note 14 Pro Plus | DECLINING | 269 | 8 | 3% | – | 1 | daily |

- **Weekly vs daily check:** all 10 files are daily, cover exactly 2026-01-08 to 2026-10-03 and have the same range. The loader accepted them without `--allow-weekly`. There are no gaps, missing values or duplicates (`cleaning_report_trends.csv`).
- **Partial last day:** the last day (2026-10-03) is `isPartial` for every product. It is kept in the data and should be excluded from features.
- **Zeros:** the RISING products' zeros are mostly real pre-launch days. The launch peaks match the real launch dates: Z Flip 8 on 23 Jul, Vivo T5 on 2 Sep, Poco X8 Power on 6 Sep, iPhone Duo on 10 Sep. pytrends returns integers, so a `<1` day is stored as 0; `is_below_threshold` is never set for this data.

## YouTube per product (active query version)

| Product | On-target share (main pass) | Videos in last 7 days (on-target) | Note |
|---|--:|---|---|
| P003 | 92% | 50+ (37) | |
| P006 | 92% | 50+ (23) | |
| P016 | 80% | 50+ (26) | |
| P018 | 86% | 50+ (22) | |
| P035 | 92% | 46 (25) | |
| P043 | 96% | 18 (13) | |
| P044 | **56%** | 50+ (3) | flagged `low_on_target_share`; replacement pending |
| P046 | 94% | 34 (7) | |
| P057 | 64% | 12 (10) | v2 query; 18% Pixel 9a noise |
| P059 | 96% | 37 (3) | recent pass mostly off-topic |

`50+` means the 50-result cap was hit.

## Join and aligned table

- **`aligned_daily.csv`: 2,690 rows**, i.e. 10 products × 269 days. That is every Trends row, with no forward fill.
- **YouTube join:** all 10 pilot snapshots and all 10 recent-pass rows match a Trends day (2026-10-03).
  - Coverage is **1 of 269 days per product (0.37%)**, because YouTube has a single snapshot.
  - The 5 unmatched YouTube rows are the P044 replacement candidates (2026-10-04), which have no Trends data.
- **Estimated final size for all 60 products:** 60 × 269 = **16,140 rows** for this batch window. Each YouTube snapshot day adds at most 60 observed YouTube rows, and only if it falls inside a Trends window.
- **Collecting the other 50 Trends series with pytrends:**
  - At the pacing used (30–75 s pauses), the dry run estimates about **47 minutes**.
  - The pilot's observed pace (10.5 min for 9 fetches, including two 429 backoffs, about 70 s per product) suggests **about 1 hour**.
  - Worst case with every retry: about 6.6 hours. Running in sessions of 15–20 products (`--max-products`) keeps the risk of a block low.

## Fixes before scaling

1. **Build the YouTube time series.**
   - **Problem:** with one snapshot, YouTube overlaps Trends on one day per product, which is not enough to model anything over time.
   - **Fix:** schedule collection (`docs/scheduling.md`). Recent pass daily, main pass weekly.
2. **Decide the Trends refresh strategy.**
   - **Problem:** future YouTube snapshots fall after 2026-10-03, outside this Trends window. Each new Trends request is rescaled to its own peak, so rows from different requests cannot be mixed directly.
   - **Fix:** the team must choose between two approaches:
     - re-collect a full window per refresh and use only the latest batch (the current loader rule);
     - add overlapping windows and an explicit rescaling step.
3. **Decide P044.**
   - **Problem:** 56% on-target, and only 3 of 50 recent uploads are on-target.
   - **Fix:** a replacement was measured earlier. P033 Pixel 10a scored 96% and is the recommendation, but needs a Trends volume check. Its Trends series would then need collecting (1 product, about 1 minute).
4. **Data-quality flags to carry into modelling (not blockers):**
   - **P006 Galaxy Z Flip 8:** 62% zero days even after launch. The launch peak compresses ordinary days to 0 in integer data. Either accept this, or download a manual export for it to recover `<1` days.
   - **P018 Vivo T5:** non-zero interest throughout January–August, before its September launch. That points to sibling models (T5 Pro and other variants) inside the Trends term. The same broad-match issue affects `pixel 9` (P057).
   - **Partial last day:** exclude 2026-10-03 when computing changes.

## Why not NO-GO

There were no collection failures. The stop rule never triggered: 2 requests got HTTP 429 and both recovered after one 60 s backoff. Every series passed validation, and every pipeline step ran without errors. The open issues are about coverage and product choice, not broken data.
