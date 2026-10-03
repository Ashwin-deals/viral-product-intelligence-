# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

University Business Analytics capstone: "Viral Product Intelligence: Early Detection and Prediction of Consumer Demand Surges Using Multi-Source Signals". Scope: 60 smartphones in the India market (`data/products.csv`), tracked through YouTube Data API v3 snapshots and manually exported Google Trends data.

Status (2026-10-03):
- The collection pipeline is built.
- The YouTube pilot ran once: 10 products, snapshot 2026-10-03, all succeeded, 70 quota units.
- Google Trends exports have not been downloaded yet; they are a manual human step.
- Nothing is scheduled.
- No modelling, cleaning or dashboard work has started; do not start it unless asked.

## Commands

```
pip3 install -r requirements.txt
python3 -m pytest                                  # all tests; no network or API key needed
python3 -m pytest tests/test_load_trends.py -k weekly   # a single test
python3 src/validate_products.py                   # registry checks
python3 src/select_pilot.py                        # data/pilot_products.csv (seed 42)
python3 src/collect_youtube.py --dry-run [--all]   # plan + quota estimate, no API calls
python3 src/collect_youtube.py --smoke-test        # one search.list call, writes only a log row
python3 src/collect_youtube.py [--all]             # pilot (default) or all 60 products
python3 src/make_trends_checklist.py               # Trends export checklist + batch date range
python3 src/load_trends.py [--allow-weekly]        # raw/trends/*.csv -> data/trends_long.csv
python3 src/pilot_report.py                        # go/no-go report
```

Scripts are run from the repo root as `python3 src/<script>.py`. Modules import each other by plain name (`import config`). `pytest.ini` sets `pythonpath = src`.

## Architecture

- `src/config.py`: single source of settings. `Paths` derives every file location from one root, so tests use `Paths.from_root(tmp_path)`; functions take a `paths` argument rather than reading module globals. It also holds the YouTube settings, the quota constants (with the doc URLs where they were verified) and the Trends window.
- `src/common.py`: the collection-log writer (writes the header if the file is empty; append-only), `redact()` for API keys, and `append_rows_dedup()` for flat extracts.
- `src/collect_youtube.py` is built from small classes:
  - `QuotaTracker`: units and search.list calls per Pacific-time quota day, persisted per request to `data/youtube/quota_usage.csv`.
  - `YouTubeApi`: wraps the googleapiclient service, with budget checks before each request, pacing, 429/5xx backoff, and mapping of HTTP errors to `QuotaExceededError`, `FatalApiError` and `CommentsDisabledError`.
  - `YouTubeCollector`: per product, search → videos → commentThreads, with each step's raw JSON written once.
  - `run_collection`: logs one row per product and stops on budget, quota or fatal errors.
  - `resume_order`: orders products for `--all`.
  - Tests fake the googleapiclient service (`FakeService` in `tests/test_collect_youtube.py`), so no network is used.
- `src/load_trends.py`: validates all exports first (file name, search term = `trends_query`, daily granularity, identical date range across products' latest exports). It writes `data/trends_long.csv` only if everything passes; otherwise it logs and exits 1.
- `src/pilot_report.py`: read-only; must not crash when inputs are missing.

## Data conventions

- `raw/` holds untouched originals, append-only. Never edit, overwrite or delete them; existing raw files are reused as a cache (`skipped_cached`). Raw files are written with mode `"x"`.
- `data/` holds every CSV the project generates. Never delete rows from `data/collection_log.csv`. Do not edit `data/products.csv` or the frozen `products_v1_*.csv`; if the registry changes, save a new dated version.
- **The GitHub repo is public.** `raw/youtube/*.json` and `data/youtube/{videos,comments}_snapshot.csv` are git-ignored because they contain YouTube content and commenter identifiers. Do not commit them, or un-ignore them, unless the user decides to.
- **Google Trends (team decision 2026-10-03):** India, a custom range of exactly 269 days (daily data; longer ranges switch to weekly), the same start and end dates for every product, one term per export. Files are named `raw/trends/<product_id>_<YYYY-MM-DD>.csv`, where the date is the download date. `<1` is stored as 0.5 with `is_below_threshold=true`.
- **YouTube quota (verified 2026-10-03 at developers.google.com/youtube/v3/determine_quota_cost):** each list call costs 1 unit. There are 10,000 units/day plus a separate cap of 100 search.list calls/day, resetting at midnight Pacific Time. The defaults budget 9,000 units and 90 search calls. The pilot measured 7 units and 1 search call per product.
- Registry columns, in order: `product_id, product_name, brand, category, trends_query, youtube_query, launch_period, product_type, notes`. `youtube_query` is always `<trends_query> review`; the validator enforces this. Redmi/Poco and iQOO are listed as their own brands.
- Base-model queries also match sibling models: Trends broad-matches them, and YouTube search returns sibling videos. `pilot_report.py` flags products whose video titles mostly name a sibling.

## Rules for working in this repo

- Never commit as Claude: no `Co-Authored-By: Claude` trailers and no "Generated with Claude Code" lines in commits or PRs. Only the user and their teammates are contributors.
- Never push to `main` or merge PRs. Work on a feature branch and open a PR. While earlier PRs are unmerged, stack the new branch on the latest one.
- The API key lives only in `.env` (git-ignored). Never print, log, echo or commit it. All error text goes through `redact()`.
- Official sources only: YouTube Data API v3 and manual Google Trends exports. No scraping, no pytrends, no ready-made datasets.
- Stay inside quota. Run `--dry-run` before any real collection, never loop on retries after a key or quota failure, and never run the full collection (`--all`) unless asked.
- Do not invent facts. Mark unverifiable launch details as `unverified` or in `notes`. Use web search to confirm current lineups.
- Google Trends downloads and checking each `trends_query`'s volume in Google Trends are human tasks; do not claim they were done.
