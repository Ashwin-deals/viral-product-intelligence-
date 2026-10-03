"""Google Trends cleaning: NOT IMPLEMENTED YET.

TODO(trends-cleaning): implement once the manual exports are downloaded and loaded.
The Google Trends exports have not been downloaded yet (see docs/trends_export_guide.md and
data/trends_export_checklist.csv), so there is nothing to clean or test against.

When implemented, clean_trends() should:
- read data/trends_long.csv, the validated output of src/load_trends.py (do not re-parse
  raw/trends/; the loader already enforces daily granularity and identical date ranges);
- keep each product's latest export (max collection_date) as the analysis series and keep the
  older exports' rows separately for re-scaling checks;
- keep "<1" points as search_interest = 0.5 with is_below_threshold = true (do not turn them into 0);
- check for gaps in the daily date sequence and report them, never fill them silently;
- never remove outliers (surges are the signal);
- write data/processed/trends_clean.csv and data/processed/cleaning_report_trends.csv in the
  same before/after format as src/clean_youtube.py, plus docs/cleaning_summary_trends.md.

Usage (once implemented): python src/clean_trends.py
"""

import sys

import config


def clean_trends(paths=config.PATHS):
    """TODO(trends-cleaning): clean data/trends_long.csv into data/processed/trends_clean.csv."""
    raise NotImplementedError(
        "Google Trends cleaning is not implemented yet: the exports have not been downloaded. "
        f"Load them with src/load_trends.py first; this step will read {paths.trends_long_csv}.")


def main(paths=config.PATHS):
    try:
        clean_trends(paths)
    except NotImplementedError as error:
        print(f"TODO: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
