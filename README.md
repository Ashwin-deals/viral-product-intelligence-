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
| `data/data_dictionary.csv` | Definitions of the columns used across the project's datasets. |
| `data/pilot_products.csv` | The 10 pilot products picked from the registry by `src/select_pilot.py` (fixed seed). |
| `data/youtube/videos_snapshot.csv` | One row per product, video and snapshot date: title, channel, publish date, view/like/comment counts. |
| `data/youtube/comments_snapshot.csv` | Top-level comments for each product's most-viewed videos, per snapshot date. |
| `data/youtube/product_daily.csv` | Per product per day: number of videos found, total views, total comments. |
| `data/youtube/quota_usage.csv` | YouTube API quota used per request, bucketed by Pacific date (the API's quota day). |
| `data/trends_long.csv` | Google Trends interest over time (daily) in long format, rebuilt from `raw/trends/` by `src/load_trends.py`. |
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

The GitHub repo is **public**. YouTube API content therefore stays on the machine that collected it and is git-ignored: `raw/youtube/*.json` (it includes commenter names and channel ids), `data/youtube/videos_snapshot.csv` and `data/youtube/comments_snapshot.csv`. Aggregates are committed: `data/youtube/product_daily.csv`, `data/youtube/quota_usage.csv` and the logs. Google Trends exports in `raw/trends/` are committed. Share the git-ignored files with the team through a private channel, or make the repo private and remove those lines from `.gitignore`.

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
python3 src/collect_youtube.py --all        # every product in data/products.csv, resumable
python3 src/pilot_report.py                 # go/no-go report: coverage, joins, quota, warnings
```

Options: `--products <csv>` or `--all`, `--max-videos`, `--comment-videos`, `--comments-per-video`, `--daily-budget` (default 9000 units) and `--search-call-budget` (default 90).

How a run works:
- Settings and quota constants are in `src/config.py`. As of 2026-10-03, each `list` call costs 1 unit. The default quota is 10,000 units/day plus a separate cap of 100 `search.list` calls/day, and quotas reset at midnight Pacific Time.
- Quota use is stored in `data/youtube/quota_usage.csv`. A run stops cleanly before it would exceed either budget.
- If a raw file for the same product and date already exists, it is reused instead of re-fetched, and the product is logged as `skipped_cached`. Running again on the same day therefore costs nothing.
- `--all` orders products as never-collected first, then stalest, then already collected today. When a budget cap is hit, the run stops with `aborted_budget`, and the next run continues with the remaining products. Measured cost is 7 units and 1 search call per product: all 60 products take 420 units and 60 search calls per snapshot, so daily snapshots fit. To run it on a schedule, see [docs/scheduling.md](docs/scheduling.md); nothing is scheduled by the repo.
- Pilot status: the first pilot run (snapshot 2026-10-03) collected all 10 pilot products (500 videos, 4,697 comments, 70 units).

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
