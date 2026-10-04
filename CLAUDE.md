# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

University Business Analytics capstone: "Viral Product Intelligence: Early Detection and Prediction of Consumer Demand Surges Using Multi-Source Signals". Scope: 60 smartphones in the India market (registry versions in `data/products*.csv`; active: `data/products_v3.csv`), tracked through YouTube Data API v3 snapshots and manually exported Google Trends data.

Status (2026-10-03):
- Task 1 (collection and data preparation) is built end to end. The teammate handoff is `docs/handoff_task1.md`.
- Pilot products (seed 42, `data/pilot_products.csv`):
  - RISING: P003, P006, P016, P018
  - STABLE: P035, P043, P044, P046
  - DECLINING: P057, P059
- YouTube has **one** snapshot (2026-10-03), so there is no time series yet:
  - main pass for all 10 pilot products;
  - v2-query re-collection of P044 and P057;
  - 7-day recent pass for all 10.
  - The other 50 products have no YouTube data.
- Registry v3 is active: P044 is back on its v1 query, and P057 keeps its v2 query. Nothing was re-collected for v3.
- Google Trends: the 10 pilot products were collected with pytrends on 2026-10-04 (269 daily rows each, last day `isPartial`); the other 50 are not collected yet.
- The pipeline ran end to end: `aligned_daily.csv` has 2,690 rows. Go/no-go: **GO-WITH-FIXES** (`docs/pilot_go_no_go.md`).
- EDA: `notebooks/03_eda_pilot.ipynb` (exploratory only), with charts in `docs/figures/`.
- Nothing is scheduled.
- No features, labels (growth, acceleration, surge labels), modelling or dashboard work have been done. They belong to teammates; do not start them unless asked.

Git history (2026-10-03):
- GitHub has only `main`, with two commits:
  - `0257cbb`, the teammate SisthickS's initial commit. Never rewrite or remove it.
  - `923b278`, a single squashed commit holding all the user's work.
- The user replaced their 28 earlier commits with that one commit and deleted the feature branches on GitHub.
- PRs #1 to #5 are merged and still show the old commits on GitHub.
- The local clone may still have the old feature branches. Don't push them back.

Open items for a human:
- Collect Trends for the other 50 products (pytrends in sessions with `--max-products`, or manually).
- Act on the fixes in `docs/pilot_go_no_go.md` (YouTube time series, Trends refresh strategy, P006 compression).
- Decide on P044 (56% on-target; possible replacement P022, unmeasured).
- Schedule collection (`docs/scheduling.md`).

## Commands

```
pip3 install -r requirements.txt
python3 -m pytest                                  # all tests; no network or API key needed
python3 -m pytest tests/test_load_trends.py -k weekly   # a single test
python3 src/validate_products.py                   # every registry version + cross-version checks
python3 src/select_pilot.py                        # data/pilot_products.csv (seed 42)
python3 src/collect_youtube.py --dry-run [--all]   # plan + quota estimate, no API calls
python3 src/collect_youtube.py --smoke-test        # one search.list call, writes only a log row
python3 src/collect_youtube.py [--all]             # pilot (default) or all 60 products
python3 src/collect_youtube.py --pass recent|both  # 7-day recent-window pass (1 search call/product)
python3 src/collect_youtube.py --only P044,P057    # restrict to some products
python3 src/collect_youtube.py --plan | --migrate  # quota scenarios / local schema update; no API calls
python3 src/make_trends_checklist.py               # Trends export checklist + batch date range
python3 src/collect_trends_pytrends.py --dry-run|--smoke-test|--pilot|--all [--only P003] [--max-products N]
python3 src/load_trends.py [--allow-weekly]        # raw/trends/*.csv -> data/trends_long.csv
python3 src/pilot_report.py                        # go/no-go report
python3 src/check_query_versions.py                # raw files + table rows vs registry query versions
python3 src/clean_youtube.py                       # data/youtube -> data/processed (+ cleaning report)
python3 src/clean_trends.py                        # data/trends_long.csv -> data/processed/trends_clean.csv
python3 src/build_aligned.py                       # Trends index + same-day YouTube -> aligned_daily.csv
python3 src/backup_local_data.py                   # archive all of raw/, data/, docs/ (not .env) into backups/
```

Scripts are run from the repo root as `python3 src/<script>.py`. Modules import each other by plain name (`import config`). `pytest.ini` sets `pythonpath = src`.

Pipeline order:
1. Collect: `collect_youtube.py`, then `check_query_versions.py`.
2. Load and clean: `load_trends.py`, `clean_trends.py` and `clean_youtube.py`.
3. Align: `build_aligned.py` produces `data/processed/aligned_daily.csv`, which is the starting point for teammates' feature work.
4. Report: `pilot_report.py` gives a go/no-go view at any point.

Every step is idempotent and only reads `raw/`.

Docs:
- `docs/handoff_task1.md`: what exists, its limitations, and where to start
- `docs/registry_changelog.md`: registry versions and the measurements behind them
- `docs/cleaning_summary_youtube.md`: generated by `clean_youtube.py`; do not hand-edit
- `docs/trends_export_guide.md`: manual Trends steps
- `docs/scheduling.md`: launchd, cron and systemd examples; nothing installed
- `data/data_dictionary.csv`: every field

## Architecture

- Registry versions: `config.REGISTRY_FILES` maps v1, v2 and v3 to `data/products.csv`, `products_v2.csv` and `products_v3.csv`. The collector, cleaning and alignment read `config.ACTIVE_REGISTRY` (now v3). `resolve_products` takes product_ids from any products file and the fields from the active registry. It attaches `query_version`, the oldest version with the same `youtube_query`. Raw file names get `_q<version>` for versions after v1, and extract keys include `query_version`.
- `src/title_match.py`: classifies a video title as on-target, sibling or off-topic for a `trends_query`. The pilot report's on-target share and the recent pass's `videos_7d_on_target` both use it.
- `src/config.py`: single source of settings. `Paths` derives every file location from one root, so tests use `Paths.from_root(tmp_path)`; functions take a `paths` argument rather than reading module globals. It also holds the YouTube settings, the quota constants (with the doc URLs where they were verified) and the Trends window.
- `src/common.py`: the collection-log writer (writes the header if the file is empty; append-only), `redact()` for API keys, and `append_rows_dedup()` for flat extracts.
- `src/collect_youtube.py` is built from small classes:
  - `QuotaTracker`: units and search.list calls per Pacific-time quota day, persisted per request to `data/youtube/quota_usage.csv`.
  - `YouTubeApi`: wraps the googleapiclient service, with budget checks before each request, pacing, 429/5xx backoff, and mapping of HTTP errors to `QuotaExceededError`, `FatalApiError` and `CommentsDisabledError`.
  - `YouTubeCollector`: per product, search → videos → commentThreads, with each step's raw JSON written once.
  - `run_collection`: logs one row per product and pass (sources `youtube` and `youtube_recent`), and stops on budget, quota or fatal errors.
  - `migrate_extracts`: adds new columns to old local extracts from the raw files, via `common.ensure_schema`.
  - `resume_order`: orders products for `--all`.
  - Tests fake the googleapiclient service (`FakeService` in `tests/test_collect_youtube.py`), so no network is used.
- `src/load_trends.py`: validates all exports first (file name, search term = `trends_query`, daily granularity, identical date range across products' latest exports). It writes `data/trends_long.csv` only if everything passes; otherwise it logs and exits 1.
- `src/pilot_report.py`: read-only; must not crash when inputs are missing. It reports on each product's active query version and compares query versions.
- `src/clean_youtube.py`: writes only to `data/processed/` and `docs/cleaning_summary_youtube.md`, and is idempotent.
  - It keeps only rows whose `query_version` is the product's active one (step `select_active_query`).
  - Key duplicates keep the first row seen. Missing counts are never 0, outliers are never removed, and comments are flagged rather than dropped.
  - It adds the on-target metrics (`*_on_target*`, `on_target_share`, `low_on_target_share` < `config.ON_TARGET_MIN_SHARE`) next to the all-video totals.
  - Each step's numbers go to `data/processed/cleaning_report_youtube.csv`.
- `src/check_query_versions.py`: read-only on `raw/`. It writes `data/youtube/raw_file_index.csv` and exits 1 on any mismatch. Untagged raw files are v1 by convention.
- `src/collect_trends_pytrends.py`: a `Collector` with an injectable fetch and sleep, so the tests need no network.
  - It writes the native result to `raw/trends/pytrends_native/` and a website-layout copy to `raw/trends/` (filename date = `config.TRENDS_FILE_DATE`), both once with mode `"x"`.
  - It upserts `data/trends_collection_manifest.csv` and logs source `google_trends_pytrends`.
  - pytrends 4.9.2 + urllib3 2.x: keep `retries=0`/`backoff_factor=0`, because its Retry call uses the removed `method_whitelist`.
- `src/clean_trends.py`: keeps each product's latest export, deduplicates on `(product_id, date)`, and reports gaps without filling them.
- `src/build_aligned.py`: a left join on the Trends daily index, with observed flags and no forward fill.
- Both Trends-side scripts exit 0 and write nothing when their input is missing.

## Data conventions

- `raw/` holds untouched originals, append-only. Never edit, overwrite or delete them; existing raw files are reused as a cache (`skipped_cached`). Raw files are written with mode `"x"`.
- `data/` holds every CSV the project generates. Cleaned outputs go to `data/processed/`; the top-level `processed/` folder is an unused placeholder. Never delete rows from `data/collection_log.csv`. Do not edit any registry version (`products.csv`, `products_v2.csv`, `products_v3.csv`) or the frozen `products_v1_*.csv`. If the registry changes, add a new version file.
- **The GitHub repo is public.**
  - Git-ignored: `.env`, `backups/` and `raw/youtube/*.json`. The raw JSON is ignored because it holds commenter names and channel ids.
  - Committed, by the user's decision on 2026-10-03: `data/youtube/{videos,comments}_snapshot.csv` and their cleaned copies in `data/processed/` (video titles and comment text, without author names).
  - Do not change what is ignored or tracked without asking the user.
- pytrends returns integers, so a `<1` day arrives as 0 and `is_below_threshold` is never true for pytrends data. The last day can be `isPartial`; it is recorded in the native file and the manifest, not removed.
- **Google Trends (team decision 2026-10-03):** India, a custom range of exactly 269 days (daily data; longer ranges switch to weekly), the same start and end dates for every product, one term per export. Files are named `raw/trends/<product_id>_<YYYY-MM-DD>.csv`, where the date is the download date. `<1` is stored as 0.5 with `is_below_threshold=true`.
- **YouTube quota (verified 2026-10-03 at developers.google.com/youtube/v3/determine_quota_cost):** each list call costs 1 unit. There are 10,000 units/day plus a separate cap of 100 search.list calls/day, resetting at midnight Pacific Time. The defaults budget 9,000 units and 90 search calls. The pilot measured 7 units and 1 search call per product.
- Registry columns, in order: `product_id, product_name, brand, category, trends_query, youtube_query, launch_period, product_type, notes`. `youtube_query` is `<trends_query> review`, optionally followed by `-term` exclusions; the validator enforces this, and between versions only `youtube_query` may change. Redmi/Poco and iQOO are listed as their own brands.
- Base-model queries also match sibling models: Trends broad-matches them, and YouTube search returns sibling videos. `pilot_report.py` flags products whose video titles mostly name a sibling.
- Registry v2 narrowed the P044 and P057 YouTube queries with `-term` exclusions. It did not clearly help: P044 fell from 56% to 44% on-target, and P057 rose from 58% to 64%. v3 therefore reverted P044 and kept P057; see `docs/registry_changelog.md`. Never edit an earlier registry version or the frozen copy: add a new version file, register it in `config.REGISTRY_FILES`, and log it in the changelog.
- `video_result_count` is capped at 50 and is not a signal. **Modelling should use the on-target columns** (`views_on_target_total`, `comments_on_target_total`, `videos_7d_on_target`), not the all-video totals. P044 is flagged `low_on_target_share` (56%). Every field is defined in `data/data_dictionary.csv`.
- Quota per product: main pass 7 units + 1 search call, recent pass 1 + 1. Both passes daily for 60 products would need 120 search calls/day, over the 90 budget; `--plan` shows the options.

## Rules for working in this repo

- Never commit as Claude: no `Co-Authored-By: Claude` trailers and no "Generated with Claude Code" lines in commits or PRs. Only the user and their teammates are contributors.
- Default workflow: work on a feature branch from `main` and open a PR; never merge PRs yourself. Push directly to `main`, or rewrite its history, only when the user explicitly asks. Then use `--force-with-lease` and keep the teammate's commit `0257cbb`.
- Commit at the end of a task in logical commits, authored by the user's git identity. Before committing, check `git status --ignored` and scan for the key. Never commit git-ignored files. If a file holds personal data or you are unsure about it, list it and ask.
- The API key lives only in `.env` (git-ignored). Never print, log, echo or commit it. All error text goes through `redact()`.
- Sources: YouTube Data API v3, manual Google Trends exports, and (since 2026-10-04, by the user's decision) pytrends via `src/collect_trends_pytrends.py`. pytrends uses Google's unofficial web endpoints:
  - Keep its polite pacing (30-75 s pauses, 60/120/240 s backoff, stop after 3 consecutive failures).
  - Never work around rate limits or blocks (no proxies, spoofed headers or identity rotation).
  - No other scraping, and no ready-made datasets.
- Stay inside quota. A per-task limit from the user (e.g. "fewer than 10 search calls in total") overrides the defaults. If a task's steps would exceed it, keep to the limit and report what was skipped. Run `--dry-run` before any real collection, and cap ad-hoc runs with `--search-call-budget` (calls already used today + calls planned). Never loop on retries after a key or quota failure, and never run the full collection (`--all`) unless asked.
- Do not invent facts. Mark unverifiable launch details as `unverified` or in `notes`. Use web search to confirm current lineups.
- Google Trends downloads and checking each `trends_query`'s volume in Google Trends are human tasks; do not claim they were done.
