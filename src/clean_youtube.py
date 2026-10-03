"""Clean the local YouTube tables into data/processed/. Inputs are read, never modified.

Inputs  (data/youtube/):  videos_snapshot.csv, comments_snapshot.csv, product_daily.csv
Outputs (data/processed/): youtube_videos_clean.csv, youtube_comments_clean.csv (both git-ignored:
         they hold titles and comment text), youtube_product_daily_clean.csv,
         cleaning_report_youtube.csv (before/after evidence)
Summary: docs/cleaning_summary_youtube.md

Rules (also in docs/cleaning_summary_youtube.md):
- Column names: lowercase snake_case. product_id: "P" + 3 digits ("p44" -> "P044").
- snapshot_date -> YYYY-MM-DD; timestamps -> ISO 8601 UTC "YYYY-MM-DDTHH:MM:SSZ" (timestamps
  without a zone are taken as UTC, which is what the API returns). Unparseable timestamps become
  missing; rows with an unparseable snapshot_date or product_id are dropped (they cannot be keyed).
- Duplicates: exact duplicate rows are removed, then duplicates on the documented key
  (snapshot_date, product_id, query_version, video_id | comment_id; product_daily without the
  id). KEEP RULE: the first row seen in the input file is kept (rows are appended in collection
  order, so this is the earliest collected copy).
- Zero vs missing: an empty or non-numeric count is missing (<NA>), never 0. Hidden like counts
  and disabled statistics are missing; a reported 0 stays 0. Negative counts become missing.
- Comment text: HTML entities decoded, markup tags and zero-width characters stripped, whitespace
  collapsed; comments that
  are empty after this are dropped. Flags (rows kept): is_emoji_only (no letter or digit in any
  script) and likely_non_english (fewer than half of the letters are Latin a-z; a script-based
  heuristic, so romanised Hindi or Tamil counts as Latin).
- Outliers are never removed: surges are what the project studies.
- query_version is kept; is_active_query marks rows collected with the product's active query.

The output depends only on the inputs, so re-running gives identical files.

Usage: python src/clean_youtube.py
"""

import html
import re
import sys

import pandas as pd

import config
from collect_youtube import load_registries, query_version

VIDEOS_OUT = "youtube_videos_clean.csv"
COMMENTS_OUT = "youtube_comments_clean.csv"
DAILY_OUT = "youtube_product_daily_clean.csv"
REPORT_OUT = "cleaning_report_youtube.csv"
SUMMARY_DOC = "cleaning_summary_youtube.md"
REPORT_HEADER = ["table", "step", "column", "rows_before", "rows_after", "rows_dropped",
                 "missing_before", "missing_after", "detail"]

TABLES = {
    "videos": {
        "key": ["snapshot_date", "product_id", "query_version", "video_id"],
        "timestamps": ["published_at"],
        "counts": ["view_count", "like_count", "comment_count"],
        "booleans": [],
    },
    "comments": {
        "key": ["snapshot_date", "product_id", "query_version", "comment_id"],
        "timestamps": ["published_at"],
        "counts": ["like_count"],
        "booleans": [],
    },
    "product_daily": {
        "key": ["snapshot_date", "product_id", "query_version"],
        "timestamps": [],
        "counts": ["video_result_count", "video_views_total", "video_comments_total",
                   "search_total_results_approx"],
        "booleans": ["result_cap_hit"],
    },
}

TAG_PATTERN = re.compile(r"</?[A-Za-z][^>]*>")  # tags only: "I <3 it" is left alone
# zero-width space, word joiner, BOM; the zero-width joiner (U+200D) is kept: emoji sequences use it
INVISIBLE_PATTERN = re.compile("[\u200b\u2060\ufeff]")
PRODUCT_ID_PATTERN = re.compile(r"^P?0*(\d{1,3})$", re.IGNORECASE)


class Report:
    def __init__(self):
        self.rows = []

    def step(self, table, step, before, after, detail=""):
        self.rows.append({"table": table, "step": step, "column": "", "rows_before": before, "rows_after": after,
                          "rows_dropped": before - after, "missing_before": "", "missing_after": "",
                          "detail": detail})

    def missing(self, table, column, before, after, detail=""):
        self.rows.append({"table": table, "step": "missing_values", "column": column, "rows_before": "",
                          "rows_after": "", "rows_dropped": "", "missing_before": before,
                          "missing_after": after, "detail": detail})


def snake_case(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip())
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()


def normalize_product_id(value):
    match = PRODUCT_ID_PATTERN.match(value.strip())
    return f"P{int(match.group(1)):03d}" if match else None


def to_snapshot_date(series):
    parsed = pd.to_datetime(series.str.strip(), errors="coerce", format="mixed")
    return parsed.dt.strftime("%Y-%m-%d")


def to_utc_iso(series):
    parsed = pd.to_datetime(series.str.strip().replace("", None), errors="coerce", utc=True, format="mixed")
    return parsed.dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_count(series):
    numbers = pd.to_numeric(series.str.strip().replace("", None), errors="coerce")
    numbers = numbers.where(numbers >= 0)
    return numbers.round().astype("Int64")


def to_bool(series):
    mapping = {"true": True, "false": False, "1": True, "0": False}
    return series.str.strip().str.lower().map(mapping).astype("boolean")


def clean_text(text):
    text = html.unescape(text)
    text = TAG_PATTERN.sub(" ", text)
    text = INVISIBLE_PATTERN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def is_emoji_only(text):
    return bool(text) and not any(ch.isalnum() for ch in text)


def likely_non_english(text):
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    latin = sum(("a" <= ch.lower() <= "z") for ch in letters)
    return latin / len(letters) < 0.5


def missing_mask(df, column):
    """Missing = empty string in the raw input, NA after cleaning."""
    values = df[column]
    if values.dtype == object:
        return values.isna() | (values.astype(str).str.strip() == "")
    return values.isna()


def clean_table(name, raw, active_versions, report):
    spec = TABLES[name]
    df = raw.copy()
    report.step(name, "load", len(df), len(df), "rows read from the input file")

    renamed = {c: snake_case(c) for c in df.columns if snake_case(c) != c}
    df = df.rename(columns=renamed)
    if "query_version" not in df:
        df["query_version"] = "v1"  # files written before query versions existed
        renamed["(added)"] = "query_version=v1"
    report.step(name, "normalize_column_names", len(df), len(df),
                "; ".join(f"{a}->{b}" for a, b in renamed.items()) or "already snake_case")
    for col in spec["counts"] + spec["booleans"]:
        if col not in df:
            df[col] = ""  # legacy files without the newer columns: values are missing
    missing_before = {c: int(missing_mask(df, c).sum()) for c in df.columns}

    before = len(df)
    df["product_id"] = df["product_id"].map(normalize_product_id)
    df = df[df["product_id"].notna()]
    report.step(name, "normalize_product_id", before, len(df), "dropped: product_id not like P001")

    before = len(df)
    df["snapshot_date"] = to_snapshot_date(df["snapshot_date"])
    df = df[df["snapshot_date"].notna()]
    for col in spec["timestamps"]:
        df[col] = to_utc_iso(df[col])
    report.step(name, "standardize_dates", before, len(df),
                "snapshot_date -> YYYY-MM-DD (unparseable rows dropped); "
                + (", ".join(spec["timestamps"]) + " -> ISO 8601 UTC (unparseable -> missing)"
                   if spec["timestamps"] else "no timestamp columns"))

    before = len(df)
    df = df[~df.duplicated(keep="first")]
    report.step(name, "drop_exact_duplicates", before, len(df), "identical rows; keep first seen")
    before = len(df)
    df = df[~df.duplicated(subset=spec["key"], keep="first")]
    report.step(name, "drop_key_duplicates", before, len(df), f"key ({', '.join(spec['key'])}); keep first seen")

    for col in spec["counts"]:
        df[col] = to_count(df[col])
    for col in spec["booleans"]:
        df[col] = to_bool(df[col])
    report.step(name, "types_zero_vs_missing", len(df), len(df),
                "counts -> integers; empty, non-numeric or negative -> missing (not 0)")

    if name in ("videos", "comments"):
        text_col = "title" if name == "videos" else "text"
        cleaned = df[text_col].map(clean_text)
        changed = int((cleaned != df[text_col]).sum())
        df[text_col] = cleaned
        report.step(name, f"clean_{text_col}", len(df), len(df),
                    f"{changed} value(s) changed: HTML entities decoded, tags stripped, whitespace collapsed")
    if name == "comments":
        before = len(df)
        df = df[df["text"] != ""]
        report.step(name, "drop_empty_text", before, len(df), "comments empty after text cleaning")
        df["is_emoji_only"] = df["text"].map(is_emoji_only)
        df["likely_non_english"] = df["text"].map(likely_non_english)
        report.step(name, "flag_language", len(df), len(df),
                    f"flagged, not dropped: is_emoji_only={int(df['is_emoji_only'].sum())}, "
                    f"likely_non_english={int(df['likely_non_english'].sum())}")

    df["is_active_query"] = df["query_version"] == df["product_id"].map(active_versions)
    df = df.sort_values(spec["key"], kind="stable").reset_index(drop=True)
    report.step(name, "output", len(raw), len(df), "outliers kept; rows sorted by key")
    for col in df.columns:
        if col in missing_before or col in raw.columns:
            report.missing(name, col, missing_before.get(col, ""), int(missing_mask(df, col).sum()))
    return df


def active_query_versions(paths):
    registries = load_registries(paths)
    active = registries.get(config.ACTIVE_REGISTRY, {})
    return {pid: query_version(pid, p["youtube_query"], registries) for pid, p in active.items()}


def read_input(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def write_summary(paths, report_rows, outputs):
    lines = [
        "# YouTube cleaning summary",
        "",
        "Generated by `python3 src/clean_youtube.py`; re-running it with the same inputs gives the same files.",
        "The inputs in `data/youtube/` are never modified. The numbers below come from "
        f"`data/processed/{REPORT_OUT}`.",
        "",
        "## Rows",
        "",
        "| table | input rows | output rows | dropped | exact duplicates | key duplicates | other drops |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for table in TABLES:
        steps = {r["step"]: r for r in report_rows if r["table"] == table and r["column"] == ""}
        if "output" not in steps:
            continue
        exact = steps["drop_exact_duplicates"]["rows_dropped"]
        key = steps["drop_key_duplicates"]["rows_dropped"]
        total = steps["output"]["rows_dropped"]
        lines.append(f"| {table} | {steps['load']['rows_before']} | {steps['output']['rows_after']} | {total} "
                     f"| {exact} | {key} | {total - exact - key} |")
    lines += ["", "## Missing values (before -> after)", ""]
    for table in TABLES:
        changes = [r for r in report_rows if r["table"] == table and r["step"] == "missing_values"
                   and (r["missing_before"] or r["missing_after"])]
        if changes:
            lines.append(f"- **{table}**: " + ", ".join(
                f"{r['column']} {r['missing_before'] if r['missing_before'] != '' else 'n/a'} -> {r['missing_after']}"
                for r in changes))
    flags = [r for r in report_rows if r["step"] == "flag_language"]
    if flags:
        lines += ["", f"Comment flags (kept, not dropped): {flags[0]['detail'].split(': ', 1)[1]}."]
    lines += [
        "",
        "## Rules",
        "",
        "- Timestamps are ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`), and snapshot dates are `YYYY-MM-DD`. Column names are snake_case, and product_id is formatted like `P001`.",
        "- Duplicates are removed in two steps: exact duplicate rows first, then rows repeating the key. The key is `(snapshot_date, product_id, query_version, video_id | comment_id)`; `product_daily` has no id column. **The first row seen in the input file is kept**, which is the earliest collected copy.",
        "- Zero and missing are kept apart. Empty, non-numeric or negative counts become missing (empty in the CSV), never 0, so hidden like counts and disabled statistics show as missing. "
        "Exception: `video_views_total` and `video_comments_total` in `product_daily` are sums computed by the collector, which counted a video with hidden statistics as 0.",
        "- Comment text is cleaned: HTML entities are decoded, markup tags and zero-width characters removed, and whitespace collapsed. Comments that end up empty are dropped. `is_emoji_only` (no letter or digit) and `likely_non_english` (fewer than half the letters are Latin a-z) are flags only; those rows are kept.",
        "- `likely_non_english` judges the script, not the language. Hindi or Tamil typed in Latin letters counts as English.",
        "- **Outliers are never removed.** Demand surges are what the project studies.",
        "- `is_active_query` marks rows collected with the product's active `youtube_query` (see `docs/registry_changelog.md`). Use only those rows for one consistent series per product.",
        "",
        "## Outputs",
        "",
    ]
    lines += [f"- `data/processed/{name}`: {rows} rows{note}" for name, rows, note in outputs]
    lines += ["", "Google Trends cleaning is not done yet; see the TODO in `src/clean_trends.py`.", ""]
    (paths.root / "docs" / SUMMARY_DOC).write_text("\n".join(lines), encoding="utf-8")


def main(paths=config.PATHS):
    inputs = {
        "videos": (paths.videos_snapshot_csv, VIDEOS_OUT, " (git-ignored: titles)"),
        "comments": (paths.comments_snapshot_csv, COMMENTS_OUT, " (git-ignored: comment text)"),
        "product_daily": (paths.product_daily_csv, DAILY_OUT, ""),
    }
    missing = [str(p) for p, _, _ in inputs.values() if not p.exists()]
    if missing:
        print(f"Missing input(s): {', '.join(missing)}. Run the collector first.", file=sys.stderr)
        return 1

    active = active_query_versions(paths)
    report = Report()
    outputs = []
    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    for name, (in_path, out_name, note) in inputs.items():
        cleaned = clean_table(name, read_input(in_path), active, report)
        cleaned.to_csv(paths.processed_dir / out_name, index=False, lineterminator="\n")
        outputs.append((out_name, len(cleaned), note))

    pd.DataFrame(report.rows, columns=REPORT_HEADER).to_csv(
        paths.processed_dir / REPORT_OUT, index=False, lineterminator="\n")
    (paths.root / "docs").mkdir(exist_ok=True)
    write_summary(paths, report.rows, outputs)

    for name, rows, _ in outputs:
        print(f"  {name}: {rows} rows")
    print(f"Evidence: {paths.processed_dir / REPORT_OUT}; summary: docs/{SUMMARY_DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
