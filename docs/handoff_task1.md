# Handoff: Task 1 (data collection and preparation)

Status as of **2026-10-03**. This document tells teammates what data exists, where it is, how it was made, what is wrong with it, and where to start. Feature engineering and labels (growth, acceleration, surge labels) are **not** done here; they are the next task.

## 1. What was collected

| Source | Scope | Status |
|--------|-------|--------|
| Product registry | 60 smartphones, India market (24 RISING, 24 STABLE, 12 DECLINING; 13 brands) | done; active version **v3** |
| YouTube Data API v3, main pass | 10 pilot products, one snapshot (2026-10-03) | done |
| YouTube Data API v3, recent pass (uploads in the last 7 days) | 10 pilot products, one snapshot (2026-10-03) | done |
| Google Trends (pytrends; manual exports also supported) | 60 products, India, 269-day daily window | **10 pilot products collected** 2026-10-04 (269 days each); 50 to go |

The pilot products are P003, P006, P016 and P018 (RISING); P035, P043, P044 and P046 (STABLE); P057 and P059 (DECLINING). They were chosen with a fixed seed by `src/select_pilot.py`.

**Only one YouTube snapshot exists**, so there is no YouTube time series yet. One appears only once collection runs on a schedule (`docs/scheduling.md`).

## 2. Where each file lives

The GitHub repo is **public**. The raw YouTube JSON exists only on the collecting machine, because it holds commenter names and ids. Ask the data owner for the latest `backups/local_data_*.tar.gz` (made by `src/backup_local_data.py`), which includes a SHA-256 manifest. The backup covers every file under `raw/`, `data/` and `docs/`, tracked or not; only `.env` and `backups/` are excluded. The per-video and per-comment tables are also tracked in git since 2026-10-03, by the user's decision.

| File | What it is | In git? |
|------|------------|---------|
| `data/products_v3.csv` | Active registry (`config.ACTIVE_REGISTRY`); v1/v2 kept unchanged; see `docs/registry_changelog.md` | yes |
| `data/pilot_products.csv` | The 10 pilot product_ids | yes |
| `raw/youtube/*.json` | Untouched API responses (one file per product, date, query version and step) | **no** (commenter names and ids) |
| `raw/trends/*.csv` | Untouched Google Trends exports | yes (none yet) |
| `data/youtube/product_daily.csv`, `recent_window_daily.csv`, `quota_usage.csv`, `raw_file_index.csv` | Collector output and audit index | yes |
| `data/youtube/videos_snapshot.csv`, `comments_snapshot.csv` | Per-video and per-comment extracts (titles, comment text; no author names) | yes (since 2026-10-03, by the user's decision) |
| `data/processed/youtube_product_daily_clean.csv` | **Start here for YouTube**: cleaned daily table with on-target metrics | yes |
| `data/processed/youtube_recent_window_clean.csv` | Cleaned 7-day upload counts | yes |
| `data/processed/youtube_videos_clean.csv`, `youtube_comments_clean.csv` | Cleaned per-video and per-comment tables | yes (since 2026-10-03, by the user's decision) |
| `data/processed/cleaning_report_youtube.csv` | Before/after evidence for every cleaning step | yes |
| `raw/trends/pytrends_native/*.csv`, `data/trends_collection_manifest.csv` | Untouched pytrends results; collection method, time and validation per product | yes |
| `data/trends_long.csv` -> `data/processed/trends_clean.csv` | Trends loader output -> cleaned Trends | yes (2,690 rows, 10 pilot products) |
| `data/processed/aligned_daily.csv`, `alignment_report.csv` | **Start here for modelling**: Trends daily index joined with YouTube | yes (2,690 rows; YouTube observed on 1 day per product) |
| `docs/pilot_go_no_go.md`, `notebooks/03_eda_pilot.ipynb`, `docs/figures/` | Pilot go/no-go (GO-WITH-FIXES) and exploratory charts | yes |
| `data/data_dictionary.csv` | Definition and limitations of every field | yes |
| `data/collection_log.csv`, `data/source_log.csv` | Every collection attempt; source descriptions | yes |

## 3. How it was collected

**YouTube main pass** (`src/collect_youtube.py`), per product:
- **Search:** `search.list` with q = the registry `youtube_query`, `type=video`, `regionCode=IN`, `order=relevance`, `maxResults=50`.
- **Video statistics:** `videos.list` (snippet, statistics) for those up to 50 video ids.
- **Comments:** `commentThreads.list` (`order=relevance`, `textFormat=plainText`, up to 100 top-level comments) for the **5 most-viewed** of those videos.

**YouTube recent pass:** one `search.list` with the same q, `order=date`, `publishedAfter` = run time minus 7 days, and `maxResults=50`. Each title is classified as exact model or not (`src/title_match.py`).

**Dates and quota:**
- **Snapshot dates** are India time (Asia/Kolkata). Quota days are Pacific time, when the API resets.
- **Cost per product:** the main pass costs 7 units and 1 search call; the recent pass 1 unit and 1 search call.
- **Daily limits:** 10,000 units and 100 search calls; our budgets are 9,000 and 90.
- **Pilot total:** 94 units and 22 search calls on 2026-10-03.
- **P044 replacement check** (snapshot 2026-10-04, main pass only): 35 units and 5 search calls for P026, P027, P033, P039 and P045.

**Queries and versions:**
- `youtube_query` is `<trends_query> review`. Registry v2 added `-term` exclusions for P044 and P057, and v3 reverted P044.
- Every row and raw file carries the `query_version` that produced it. Untagged raw files are v1.
- Cleaned tables keep only the **active** version: P044 uses v1, P057 uses v2, and the others v1. 100 video rows, 1,000 comment rows and 2 daily rows from the inactive versions are excluded; the cleaning report lists them.
- `src/check_query_versions.py` verifies every raw file and table row against the registry. Result: PASS.

**Google Trends:**
- **How:** two methods, both following `docs/trends_export_guide.md` (India, All categories, Web Search, one term per request):
  - a manual website export;
  - since 2026-10-04, `src/collect_trends_pytrends.py`, which uses the pytrends library on Google's **unofficial** web endpoints, politely rate-limited.

  `data/trends_collection_manifest.csv` records the method, collection time, rows, date range, partial days and validation status of every product.
- **Window:** a custom 269-day range (daily data), with identical dates for every product. `src/make_trends_checklist.py` prints the range and the file names.
- **Loading:** `src/load_trends.py` rejects weekly or monthly files and mismatched date ranges. `src/clean_trends.py` keeps each product's latest export only.

## 4. Known limitations (read before modelling)

**YouTube metrics are from a 50-video relevance-ranked sample.**
- `video_views_total` and `video_comments_total` are sums over the top 50 search results at snapshot time.
- The sample changes between snapshots, so a change mixes real growth with a changing sample.
- View counts are cumulative over each video's lifetime, not views on that day.

**Some metrics are capped and carry no signal.**
- `video_result_count` is 50 for every product (the search cap). `search_total_results_approx` is an approximation that hits its 1,000,000 ceiling for 8 of 10 products.
- `videos_published_7d` is capped at 50, and 5 of 10 products hit the cap (`cap_hit`).

**Query noise from sibling models.** Base-model queries also return sibling models.
- **Exact-model share** (pilot, active queries): P003 92%, P006 92%, P016 80%, P018 86%, P035 92%, P043 96%, P044 **56%** (flagged `low_on_target_share`), P046 94%, P057 64%, P059 96%.
- **Recent uploads** are noisier still: 6% to 83% exact-model, with P044 at 3 of 50.
- **Use the on-target columns** (`views_on_target_total`, `comments_on_target_total`, `videos_7d_on_target`) for features, not the all-video totals. The title matcher is a heuristic, so videos that name the model only in the description are missed.

**Missing is not zero.**
- 15 videos have hidden like counts, which stay missing.
- In `product_daily`, the all-video sums count hidden statistics as 0; the on-target sums skip them.

**Comments are a narrow, biased sample.**
- Only top-level comments are collected, from the 5 most-viewed videos, in YouTube's relevance order. Replies are not collected.
- `likely_non_english` judges the **script**, not the language. Hinglish and other romanised Indian languages are labelled English, so an English sentiment model will score them poorly. 123 comments in Devanagari or other non-Latin scripts are flagged.
- 54 emoji-only comments are flagged. Flagged rows are kept, not dropped.

**Google Trends values are relative.**
- Each export is scaled to its own peak (100), so values are not comparable across products, or across exports of one product, in absolute terms.
- Manual exports: `<1` is stored as 0.5 with `is_below_threshold`. **pytrends data has no `<1`:** it returns integers, so tiny interest arrives as 0, `is_below_threshold` is never true, and zeros mix real zeros with `<1`.
- pytrends flags a still-incomplete last day as `isPartial`. It is recorded in the native file and the manifest and left in the data. The EDA notebook leaves it out of change and spike calculations.
- A later re-export with a new end date is rescaled; never mix rows from different exports. `clean_trends.py` keeps the latest export only.

**Alignment coverage.**
- The aligned table is a left join on the Trends daily index. YouTube snapshots fall on Trends dates only inside the export window: today that is one day (2026-10-03, if exports end that day).
- Later YouTube snapshots need a later Trends export to align, which is a new, rescaled series. Plan the Trends refresh with that in mind.
- YouTube values are never forward-filled; `youtube_observed` marks the dates with a snapshot.

**Pilot only.** 45 of the 50 non-pilot products have no YouTube data yet. The other 5 (P026, P027, P033, P039, P045) have one main-pass snapshot (2026-10-04), taken to measure P044 replacement candidates. They have no recent pass. Their rows are in the cleaned YouTube tables but not in the aligned table, because they have no Trends data. The registry lists their unverified launches and Trends-volume concerns in `notes`.

## 5. What teammates can start from now

1. **YouTube:** use `data/processed/youtube_product_daily_clean.csv` and `youtube_recent_window_clean.csv`. Filter or flag `low_on_target_share`, and use the `*_on_target` columns. Definitions are in `data/data_dictionary.csv`.
2. **Comments and sentiment:** use `data/processed/youtube_comments_clean.csv`, which is in git since 2026-10-03 and also in the backup archive. Remember the Hinglish caveat above, and consider a multilingual or Hinglish-aware model.
3. **Modelling table:** `data/processed/aligned_daily.csv` exists for the 10 pilot products. After new Trends or YouTube data, rebuild it with the four steps below. Every row then has registry fields, Trends values and same-day YouTube observations, with flags for what was observed.

   ```
   python3 src/load_trends.py
   python3 src/clean_trends.py
   python3 src/clean_youtube.py
   python3 src/build_aligned.py
   ```

   This produces `data/processed/aligned_daily.csv`. Feature engineering starts from that table.
4. **Re-running:** every script is idempotent and never edits `raw/`. `python3 -m pytest` runs the tests offline, with no API key needed.

## 6. Open items that need a human

- Collect Trends for the other 50 products (`python3 src/collect_trends_pytrends.py --all --max-products 20`, about 20–25 min per session) or download them manually. Check each term's volume.
- Act on the fixes in `docs/pilot_go_no_go.md`: a YouTube time series, a Trends refresh strategy, the P044 decision and the P006 compression flag.
- Decide on P044 (56% exact-model). Five STABLE candidates were measured on 2026-10-04 (exact-model share):
  - P033 Pixel 10a: 96%
  - P027 Galaxy S26 Ultra: 96%
  - P026 iPhone 17e: 92%
  - P039 Vivo X300 Pro: 78%
  - P045 Oppo Find X9 Pro: 76%
  
  Each candidate's Google Trends volume still needs a manual check. P022 (Motorola Edge 70 Max) is unmeasured.
- Schedule YouTube collection (`docs/scheduling.md`) to build a time series. Running both passes daily for 60 products exceeds the search-call budget; use the recent pass daily and the main pass weekly over 2 days.
- Store the backup archive safely, and share it privately with teammates who need the local-only files.
