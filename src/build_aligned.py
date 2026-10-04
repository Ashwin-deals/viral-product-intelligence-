"""Join cleaned Google Trends and cleaned YouTube daily data into one aligned table (no features).

Inputs:  data/processed/trends_clean.csv               (required; src/clean_trends.py)
         data/processed/youtube_product_daily_clean.csv (src/clean_youtube.py)
         data/processed/youtube_recent_window_clean.csv (src/clean_youtube.py, optional)
         active registry (config.ACTIVE_REGISTRY) for product_name, brand, category, product_type
Outputs: data/processed/aligned_daily.csv
         data/processed/alignment_report.csv (row counts, unmatched rows, coverage per product)

Rules:
- The Trends daily index (product_id, date) is the spine: every Trends row is kept (left join).
  YouTube rows whose snapshot_date is outside a product's Trends index are not in the table;
  the report counts them as unmatched.
- YouTube values appear only on the date they were observed. Nothing is forward-filled or
  interpolated; a date without a snapshot has missing YouTube values.
- trends_observed / youtube_observed / youtube_recent_observed say whether that source has a value
  on that date.
- is_provisional is true on each product's last Trends date (2026-10-03 for the current batch):
  Google marks its latest day as partial (isPartial), so that value may still change. It applies
  to every product, also those collected after Google stopped flagging the day, so the same date
  is treated the same way everywhere.
- zero_share_product = share of the product's days with search_interest exactly 0, among days
  that have a value (missing days are neither zero nor counted). low_signal_product is true when
  that share is above config.LOW_SIGNAL_ZERO_SHARE (50%). Rows are flagged, never dropped.
- No features or labels (growth, acceleration, surge labels) are computed here.

If the cleaned Trends data does not exist yet, the script says so and exits cleanly without
writing anything.

Usage: python src/build_aligned.py
"""

import sys

import pandas as pd

import config

TRENDS_IN = "trends_clean.csv"
DAILY_IN = "youtube_product_daily_clean.csv"
RECENT_IN = "youtube_recent_window_clean.csv"
ALIGNED_OUT = "aligned_daily.csv"
REPORT_OUT = "alignment_report.csv"

REGISTRY_FIELDS = ["product_name", "brand", "category", "product_type"]
TRENDS_FIELDS = ["search_interest", "is_below_threshold", "is_zero"]
TRENDS_FLAGS = ["is_provisional", "zero_share_product", "low_signal_product"]
YOUTUBE_FIELDS = ["query_version", "video_result_count", "video_views_total", "video_comments_total",
                  "videos_on_target", "views_on_target_total", "comments_on_target_total",
                  "on_target_share", "low_on_target_share", "result_cap_hit"]
RECENT_FIELDS = ["videos_published_7d", "videos_7d_on_target", "cap_hit"]
REPORT_HEADER = ["product_id", "trends_days", "trends_first", "trends_last", "trends_missing_values",
                 "youtube_snapshots", "youtube_matched", "youtube_unmatched", "youtube_coverage",
                 "recent_snapshots", "recent_matched", "recent_unmatched", "recent_coverage", "in_registry"]


def read(path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def source_frame(df, fields, rename=None):
    """(product_id, date) + fields; empty frame with those columns if the source is missing."""
    rename = rename or {}
    columns = ["product_id", "date"] + [rename.get(f, f) for f in fields]
    if df is None:
        return pd.DataFrame(columns=columns)
    out = df.rename(columns={"snapshot_date": "date", **rename})
    for col in columns:
        if col not in out:
            out[col] = ""
    return out[columns]


def coverage(matched, days):
    return f"{matched / days:.4f}" if days else ""


def build(trends, daily, recent, registry):
    spine = trends[["product_id", "date"] + TRENDS_FIELDS].copy()
    spine["trends_observed"] = spine["search_interest"].str.strip() != ""
    values = pd.to_numeric(spine["search_interest"].str.strip().replace("", None), errors="coerce")
    spine["is_provisional"] = spine["date"] == spine.groupby("product_id")["date"].transform("max")
    zero_days = (values == 0).groupby(spine["product_id"]).transform("sum")
    valued_days = values.notna().groupby(spine["product_id"]).transform("sum")
    spine["zero_share_product"] = (zero_days / valued_days.where(valued_days > 0)).round(4)
    spine["low_signal_product"] = spine["zero_share_product"] > config.LOW_SIGNAL_ZERO_SHARE

    yt = source_frame(daily, YOUTUBE_FIELDS, {"query_version": "youtube_query_version"})
    yt["youtube_observed"] = True
    rc = source_frame(recent, RECENT_FIELDS, {"cap_hit": "recent_cap_hit"})
    rc["youtube_recent_observed"] = True

    aligned = (spine.merge(yt, on=["product_id", "date"], how="left")
                    .merge(rc, on=["product_id", "date"], how="left"))
    aligned["youtube_observed"] = aligned["youtube_observed"].eq(True)
    aligned["youtube_recent_observed"] = aligned["youtube_recent_observed"].eq(True)
    reg = pd.DataFrame(
        [{"product_id": pid, **{f: p[f] for f in REGISTRY_FIELDS}} for pid, p in registry.items()],
        columns=["product_id"] + REGISTRY_FIELDS)
    aligned = aligned.merge(reg, on="product_id", how="left")
    order = (["product_id", "date"] + REGISTRY_FIELDS + ["trends_observed"] + TRENDS_FIELDS + TRENDS_FLAGS
             + ["youtube_observed", "youtube_query_version"] + YOUTUBE_FIELDS[1:]
             + ["youtube_recent_observed", "videos_published_7d", "videos_7d_on_target", "recent_cap_hit"])
    aligned = aligned[order].sort_values(["product_id", "date"], kind="stable").reset_index(drop=True)

    products = sorted(set(spine["product_id"]) | set(yt["product_id"]) | set(rc["product_id"]))
    report = []
    for pid in products:
        t = spine[spine["product_id"] == pid]
        t_dates = set(t["date"])
        y_dates = set(yt.loc[yt["product_id"] == pid, "date"])
        r_dates = set(rc.loc[rc["product_id"] == pid, "date"])
        report.append({
            "product_id": pid, "trends_days": len(t_dates),
            "trends_first": min(t_dates) if t_dates else "", "trends_last": max(t_dates) if t_dates else "",
            "trends_missing_values": int((~t["trends_observed"]).sum()),
            "youtube_snapshots": len(y_dates), "youtube_matched": len(y_dates & t_dates),
            "youtube_unmatched": len(y_dates - t_dates), "youtube_coverage": coverage(len(y_dates & t_dates), len(t_dates)),
            "recent_snapshots": len(r_dates), "recent_matched": len(r_dates & t_dates),
            "recent_unmatched": len(r_dates - t_dates), "recent_coverage": coverage(len(r_dates & t_dates), len(t_dates)),
            "in_registry": pid in registry,
        })
    totals = {k: sum(r[k] for r in report) for k in REPORT_HEADER
              if k not in ("product_id", "trends_first", "trends_last", "youtube_coverage", "recent_coverage",
                           "in_registry")}
    report.append({"product_id": "ALL", **totals,
                   "trends_first": min((r["trends_first"] for r in report if r["trends_first"]), default=""),
                   "trends_last": max((r["trends_last"] for r in report if r["trends_last"]), default=""),
                   "youtube_coverage": coverage(totals["youtube_matched"], totals["trends_days"]),
                   "recent_coverage": coverage(totals["recent_matched"], totals["trends_days"]),
                   "in_registry": ""})
    return aligned, pd.DataFrame(report, columns=REPORT_HEADER)


def main(paths=config.PATHS):
    trends = read(paths.processed_dir / TRENDS_IN)
    if trends is None:
        print(f"Cleaned Google Trends data ({paths.processed_dir / TRENDS_IN}) is not available yet; nothing "
              "to align. Download the exports, run src/load_trends.py and src/clean_trends.py, then run this again.")
        return 0
    daily = read(paths.processed_dir / DAILY_IN)
    recent = read(paths.processed_dir / RECENT_IN)
    if daily is None:
        print(f"Note: {DAILY_IN} not found; YouTube columns will be empty. Run src/clean_youtube.py.")
    registry = {p["product_id"]: p for p in pd.read_csv(paths.active_registry_csv, dtype=str,
                                                         keep_default_na=False).to_dict("records")}
    aligned, report = build(trends, daily, recent, registry)
    aligned.to_csv(paths.processed_dir / ALIGNED_OUT, index=False, lineterminator="\n")
    report.to_csv(paths.processed_dir / REPORT_OUT, index=False, lineterminator="\n")
    total = report[report["product_id"] == "ALL"].iloc[0]
    print(f"  {ALIGNED_OUT}: {len(aligned)} rows ({aligned['product_id'].nunique()} products)")
    print(f"  YouTube snapshots matched {total['youtube_matched']}, unmatched {total['youtube_unmatched']}; "
          f"recent matched {total['recent_matched']}, unmatched {total['recent_unmatched']}")
    print(f"Report: {paths.processed_dir / REPORT_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
