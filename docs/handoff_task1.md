# Handoff: Task 1 (collection), Task 2 (cleaning), Task 3 (EDA)

Status as of **2026-10-04**. This document is for teammates who build features, labels and models next. It covers:
- what data exists and its schema;
- what is wrong with the data;
- the rules to follow when it is refreshed.

**No model features or surge labels have been built**; those belong to the modelling task.

The handoff bundle is **`data/handoff/task1_handoff.zip`**, built by `python3 src/build_handoff.py`. It contains:
- `aligned_daily.csv`, `trends_clean.csv`, `youtube_videos_clean.csv`, `youtube_comments_clean.csv` and `full_report.csv`;
- this document;
- `MANIFEST.csv`, with sizes and SHA-256 hashes.

It contains nothing from `raw/` and no secrets. Every field is defined in `data/data_dictionary.csv`.

## 1. What was collected

| Source | Scope | Method |
|---|---|---|
| Product registry | 60 smartphones, India: 24 RISING, 24 STABLE, 12 DECLINING; 13 brands | chosen by hand and web-checked; active version **v3** (`data/products_v3.csv`; changes in `docs/registry_changelog.md`) |
| Google Trends | all 60 products, India, daily, **2026-01-08 to 2026-10-03** (269 days each, 16,140 rows) | pytrends, one term per request, politely rate-limited (`src/collect_trends_pytrends.py`); manual website exports are also supported |
| YouTube main pass | all 60 products on snapshot **2026-10-04**; the 10 pilot products also on **2026-10-03** | YouTube Data API v3: top 50 videos by relevance (`regionCode=IN`), their statistics, and up to 100 top-level comments for each of the 5 most-viewed videos |
| YouTube recent pass | 10 pilot products on 2026-10-03; all 60 once `run_after_reset.sh` runs | one search for uploads in the last 7 days (`order=date`, capped at 50) |

Pilot products (fixed seed): P003, P006, P016 and P018 (RISING); P035, P043, P044 and P046 (STABLE); P057 and P059 (DECLINING).

## 2. Schema of the bundle

| File | Grain | Key columns |
|---|---|---|
| `aligned_daily.csv` (16,140 rows) | one row per product and Trends day | `product_id`, `date`; registry fields (`product_name`, `brand`, `category`, `product_type`); Trends values `search_interest`, `is_below_threshold` and `is_zero`; per-source observed flags `trends_observed`, `youtube_observed` and `youtube_recent_observed`; flags `is_provisional`, `zero_share_product` and `low_signal_product`; same-day YouTube main-pass totals, both all-video and `*_on_target`, with `on_target_share` and `low_on_target_share`; recent-pass `videos_published_7d`, `videos_7d_on_target` and `recent_cap_hit` |
| `trends_clean.csv` (16,140 rows) | product and day | `search_interest` (0–100, relative to the product's own peak), `is_below_threshold`, `is_zero`, `granularity`, `source_file`, `collection_date` |
| `youtube_videos_clean.csv` (3,500 rows) | snapshot, product and video | `video_id`, `title`, `channel_id`, `published_at` (UTC), cumulative `view_count`, `like_count`, `comment_count`, `query_version` |
| `youtube_comments_clean.csv` (32,215 rows) | snapshot, product and comment | `video_id`, `comment_id`, `text` (cleaned), `like_count`, `published_at` (UTC), `is_emoji_only`, `likely_non_english`, `query_version` |
| `full_report.csv` (60 rows) | product | Trends rows, zero share, peak, mean, `last_7d_mean`, YouTube videos and comments, on-target share, recent-pass counts, `usable`, `issues` |

Conventions:
- **Missing is never filled with 0.** An empty cell means missing.
- **Dates:** `YYYY-MM-DD` (India time for YouTube snapshots), and timestamps are ISO 8601 UTC.
- **Nothing is forward-filled.**
- **Only active query versions:** cleaned tables keep rows from each product's active query version. 100 videos, 1,000 comments and 2 daily rows from earlier query versions are excluded, as listed in `data/processed/cleaning_report_youtube.csv`.

## 3. Read before modelling

**Zero-heavy Trends products (flagged `low_signal_product`, never dropped).**
- 15 products have more than 50% zero days: P014, P021, P003, P006, P016, P029, P024, P010, P013, P005, P007, P022, P023, P012 and P004 (94% down to 54%).
- 14 of them are RISING products launched inside the window. Their series is zero before launch.
- pytrends returns integers, so a `<1` day is also stored as 0, and `is_below_threshold` is never true for this data.
- Their signal is concentrated around launch.
- `docs/full_report.md` lists all the flags.

**Thin YouTube overlap.**
- YouTube has one main-pass snapshot per product (two for the pilot products), and the latest (2026-10-04) falls **after** the Trends window.
- Only **10 of 16,140 product-days (0.06%)** have both a Trends value and a YouTube observation, so the data cannot yet relate YouTube and Trends over time.
- This grows only with scheduled YouTube snapshots (`docs/scheduling.md`) **and** a Trends refresh whose window covers those dates (see the refresh rule below).

**The last Trends day is provisional.**
- `is_provisional` is true on 2026-10-03 for every product, because Google marks its latest day as partial and may revise it.
- 40 products were flagged `isPartial` at collection. The 20 collected later were not, but the day is treated the same way everywhere.
- Exclude it from changes and spike detection. `full_report.csv`'s `last_7d_mean` already does.

**Trends values are relative.**
- Each product's series is scaled to its own peak (= 100). Shapes and changes within a product are meaningful; levels across products are not.

**Trends refresh rule: use the latest batch, never stack pulls.**
- Every new pull is rescaled to its own peak, so values from different pulls are on different scales.
- To extend the window, re-pull the **full** window for every product as a new batch, and use only that batch.
- `clean_trends.py` keeps each product's latest export. `load_trends.py` rejects batches whose products cover different date ranges.
- Never append a new short pull to an old one.

**YouTube totals are capped, relevance-ranked samples.**
- `video_result_count` is **capped at 50 and carries no signal**.
- `video_views_total` and `video_comments_total` sum the top 50 relevance-ranked videos. That set changes between snapshots, and the counts are cumulative lifetime totals.
- **Use the `*_on_target` columns** for modelling. They count only videos whose title names the exact model.
- 4 products are below 60% on-target, because sibling models share the search phrase: P015 48%, P009 50%, P012 56% and P028 56%.
- P044 Motorola Edge 70 is borderline: 56% on 2026-10-03 and 66% on 2026-10-04.

**Comments are a narrow sample.**
- Comments are top-level only, from the 5 most-viewed videos, in YouTube's relevance order, with at most 500 per product.
- Flagged and kept: `is_emoji_only` (395 comments) and `likely_non_english` (934, non-Latin script).
- **Hinglish** and other romanised Indian languages count as English under the script-based flag, so an English-only sentiment model will misread them.
- 85 videos have hidden like counts and 4 have no comment count; they stay missing.

**Missing sources: price, rating and review count.**
- The project title promises multi-source signals, but **no price, rating or review-count data was collected**.
- There is no official, freely usable API for Indian e-commerce listings. Amazon's Product Advertising API needs an approved associate account, and Flipkart's affiliate API needs its own approval.
- The project rules forbid scraping, so these columns do not exist.
- If the team needs them, it must get API access, or record them manually from a documented source, and add them as a new dated source in `data/source_log.csv`.

## 4. Where to start

1. **Modelling:** `aligned_daily.csv`. Filter or weight `low_signal_product` and `low_on_target_share`, exclude `is_provisional` from change calculations, and build features from `search_interest` and the `*_on_target` columns.
2. **Product choice:** `full_report.csv` and `docs/full_report.md`, which flag 42 products as usable on both sources.
3. **Comments and sentiment:** `youtube_comments_clean.csv`. Mind the Hinglish caveat above.
4. **EDA:** `notebooks/04_eda_full.ipynb` (all 60 products), with charts in `docs/figures/eda_full_*.png`. The pilot notebook is `notebooks/03_eda_pilot.ipynb`; the pilot verdict is in `docs/pilot_go_no_go.md`.

To rebuild everything after new data, run `run_after_reset.sh` (recent pass, cleaning, alignment, report, EDA, this bundle, backup and tests). Every script is idempotent and never edits `raw/`. `python3 -m pytest` runs offline.
