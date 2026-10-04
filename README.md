# Viral Product Intelligence

Early Detection and Prediction of Consumer Demand Surges Using Multi-Source Signals (Business Analytics capstone).

## Data files

All CSV files live in `data/`.

| File | Purpose |
|------|---------|
| `data/products.csv` | Product registry (working copy): smartphones for the India market, with the fixed Google Trends and YouTube search queries used for collection. |
| `data/products_v1_2026-10-03.csv` | Frozen copy of the product registry as of 2026-10-03. Do not edit; save a new dated version if the list changes. |
| `data/collection_log.csv` | Log of data-collection runs. |
| `data/source_log.csv` | Log of data sources used. |
| `data/data_dictionary.csv` | Precise definition and limitations of every YouTube-derived field (file, column, type, definition, limitations). |
| `data/pilot_products.csv` | The 10 pilot products picked from the registry by `src/select_pilot.py` (fixed seed). |
| `data/youtube/videos_snapshot.csv` | One row per product, video and snapshot date: title, channel, publish date, view/like/comment counts. |
| `data/youtube/comments_snapshot.csv` | Top-level comments for each product's most-viewed videos, per snapshot date. |
| `data/youtube/product_daily.csv` | Per product per day: number of videos found, total views, total comments. |
| `data/youtube/quota_usage.csv` | YouTube API quota used per request, bucketed by Pacific date (the API's quota day). |
| `data/trends_long.csv` | Google Trends interest over time (daily) in long format, rebuilt from `raw/trends/` by `src/load_trends.py`. |
| `data/products_v2.csv` | Registry v2: v1 with narrowed `youtube_query` for P044 and P057. Superseded; kept unchanged. |
| `data/products_v3.csv` | Registry v3, the **active** registry: v2 with P044 back on its v1 query. The changes and measurements behind them are in [docs/registry_changelog.md](docs/registry_changelog.md). |
| `data/youtube/recent_window_daily.csv` | Recent-window pass: uploads in the last 7 days per product and day (`videos_published_7d`, title-matched `videos_7d_on_target`, `cap_hit`). |
| `data/youtube/raw_file_index.csv` | One row per raw YouTube file, with its query version and whether its query matches the registry (`src/check_query_versions.py`). |
| `data/processed/youtube_product_daily_clean.csv` | Cleaned `product_daily.csv`, active query versions only, with exact-model metrics (`videos_on_target`, `views_on_target_total`, `comments_on_target_total`, `on_target_share`, `low_on_target_share`) next to the all-video totals. The cleaned video and comment tables sit next to it but are git-ignored. |
| `data/processed/youtube_recent_window_clean.csv` | Cleaned recent-window pass. |
| `data/processed/trends_clean.csv`, `cleaning_report_trends.csv` | Cleaned Google Trends (latest export per product) and its evidence report (`src/clean_trends.py`). 10 pilot products × 269 days (2,690 rows) as of 2026-10-04. |
| `data/processed/aligned_daily.csv`, `alignment_report.csv` | Trends daily index joined with same-day YouTube data and registry fields, with observed flags and no forward fill (`src/build_aligned.py`). 2,690 rows (10 pilot products) as of 2026-10-04. Go/no-go: [docs/pilot_go_no_go.md](docs/pilot_go_no_go.md). |
| `data/processed/cleaning_report_youtube.csv` | Before/after evidence for the YouTube cleaning: rows per step, duplicates, missing values, drops with reasons. Summary: [docs/cleaning_summary_youtube.md](docs/cleaning_summary_youtube.md). |
| `data/trends_export_checklist.csv` | One row per product to export from Google Trends: expected file name, pilot flag, and `done`/`notes` columns to fill in. Made by `src/make_trends_checklist.py`. |

### Product registry conventions

- `product_id`: `P001`, `P002`, ... sequential, never reused.
- `brand`: the brand the phone is sold under in India (Redmi and Poco are Xiaomi sub-brands; iQOO is a vivo sub-brand).
- `trends_query`: lowercase Google Trends search term. `youtube_query` is always `<trends_query> review`.
- `launch_period`: India launch month (`YYYY-MM`), `established` if launched more than a year before 2026-10-03, or `unverified`.
- `product_type`: `RISING` (launched since mid-2026 or upcoming), `STABLE` (current, steady interest), `DECLINING` (superseded or older model).

Validate the registry with:

```
python3 src/validate_products.py
```

## Collection pipeline

### Folder conventions

- `raw/` holds **untouched originals**: YouTube API responses (`raw/youtube/<product_id>_<YYYY-MM-DD>_{search,videos,comments}.json`) and manual Google Trends exports (`raw/trends/<product_id>_<YYYY-MM-DD>.csv`). These files are append-only and never edited or overwritten.
- `data/` holds every CSV table the project generates: the registry, the logs and the flat extracts.
- Every product-level attempt is logged to `data/collection_log.csv`, including failures and cache skips.

### What is committed

The GitHub repo is **public**.
- **Git-ignored:** `.env`, `backups/` and the raw YouTube API responses `raw/youtube/*.json`, which include commenter names and channel ids. They stay on the machine that collected them; share them with the team through a private channel.
- **Committed since 2026-10-03, by the user's decision:** the per-video and per-comment tables (`data/youtube/videos_snapshot.csv`, `comments_snapshot.csv` and their cleaned copies in `data/processed/`), holding titles and comment text but no author names.
- **Committed:** the aggregates, logs and Google Trends exports in `raw/trends/`.

Back up the local data with `python3 src/backup_local_data.py`. It archives every file under `raw/`, `data/` and `docs/`, whether git tracks it or not, and excludes only `.env` and `backups/`. If any file looks like it contains an API key, it aborts without writing anything. It writes `backups/local_data_<UTC time>.tar.gz` plus a manifest of file names, sizes and SHA-256 hashes. `backups/` is git-ignored and nothing is uploaded, so copy the archive somewhere safe yourself.

### Setup

```
pip3 install -r requirements.txt
cp .env.example .env      # then put your key in .env: YOUTUBE_API_KEY=...
python3 -m pytest         # no network or key needed
```

`.env` is git-ignored. Never commit it or paste the key into code, logs or issues.

### YouTube

```
python3 src/select_pilot.py                 # writes data/pilot_products.csv (4 RISING, 4 STABLE, 2 DECLINING)
python3 src/collect_youtube.py --dry-run    # what would be fetched + estimated quota; no API calls
python3 src/collect_youtube.py --smoke-test # one search for the first product; writes only a log row
python3 src/collect_youtube.py              # pilot run (data/pilot_products.csv) for today's snapshot
python3 src/collect_youtube.py --all        # every product in the active registry, resumable
python3 src/collect_youtube.py --pass recent   # only the 7-day recent-window pass (1 search call/product)
python3 src/collect_youtube.py --pass both     # main + recent pass
python3 src/collect_youtube.py --only P044,P057
python3 src/collect_youtube.py --plan       # quota needed for weekly/daily schedules, no API calls
python3 src/collect_youtube.py --migrate    # update local extracts to the current schema, no API calls
python3 src/pilot_report.py                 # go/no-go report: coverage, joins, quota, warnings
python3 src/check_query_versions.py         # every raw file and table row tied to its query version
python3 src/clean_youtube.py                # clean the YouTube tables into data/processed/
```

Options: `--products <csv>` or `--all`, `--only`, `--pass`, `--max-videos`, `--comment-videos`, `--comments-per-video`, `--daily-budget` (default 9000 units) and `--search-call-budget` (default 90).

How a run works:
- Settings and quota constants are in `src/config.py`. As of 2026-10-03, each `list` call costs 1 unit. The default quota is 10,000 units/day plus a separate cap of 100 `search.list` calls/day, and quotas reset at midnight Pacific Time.
- Quota use is stored in `data/youtube/quota_usage.csv`. A run stops cleanly before it would exceed either budget.
- If a raw file for the same product and date already exists, it is reused instead of re-fetched, and the product is logged as `skipped_cached`. Running again on the same day therefore costs nothing.
- `--all` orders products as never-collected first, then stalest, then already collected today. When a budget cap is hit, the run stops with `aborted_budget`, and the next run continues with the remaining products. Measured cost is 7 units and 1 search call per product: all 60 products take 420 units and 60 search calls per snapshot, so daily snapshots fit. To run it on a schedule, see [docs/scheduling.md](docs/scheduling.md); nothing is scheduled by the repo.
- **Registry versions:** the collector reads the registry named by `ACTIVE_REGISTRY` in `src/config.py` (now `v3`). A `--products` file only selects product_ids; all other fields come from the active registry. Each product's `query_version` is the oldest registry version with the same `youtube_query`. Raw files for query versions after v1 get a `_qvN` tag (`P044_2026-10-03_qv2_search.json`), and extract rows carry `query_version`, so re-collections never collide.
- **`video_result_count` is a capped value, not a signal.** It is the number of videos the main search returned, which is the 50-result cap for every pilot product. `result_cap_hit` records whether the cap was hit. For upload activity, use the recent pass: `videos_published_7d` (cap-aware, with `cap_hit`) and the title-matched `videos_7d_on_target`. Every field is defined in `data/data_dictionary.csv`.
- **Quota per product:** main pass 7 units + 1 search call; recent pass 1 unit + 1 search call. Run `--plan` for the schedules. Both passes daily for 60 products needs 120 search calls/day, which **exceeds the 90-call budget** (at most 45 products/day). Main weekly plus recent daily works if the main pass is spread over 2 days.
- Pilot status (snapshot 2026-10-03):
  - The main pass collected all 10 pilot products (500 videos, 4,697 comments, 70 units).
  - P044 and P057 were re-collected with the v2 queries (14 units).
  - The recent pass ran for all 10 pilot products (10 units): 7 in one run, then P018, P044 and P057 later the same day.
  - Registry v3 (active) reverted P044 to its v1 query without re-collecting it. P044's active data is therefore its v1 collection; P057 stays on v2.
- P044 replacement check (snapshot 2026-10-04): a main pass for 5 STABLE candidates, P026, P027, P033, P039 and P045 (35 units, 5 search calls). They are not in the pilot list. Their rows are in the cleaned YouTube tables but not in the aligned table, because they have no Trends data. The results are in `docs/handoff_task1.md`.

### Google Trends

The window is **India, a custom 269-day range (about 9 months), daily data**, with the same start and end dates for every product (team decision, 2026-10-03). Download exports by hand following [docs/trends_export_guide.md](docs/trends_export_guide.md):

```
python3 src/make_trends_checklist.py   # checklist + this batch's date range + exports still missing
# ... download each export to raw/trends/<product_id>_<YYYY-MM-DD>.csv ...
python3 src/load_trends.py             # validate + rebuild data/trends_long.csv
python3 src/load_trends.py --allow-weekly   # only if the team ever switches back to weekly data
```

The loader fails with a clear message, and writes nothing, if a file is badly named, is for the wrong search term, is weekly or monthly, or covers a different date range from the other products.

**Automated alternative (unofficial):** `src/collect_trends_pytrends.py` fetches the same series with pytrends.
- **How:** it uses Google's unofficial web endpoints, politely: 30–75 s pauses, 60/120/240 s backoff, and a stop after 3 consecutive failures.
- **Output:** the native result goes to `raw/trends/pytrends_native/`, and a website-layout copy to `raw/trends/` that the loader reads unchanged. Every product is recorded in `data/trends_collection_manifest.csv`.
- **Limitation:** pytrends returns integers, so `<1` arrives as 0.
- **Status (2026-10-04):** all 10 pilot products collected, 269 daily rows each. Two HTTP 429s, both recovered after one backoff.

```
python3 src/collect_trends_pytrends.py --dry-run | --smoke-test | --pilot | --all [--only P003] [--max-products N]
```

Exploratory analysis of the pilot: `notebooks/03_eda_pilot.ipynb`, with charts in `docs/figures/`. It is exploratory only; the spike chart is **not** the project's surge definition.

`<1` values are stored as `search_interest = 0.5` with `is_below_threshold = true`.

### Cleaning and alignment

```
python3 src/clean_youtube.py     # data/youtube -> data/processed (+ cleaning_report_youtube.csv)
python3 src/clean_trends.py      # data/trends_long.csv -> data/processed/trends_clean.csv (+ report)
python3 src/build_aligned.py     # Trends daily index + same-day YouTube -> aligned_daily.csv (+ report)
```

All three read their inputs without changing them, and re-running gives identical files.
- **YouTube cleaning** (rules in [docs/cleaning_summary_youtube.md](docs/cleaning_summary_youtube.md)):
  - Only rows from each product's **active query version** are kept, and the report lists the excluded ones.
  - Timestamps are standardized to ISO 8601 UTC.
  - Duplicates are removed on exact match and on key, keeping the first row seen.
  - Missing values are kept apart from zero.
  - Comment text is cleaned. Emoji-only and non-Latin-script comments are flagged, not dropped.
  - Outliers are never removed.
  - **Exact-model metrics** are added next to the all-video totals. Use the `*_on_target` columns for modelling, and treat products with `low_on_target_share` (below 60%) with care.
- **Trends cleaning** keeps each product's latest export. It removes exact and `(product_id, date)` duplicates, keeps the `<1` rule (0.5 with `is_below_threshold`), keeps zero apart from missing, and reports gaps in the daily index without filling them. With no exports yet, it exits cleanly and writes nothing.
- **Alignment** is a left join on the Trends daily index. It adds `trends_observed`, `youtube_observed` and `youtube_recent_observed`, with no forward fill and no features or labels. Without Trends data it exits cleanly.

Handoff for teammates: [docs/handoff_task1.md](docs/handoff_task1.md).

Note: cleaned outputs go to `data/processed/`. The top-level `processed/` folder is an unused placeholder.
