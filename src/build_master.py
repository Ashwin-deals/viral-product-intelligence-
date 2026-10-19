"""Consolidate every project source into one validated master dataset (SCRUM-21).

Inputs (never modified):
  data/processed/aligned_daily.csv           Trends daily index + same-day YouTube snapshot (src/build_aligned.py)
  data/processed/youtube_videos_clean.csv    cleaned videos with publish times (src/clean_youtube.py)
  data/processed/youtube_comments_clean.csv  cleaned comments with publish times and language flags
  data/processed/full_report.csv             each product's latest YouTube snapshot (src/full_report.py)
  active registry (config.ACTIVE_REGISTRY)   launch_period, trends_query
  processed/features_encoded_scaled.csv      Unit 2 features (optional; only used to cross-check surge_label)
Outputs:
  processed/master_dataset.csv            one row per product per day; written only if no check fails
  processed/master_dataset_sample.csv     6 real rows (2 per product_type) from the YouTube snapshot day
  processed/master_validation_report.csv  every check with PASS / FAIL / INFO and its numbers

What it adds to aligned_daily.csv:
- launch_period from the registry.
- Each product's latest YouTube snapshot as product-level yt_* columns (same value on every row of the
  product). aligned_daily.csv joins snapshots by date, so only the 10 pilot products (snapshot
  2026-10-03) have YouTube values there; the other 50 were collected on 2026-10-04, after the Trends
  window ends. The yt_* columns give all 60 products their snapshot.
- Daily YouTube activity rebuilt from publish times (India date): videos_published,
  videos_published_on_target (title names the exact model, src/title_match.py), comments_published,
  comments_published_usable (not emoji-only and not likely non-English). The snapshot columns exist
  on one date only; these give YouTube a daily series. They count the collected sample (top-ranked
  videos and their top comments), not all of YouTube; a day with nothing in the sample is 0.
- The Unit 2 target from feature_encoding_scaling.engineer_features (same code, not re-implemented):
  search_interest_growth, search_interest_accel, surge_label. Unscaled here.

Exit code 1 if an input is missing or any check fails.

Usage: python src/build_master.py
"""

import sys

import pandas as pd

import config
from feature_encoding_scaling import engineer_features
from title_match import title_classifier

ALIGNED_IN = "aligned_daily.csv"
VIDEOS_IN = "youtube_videos_clean.csv"
COMMENTS_IN = "youtube_comments_clean.csv"
FULL_REPORT_IN = "full_report.csv"
UNIT2_IN = "features_encoded_scaled.csv"
MASTER_OUT = "master_dataset.csv"
SAMPLE_OUT = "master_dataset_sample.csv"
REPORT_OUT = "master_validation_report.csv"

LOCAL_TZ = "Asia/Kolkata"  # Trends days and snapshot dates are India days; publish times are UTC
ACTIVITY_COLS = ["videos_published", "videos_published_on_target", "comments_published",
                 "comments_published_usable"]
TARGET_COLS = ["search_interest_growth", "search_interest_accel", "surge_label"]
# full_report.csv column -> master column (product-level latest snapshot)
SNAPSHOT_COLS = {"youtube_latest_snapshot": "yt_snapshot_date", "videos_on_target": "yt_videos_on_target",
                 "views_on_target_total": "yt_views_on_target_total", "on_target_share": "yt_on_target_share",
                 "low_on_target_share": "yt_low_on_target_share", "videos_7d_on_target": "yt_videos_7d_on_target",
                 "usable": "usable_product"}
COUNT_COLS = ["video_result_count", "video_views_total", "video_comments_total", "videos_on_target",
              "views_on_target_total", "comments_on_target_total", "videos_published_7d",
              "videos_7d_on_target", "yt_videos_on_target", "yt_views_on_target_total",
              "yt_videos_7d_on_target"] + ACTIVITY_COLS


def is_true(series):
    """Works for bool columns and for 'True'/'False' text (an empty value is False)."""
    return series.astype(str).eq("True")


def local_day(timestamps):
    return pd.to_datetime(timestamps, utc=True).dt.tz_convert(LOCAL_TZ).dt.strftime("%Y-%m-%d")


def daily_activity(videos, comments, registry):
    """Videos and comments in the sample per (product_id, publish day). Includes days outside the window."""
    # A video or comment seen in two snapshots is counted once.
    videos = videos.drop_duplicates(["product_id", "video_id"]).copy()
    comments = comments.drop_duplicates(["product_id", "comment_id"]).copy()
    classifiers = {pid: title_classifier(p["trends_query"]) for pid, p in registry.items()}
    videos["on_target"] = [pid in classifiers and classifiers[pid](title) == "on_target"
                           for pid, title in zip(videos["product_id"], videos["title"])]
    videos["date"] = local_day(videos["published_at"])
    comments["date"] = local_day(comments["published_at"])
    comments["usable"] = ~(is_true(comments["is_emoji_only"]) | is_true(comments["likely_non_english"]))
    v = videos.groupby(["product_id", "date"]).agg(videos_published=("video_id", "size"),
                                                   videos_published_on_target=("on_target", "sum"))
    c = comments.groupby(["product_id", "date"]).agg(comments_published=("comment_id", "size"),
                                                     comments_published_usable=("usable", "sum"))
    return v.join(c, how="outer").fillna(0).astype(int).reset_index()


def build(aligned, videos, comments, full_report, registry):
    launch = pd.DataFrame([{"product_id": pid, "launch_period": p["launch_period"]} for pid, p in registry.items()],
                          columns=["product_id", "launch_period"])
    snapshot = full_report[["product_id"] + list(SNAPSHOT_COLS)].rename(columns=SNAPSHOT_COLS)
    activity = daily_activity(videos, comments, registry)
    master = (aligned.merge(launch, on="product_id", how="left")
                     .merge(snapshot, on="product_id", how="left")
                     .merge(activity, on=["product_id", "date"], how="left"))
    master[ACTIVITY_COLS] = master[ACTIVITY_COLS].fillna(0).astype(int)
    master = engineer_features(master)  # sorts by product_id, date
    order = list(aligned.columns)
    order.insert(order.index("product_type") + 1, "launch_period")
    return master[order + list(SNAPSHOT_COLS.values()) + ACTIVITY_COLS + TARGET_COLS], activity


def validate(master, activity, raw_rows, registry, unit2):
    checks = []

    def check(name, ok, detail, info=False):
        checks.append({"check": name, "status": "INFO" if info else ("PASS" if ok else "FAIL"), "detail": detail})

    n_products = master["product_id"].nunique()
    dates = sorted(master["date"].unique())
    check("rows = products x days", len(master) == n_products * len(dates),
          f"{len(master)} rows = {n_products} products x {len(dates)} days ({dates[0]} to {dates[-1]})")
    dupes = int(master.duplicated(["product_id", "date"]).sum())
    check("no duplicate (product_id, date)", dupes == 0, f"{dupes} duplicate keys")
    missing = sorted(set(registry) - set(master["product_id"]))
    extra = sorted(set(master["product_id"]) - set(registry))
    check("every registry product present, nothing else", not missing and not extra,
          f"{n_products} products; missing from data: {missing or 'none'}; not in registry: {extra or 'none'}")
    calendar = set(pd.date_range(dates[0], dates[-1]).strftime("%Y-%m-%d"))
    gaps = {pid: len(calendar - set(d)) for pid, d in master.groupby("product_id")["date"]}
    gaps = {pid: n for pid, n in gaps.items() if n}
    check("every product has every calendar day", not gaps,
          f"{len(calendar)} calendar days; products with gaps: {gaps or 'none'}")

    si = master["search_interest"]
    check("search_interest within 0-100", bool(si.dropna().between(0, 100).all()),
          f"min {si.min()}, max {si.max()}, missing {int(si.isna().sum())}")
    negative = {c: int((master[c] < 0).sum()) for c in COUNT_COLS if (master[c] < 0).any()}
    check("count columns never negative", not negative,
          f"{len(COUNT_COLS)} count columns checked; negative values: {negative or 'none'}")
    for col in ("on_target_share", "yt_on_target_share", "zero_share_product"):
        check(f"{col} within 0-1", bool(master[col].dropna().between(0, 1).all()),
              f"min {master[col].min()}, max {master[col].max()}")
    labels = master["surge_label"]
    check("surge_label is 0 or 1", bool(labels.isin([0, 1]).all()),
          f"{int(labels.sum())} surge days of {len(labels)} ({labels.mean():.2%})")

    with_snapshot = master.loc[master["yt_snapshot_date"].notna(), "product_id"].nunique()
    snap_dates = ", ".join(sorted(master["yt_snapshot_date"].dropna().astype(str).unique()))
    check("YouTube snapshot (yt_* columns) for every product", with_snapshot == n_products,
          f"{with_snapshot} of {n_products} products; latest snapshot date(s): {snap_dates or 'none'}")
    dated = master[is_true(master["youtube_observed"])]
    check("date-level YouTube columns from aligned_daily", True,
          f"only {dated['product_id'].nunique()} of {n_products} products, on "
          f"{', '.join(sorted(dated['date'].unique())) or 'no date'}: snapshots taken after the Trends window "
          "ends do not join by date. Use the yt_* columns for all products", info=True)
    for col, what in (("videos_published", "videos"), ("comments_published", "comments")):
        in_window, total = int(master[col].sum()), int(activity[col].sum())
        check(f"{what} reconcile with source", 0 <= in_window <= total <= raw_rows[what],
              f"{raw_rows[what]} rows in the cleaned table = {total} unique {what} + "
              f"{raw_rows[what] - total} seen again in a second snapshot; {in_window} published inside the window, "
              f"{total - in_window} earlier (not in the daily rows)")

    if unit2 is None:
        check("surge_label matches Unit 2 features file", True,
              f"processed/{UNIT2_IN} not found; skipped", info=True)
    else:
        both = master[["product_id", "date", "surge_label"]].merge(
            unit2[["product_id", "date", "surge_label"]], on=["product_id", "date"], suffixes=("", "_unit2"))
        differ = int((both["surge_label"] != both["surge_label_unit2"]).sum())
        check("surge_label matches Unit 2 features file", differ == 0 and len(both) == len(master),
              f"{len(both)} of {len(master)} rows matched by key, {differ} labels differ")

    low_signal = sorted(master.loc[is_true(master["low_signal_product"]), "product_id"].unique())
    low_on_target = sorted(master.loc[is_true(master["yt_low_on_target_share"]), "product_id"].unique())
    check("flagged products (kept, not dropped)", True,
          f"low-signal Trends: {len(low_signal)}; low YouTube on-target share: {len(low_on_target)} "
          f"({', '.join(low_on_target) or 'none'})", info=True)
    gaps_by_design = master.isna().sum()
    gaps_by_design = gaps_by_design[gaps_by_design > 0]
    check("missing values (by design, not filled)", True,
          "; ".join(f"{c}: {n}" for c, n in gaps_by_design.items()) +
          ". YouTube snapshot columns exist only on the snapshot day (no forward fill); growth and accel "
          "are empty on each product's first day and after a zero day", info=True)
    return pd.DataFrame(checks, columns=["check", "status", "detail"])


def load_inputs(paths):
    """aligned, videos, comments, full_report, registry, unit2 (None if the Unit 2 file is absent)."""
    proc, out_dir = paths.processed_dir, paths.root / "processed"
    aligned = pd.read_csv(proc / ALIGNED_IN)
    videos = pd.read_csv(proc / VIDEOS_IN, usecols=["product_id", "video_id", "title", "published_at"],
                         dtype=str, keep_default_na=False, encoding="utf-8")
    comments = pd.read_csv(proc / COMMENTS_IN, usecols=["product_id", "comment_id", "published_at",
                                                        "is_emoji_only", "likely_non_english"],
                           dtype=str, keep_default_na=False, encoding="utf-8")
    full_report = pd.read_csv(proc / FULL_REPORT_IN)
    registry = {p["product_id"]: p for p in pd.read_csv(paths.active_registry_csv, dtype=str,
                                                         keep_default_na=False).to_dict("records")}
    unit2_path = out_dir / UNIT2_IN
    unit2 = pd.read_csv(unit2_path, usecols=["product_id", "date", "surge_label"]) if unit2_path.exists() else None
    return aligned, videos, comments, full_report, registry, unit2


def main(paths=config.PATHS):
    proc, out_dir = paths.processed_dir, paths.root / "processed"
    needed = [proc / ALIGNED_IN, proc / VIDEOS_IN, proc / COMMENTS_IN, proc / FULL_REPORT_IN]
    absent = [str(p) for p in needed if not p.exists()]
    if absent:
        print(f"Missing input(s): {', '.join(absent)}. Run src/clean_youtube.py, src/build_aligned.py and "
              "src/full_report.py first.", file=sys.stderr)
        return 1
    aligned, videos, comments, full_report, registry, unit2 = load_inputs(paths)
    master, activity = build(aligned, videos, comments, full_report, registry)
    report = validate(master, activity, {"videos": len(videos), "comments": len(comments)}, registry, unit2)
    out_dir.mkdir(parents=True, exist_ok=True)
    report.to_csv(out_dir / REPORT_OUT, index=False, lineterminator="\n")
    for row in report.itertuples():
        print(f"  [{row.status}] {row.check}: {row.detail}")
    if (report["status"] == "FAIL").any():
        print(f"Validation failed; {MASTER_OUT} not written. See {out_dir / REPORT_OUT}", file=sys.stderr)
        return 1
    master.to_csv(out_dir / MASTER_OUT, index=False, lineterminator="\n")
    sample = master[is_true(master["youtube_observed"])].groupby("product_type", sort=True).head(2)
    sample.to_csv(out_dir / SAMPLE_OUT, index=False, lineterminator="\n")
    print(f"{MASTER_OUT}: {len(master)} rows x {len(master.columns)} columns -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
