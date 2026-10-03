# Google Trends export guide

Google Trends data for this project is downloaded **by hand** from the Google Trends website. We do not scrape it and do not use pre-built datasets. Each download is an original file. It goes in `raw/trends/` and is never edited afterwards.

## Fixed settings (use the same for every product)

| Setting | Value |
|---------|-------|
| Search term | the product's `trends_query` from `data/products.csv`, typed exactly (lowercase, no quotes) |
| Location | India |
| Time range | **Custom time range of exactly 269 days** (daily data), the **same start and end dates for every product** |
| Category | All categories |
| Search type | Web Search |
| Terms per export | **one** (do not use "+ Compare") |

Why these matter:

- Google Trends scales every export so that its own peak is 100. Values are therefore relative within one file, not comparable across products in absolute terms. Keep the settings identical so every file is scaled the same way.
- **Team decision (2026-10-03): past ~9 months, daily data.** Google Trends returns daily points only for ranges of 269 days or less; longer ranges (such as "Past 12 months") switch to weekly. The 269-day limit comes from secondary sources, not official Google docs, so check that the downloaded file has one row per day.
- Use a **custom** range with fixed dates rather than a relative preset. Downloads made on different days then still cover identical dates. `python3 src/make_trends_checklist.py` prints the range for today's batch: the end date is today and the start date is 268 days earlier, inclusive. Write the dates down and use them for every product in the batch, even if downloading takes several days.
- The loader rejects weekly or monthly files (`--allow-weekly` accepts weekly if the team ever needs it). It also rejects the batch if the latest exports of different products cover different date ranges.
- An export that compares several terms has more than one value column and is rejected by the loader.

## Steps

0. Run `python3 src/make_trends_checklist.py` to get the checklist, the expected file names and the date range for this batch.
1. Open https://trends.google.com/trends/explore
2. Enter the product's `trends_query` as the search term. If Google suggests a "topic" (for example "iPhone 18 Pro – Smartphone"), choose the plain **search term** instead, because the loader checks that the column name matches `trends_query`.
3. Set Location = India, category = All categories, search type = Web Search. Open the time range menu, choose **Custom time range**, and enter the batch's start and end dates (269 days, inclusive).
4. In the "Interest over time" chart, click the download (down-arrow) icon. The browser saves a CSV, usually named `multiTimeline.csv`.
5. Rename the file to `<product_id>_<YYYY-MM-DD>.csv`, where the date is **the day you downloaded it**, e.g. `P001_2026-10-03.csv`. `data/trends_export_checklist.csv` lists the expected name for every product; mark `done` there as you go.
6. Move it into `raw/trends/`. Do not open and re-save it in Excel or Sheets, because that can change the format. If a file with that name already exists, do not overwrite it; it is an earlier original.
7. Run the loader from the repo root:

   ```
   python3 src/load_trends.py
   ```

   It rebuilds `data/trends_long.csv` from every file in `raw/trends/` and logs each file to `data/collection_log.csv` (source `google_trends`).

## What the loader expects

A typical export looks like this:

```
Category: All categories

Day,iphone 18 pro: (India)
2026-01-08,12
2026-01-09,<1
```

- Lines before the header row (such as `Category: ...`) and blank lines are skipped. The header row starts with `Day`, `Week`, `Month` or `Time`. For `Time`, the granularity is inferred from the spacing between dates.
- The `(India)` suffix is removed from the term column, and the remaining term must equal the product's `trends_query`.
- `<1` means interest above zero but below 1. It is stored as `search_interest = 0.5` with `is_below_threshold = true`.
- Badly named files, exports for the wrong term, or unexpected values stop the loader with an error and a non-zero exit code.

The Google Trends website changes from time to time. If the download button or the file layout looks different from this guide, the loader will fail loudly instead of loading wrong data. Update this guide and `src/load_trends.py` together.

## Checking search volume

Before relying on a product, look at its chart. If it is mostly zero or `<1`, or shows "Hmm, your search doesn't have enough data to show here", the term has too little volume. Note that in the product's `notes` column instead of collecting it.
