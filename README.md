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
| `data/products_v2.csv` | Registry v2, the **active** registry: v1 with narrowed `youtube_query` for P044 and P057. Changes and results are in [docs/registry_changelog.md](docs/registry_changelog.md). |
| `data/youtube/recent_window_daily.csv` | Recent-window pass: uploads in the last 7 days per product and day (`videos_published_7d`, title-matched `videos_7d_on_target`, `cap_hit`). |
| `data/processed/youtube_product_daily_clean.csv` | Cleaned `product_daily.csv` (`src/clean_youtube.py`). The cleaned video and comment tables sit next to it but are git-ignored. |
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

The GitHub repo is **public**. YouTube API content therefore stays on the machine that collected it and is git-ignored: `raw/youtube/*.json` (it includes commenter names and channel ids), `data/youtube/videos_snapshot.csv`, `data/youtube/comments_snapshot.csv` and their cleaned copies in `data/processed/`. Aggregates are committed: `product_daily.csv`, `recent_window_daily.csv`, `quota_usage.csv`, the cleaned daily table, the cleaning report and the logs. Google Trends exports in `raw/trends/` are committed. Share the git-ignored files with the team through a private channel, or make the repo private and remove those lines from `.gitignore`.

Back up the local-only data with `python3 src/backup_local_data.py`. It writes `backups/local_data_<UTC time>.tar.gz` plus a manifest of file names, sizes and SHA-256 hashes. `backups/` is git-ignored and nothing is uploaded, so copy the archive somewhere safe yourself.

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
python3 src/clean_youtube.py                # clean the YouTube tables into data/processed/
```

Options: `--products <csv>` or `--all`, `--only`, `--pass`, `--max-videos`, `--comment-videos`, `--comments-per-video`, `--daily-budget` (default 9000 units) and `--search-call-budget` (default 90).

How a run works:
- Settings and quota constants are in `src/config.py`. As of 2026-10-03, each `list` call costs 1 unit. The default quota is 10,000 units/day plus a separate cap of 100 `search.list` calls/day, and quotas reset at midnight Pacific Time.
- Quota use is stored in `data/youtube/quota_usage.csv`. A run stops cleanly before it would exceed either budget.
- If a raw file for the same product and date already exists, it is reused instead of re-fetched, and the product is logged as `skipped_cached`. Running again on the same day therefore costs nothing.
- `--all` orders products as never-collected first, then stalest, then already collected today. When a budget cap is hit, the run stops with `aborted_budget`, and the next run continues with the remaining products. Measured cost is 7 units and 1 search call per product: all 60 products take 420 units and 60 search calls per snapshot, so daily snapshots fit. To run it on a schedule, see [docs/scheduling.md](docs/scheduling.md); nothing is scheduled by the repo.
- **Registry versions:** the collector reads the registry named by `ACTIVE_REGISTRY` in `src/config.py` (now `v2`). A `--products` file only selects product_ids; all other fields come from the active registry. Each product's `query_version` is the oldest registry version with the same `youtube_query`. Raw files for query versions after v1 get a `_qvN` tag (`P044_2026-10-03_qv2_search.json`), and extract rows carry `query_version`, so re-collections never collide.
- **`video_result_count` is a capped value, not a signal.** It is the number of videos the main search returned, which is the 50-result cap for every pilot product. `result_cap_hit` records whether the cap was hit. For upload activity, use the recent pass: `videos_published_7d` (cap-aware, with `cap_hit`) and the title-matched `videos_7d_on_target`. Every field is defined in `data/data_dictionary.csv`.
- **Quota per product:** main pass 7 units + 1 search call; recent pass 1 unit + 1 search call. Run `--plan` for the schedules. Both passes daily for 60 products needs 120 search calls/day, which **exceeds the 90-call budget** (at most 45 products/day). Main weekly plus recent daily works if the main pass is spread over 2 days.
- Pilot status (snapshot 2026-10-03):
  - The main pass collected all 10 pilot products (500 videos, 4,697 comments, 70 units).
  - P044 and P057 were re-collected with the v2 queries (14 units).
  - The recent pass ran for 7 of the 10 pilot products (7 units); P018, P044 and P057 were skipped to stay under that day's search-call limit.

### Google Trends

The window is **India, a custom 269-day range (about 9 months), daily data**, with the same start and end dates for every product (team decision, 2026-10-03). Download exports by hand following [docs/trends_export_guide.md](docs/trends_export_guide.md):

```
python3 src/make_trends_checklist.py   # checklist + this batch's date range + exports still missing
# ... download each export to raw/trends/<product_id>_<YYYY-MM-DD>.csv ...
python3 src/load_trends.py             # validate + rebuild data/trends_long.csv
python3 src/load_trends.py --allow-weekly   # only if the team ever switches back to weekly data
```

The loader fails with a clear message, and writes nothing, if a file is badly named, is for the wrong search term, is weekly or monthly, or covers a different date range from the other products.

`<1` values are stored as `search_interest = 0.5` with `is_below_threshold = true`.

### Cleaning

`python3 src/clean_youtube.py` reads `data/youtube/` and writes cleaned tables plus `cleaning_report_youtube.csv` to `data/processed/`, without touching its inputs. Re-running it gives identical files. The rules are in [docs/cleaning_summary_youtube.md](docs/cleaning_summary_youtube.md):
- timestamps in ISO 8601 UTC;
- duplicates removed on exact match and on key, keeping the first row seen;
- missing values kept apart from zero;
- comment text cleaned, with emoji-only and non-Latin-script comments flagged, not dropped;
- outliers never removed.

Google Trends cleaning is a marked TODO stub (`src/clean_trends.py`) until the exports are downloaded.

Note: cleaned outputs go to `data/processed/`. The top-level `processed/` folder is an unused placeholder.
