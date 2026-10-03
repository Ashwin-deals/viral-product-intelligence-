"""Clean the Google Trends loader output into data/processed/. Inputs are read, never modified.

Input:   data/trends_long.csv (written by src/load_trends.py from raw/trends/ exports)
Outputs: data/processed/trends_clean.csv
         data/processed/cleaning_report_trends.csv (before/after rows per step, per-product summary)

Steps:
- column names to snake_case; product_id to "P" + 3 digits (rows with an invalid id are dropped);
- date and collection_date to YYYY-MM-DD (rows with an unparseable date are dropped);
- latest export only: for each product, rows from older exports (smaller collection_date) are
  excluded, because every export is rescaled to its own peak and must not be mixed;
- exact duplicates removed, then duplicates on the key (product_id, date). KEEP RULE: the first
  row seen is kept;
- search_interest to a number. Missing, non-numeric or out-of-range (not 0-100) values become
  missing, never 0; a reported 0 stays 0 and is marked is_zero. "<1" points keep the loader's
  rule, search_interest = 0.5 with is_below_threshold = true; a below-threshold row with any
  other value is set to 0.5 and counted in the report;
- gaps in each product's daily index (calendar days between its first and last date with no
  row) are reported, never filled;
- outliers are never removed: surges are what the project studies.

If raw/trends/ has no exports yet, the script says so and exits cleanly without writing anything.
The output depends only on the input, so re-running gives identical files.

Usage: python src/clean_trends.py
"""

import re
import sys

import pandas as pd

import config

OUT = "trends_clean.csv"
REPORT_OUT = "cleaning_report_trends.csv"
REPORT_HEADER = ["table", "step", "column", "rows_before", "rows_after", "rows_dropped",
                 "missing_before", "missing_after", "detail"]
TABLE = "trends"
KEY = ["product_id", "date"]
OUTPUT_COLUMNS = ["product_id", "date", "search_interest", "is_below_threshold", "is_zero",
                  "granularity", "source_file", "collection_date"]
BELOW_THRESHOLD_VALUE = 0.5
PRODUCT_ID_PATTERN = re.compile(r"^P?0*(\d{1,3})$", re.IGNORECASE)


class NoTrendsExports(Exception):
    pass


def snake_case(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip())
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()


def normalize_product_id(value):
    match = PRODUCT_ID_PATTERN.match(str(value).strip())
    return f"P{int(match.group(1)):03d}" if match else None


def to_date(series):
    return pd.to_datetime(series.astype(str).str.strip(), errors="coerce", format="mixed").dt.strftime("%Y-%m-%d")


def gap_ranges(dates):
    """Missing calendar days between the first and last date, as (count, 'a..b' ranges)."""
    days = pd.to_datetime(pd.Series(sorted(set(dates))))
    if len(days) < 2:
        return 0, []
    full = pd.date_range(days.iloc[0], days.iloc[-1], freq="D")
    missing = full.difference(days)
    ranges, start, prev = [], None, None
    for day in missing:
        if start is None:
            start = prev = day
        elif (day - prev).days == 1:
            prev = day
        else:
            ranges.append((start, prev))
            start = prev = day
    if start is not None:
        ranges.append((start, prev))
    text = [a.strftime("%Y-%m-%d") + ("" if a == b else ".." + b.strftime("%Y-%m-%d")) for a, b in ranges]
    return len(missing), text


class Report:
    def __init__(self):
        self.rows = []

    def step(self, step, before, after, detail="", column=""):
        self.rows.append({"table": TABLE, "step": step, "column": column, "rows_before": before,
                          "rows_after": after, "rows_dropped": before - after if before != "" else "",
                          "missing_before": "", "missing_after": "", "detail": detail})

    def missing(self, column, before, after):
        self.rows.append({"table": TABLE, "step": "missing_values", "column": column, "rows_before": "",
                          "rows_after": "", "rows_dropped": "", "missing_before": before,
                          "missing_after": after, "detail": ""})


def clean(raw, report):
    df = raw.copy()
    report.step("load", len(df), len(df), "rows read from data/trends_long.csv")
    df = df.rename(columns={c: snake_case(c) for c in df.columns})
    for col in ("is_below_threshold", "granularity", "source_file", "collection_date"):
        if col not in df:
            df[col] = ""
    blank = lambda col: (df[col].astype(str).str.strip() == "")  # noqa: E731
    missing_before = {c: int(blank(c).sum()) for c in df.columns}

    before = len(df)
    df["product_id"] = df["product_id"].map(normalize_product_id)
    df = df[df["product_id"].notna()]
    report.step("normalize_product_id", before, len(df), "dropped: product_id not like P001")

    before = len(df)
    df["date"] = to_date(df["date"])
    df["collection_date"] = to_date(df["collection_date"])
    df = df[df["date"].notna()]
    report.step("standardize_dates", before, len(df), "date and collection_date -> YYYY-MM-DD; "
                "rows with an unparseable date dropped")

    before = len(df)
    latest = df.groupby("product_id")["collection_date"].transform(lambda s: s.dropna().max() if s.notna().any() else None)
    superseded = df[(df["collection_date"] != latest) & latest.notna()]
    df = df[(df["collection_date"] == latest) | latest.isna()]
    detail = "; ".join(f"{pid} {cd}: {n}" for (pid, cd), n in
                       superseded.groupby(["product_id", "collection_date"]).size().items())
    report.step("select_latest_export", before, len(df),
                f"rows from older exports excluded (each export has its own 0-100 scale): {detail or 'none'}")

    before = len(df)
    df = df[~df.duplicated(keep="first")]
    report.step("drop_exact_duplicates", before, len(df), "identical rows; keep first seen")
    before = len(df)
    df = df[~df.duplicated(subset=KEY, keep="first")]
    report.step("drop_key_duplicates", before, len(df), "key (product_id, date); keep first seen")

    values = pd.to_numeric(df["search_interest"].astype(str).str.strip().replace("", None), errors="coerce")
    out_of_range = values.notna() & ~values.between(0, 100)
    values = values.mask(out_of_range)
    below = df["is_below_threshold"].astype(str).str.strip().str.lower().isin({"true", "1"})
    corrected = below & (values != BELOW_THRESHOLD_VALUE)
    values = values.mask(below, BELOW_THRESHOLD_VALUE)
    df["search_interest"] = values.astype("Float64")
    df["is_below_threshold"] = below.astype("boolean")
    df["is_zero"] = (df["search_interest"] == 0).astype("boolean")
    report.step("types_zero_vs_missing", len(df), len(df),
                f"search_interest -> number; missing/non-numeric -> missing (not 0); "
                f"{int(out_of_range.sum())} out-of-range value(s) -> missing; "
                f"'<1' rows = {BELOW_THRESHOLD_VALUE} with is_below_threshold "
                f"({int(below.sum())} row(s), {int(corrected.sum())} corrected); "
                f"reported zeros = {int(df['is_zero'].fillna(False).sum())}")

    df = df.sort_values(KEY, kind="stable").reset_index(drop=True)[OUTPUT_COLUMNS]
    report.step("output", len(raw), len(df), "outliers kept; gaps reported, not filled; rows sorted by key")

    for pid, group in df.groupby("product_id", sort=True):
        gaps, ranges = gap_ranges(group["date"])
        report.rows.append({
            "table": TABLE, "step": "product_summary", "column": pid, "rows_before": "",
            "rows_after": len(group), "rows_dropped": "", "missing_before": "",
            "missing_after": int(group["search_interest"].isna().sum()),
            "detail": f"{group['date'].min()}..{group['date'].max()}; days={len(group)}; "
                      f"zero={int(group['is_zero'].fillna(False).sum())}; "
                      f"below_threshold={int(group['is_below_threshold'].sum())}; "
                      f"missing={int(group['search_interest'].isna().sum())}; gap_days={gaps}"
                      + (f" ({', '.join(ranges[:5])}{', ...' if len(ranges) > 5 else ''})" if ranges else ""),
        })
    for col in OUTPUT_COLUMNS:
        if col in missing_before:
            after = df[col].isna() | (df[col].astype(str).str.strip() == "")
            report.missing(col, missing_before[col], int(after.sum()))
    return df


def exports_present(paths):
    return paths.raw_trends_dir.exists() and any(
        p.is_file() and not p.name.startswith(".") for p in paths.raw_trends_dir.iterdir())


def main(paths=config.PATHS):
    if not exports_present(paths):
        print(f"No Google Trends exports in {paths.raw_trends_dir} yet; nothing to clean. "
              "Download them (docs/trends_export_guide.md), run src/load_trends.py, then run this again.")
        return 0
    if not paths.trends_long_csv.exists():
        print(f"Exports exist but {paths.trends_long_csv} does not. Run python3 src/load_trends.py first.",
              file=sys.stderr)
        return 1

    report = Report()
    cleaned = clean(pd.read_csv(paths.trends_long_csv, dtype=str, keep_default_na=False), report)
    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(paths.processed_dir / OUT, index=False, lineterminator="\n")
    pd.DataFrame(report.rows, columns=REPORT_HEADER).to_csv(
        paths.processed_dir / REPORT_OUT, index=False, lineterminator="\n")
    print(f"  {OUT}: {len(cleaned)} rows, {cleaned['product_id'].nunique()} products")
    for row in report.rows:
        if row["step"] == "product_summary":
            print(f"  {row['column']}: {row['detail']}")
    print(f"Evidence: {paths.processed_dir / REPORT_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
