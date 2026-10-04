"""Per-product status report for every product in the active registry (all 60), read-only.

Inputs (each optional; a missing input leaves its columns empty and is named in the summary):
  data/processed/aligned_daily.csv             Trends days, provisional and low-signal flags
  data/processed/youtube_product_daily_clean.csv  YouTube main pass (active query versions)
  data/processed/youtube_videos_clean.csv      videos per product (latest snapshot)
  data/processed/youtube_comments_clean.csv    comments per product (latest snapshot)
  data/processed/youtube_recent_window_clean.csv  7-day upload counts (latest snapshot)
Outputs:
  data/processed/full_report.csv   one row per product
  docs/full_report.md              summary: usable vs low-signal products and why

Definitions:
- zero_share: share of Trends days with search_interest exactly 0, among days with a value.
- peak_date: first day with the product's maximum value (each series is scaled to its own peak,
  so peak_value is 100 whenever the product has any search interest).
- last_7d_mean: mean of the last 7 complete days, i.e. excluding the provisional last day.
- YouTube columns come from each product's latest main-pass snapshot.
- usable = Trends present and not low-signal, and YouTube present with on_target_share >= 60%.
  Every other product lists its reasons in `issues`. Nothing is dropped or filled in.

The output depends only on the inputs, so re-running gives identical files.

Usage: python src/full_report.py
"""

import sys

import pandas as pd

import config
from common import read_products

REPORT_CSV = "full_report.csv"
REPORT_MD = "full_report.md"
COLUMNS = [
    "product_id", "product_name", "brand", "product_type", "is_pilot",
    "trends_rows", "trends_first", "trends_last", "zero_share", "low_signal_product",
    "peak_value", "peak_date", "mean_interest", "last_7d_mean",
    "youtube_snapshots", "youtube_latest_snapshot", "youtube_query_version", "youtube_videos",
    "youtube_comments", "comments_emoji_only_share", "comments_non_english_share",
    "video_views_total", "views_on_target_total", "videos_on_target", "on_target_share",
    "low_on_target_share", "videos_published_7d", "videos_7d_on_target", "recent_cap_hit",
    "usable", "issues",
]


def read(path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    return pd.read_csv(path)


def latest(df, key="snapshot_date"):
    """Rows of each product's latest snapshot."""
    if df is None or df.empty:
        return df
    return df[df[key] == df.groupby("product_id")[key].transform("max")]


def trends_stats(aligned):
    if aligned is None:
        return pd.DataFrame(columns=["product_id"])
    rows = []
    for pid, g in aligned.sort_values("date").groupby("product_id"):
        values = g["search_interest"]
        complete = g.loc[~g["is_provisional"].astype(bool), "search_interest"] if "is_provisional" in g else values
        has_values = values.notna().any()
        rows.append({
            "product_id": pid,
            "trends_rows": len(g),
            "trends_first": g["date"].min(),
            "trends_last": g["date"].max(),
            "zero_share": round(float((values == 0).sum() / values.notna().sum()), 4) if has_values else None,
            "low_signal_product": bool(g["low_signal_product"].iloc[0]) if "low_signal_product" in g else None,
            "peak_value": values.max() if has_values else None,
            "peak_date": g.loc[values.idxmax(), "date"] if has_values else None,
            "mean_interest": round(float(values.mean()), 2) if has_values else None,
            "last_7d_mean": round(float(complete.dropna().iloc[-7:].mean()), 2) if complete.notna().any() else None,
        })
    return pd.DataFrame(rows)


def youtube_stats(daily, videos, comments, recent):
    frames = []
    if daily is not None and not daily.empty:
        snaps = daily.groupby("product_id")["snapshot_date"].nunique().rename("youtube_snapshots")
        d = latest(daily).set_index("product_id")
        frames.append(snaps)
        frames.append(d.rename(columns={"snapshot_date": "youtube_latest_snapshot",
                                        "query_version": "youtube_query_version"})[
            ["youtube_latest_snapshot", "youtube_query_version", "video_views_total", "views_on_target_total",
             "videos_on_target", "on_target_share", "low_on_target_share"]])
    if videos is not None and not videos.empty:
        frames.append(latest(videos).groupby("product_id")["video_id"].nunique().rename("youtube_videos"))
    if comments is not None and not comments.empty:
        c = latest(comments)
        g = c.groupby("product_id")
        frames.append(g["comment_id"].nunique().rename("youtube_comments"))
        frames.append(g["is_emoji_only"].mean().round(4).rename("comments_emoji_only_share"))
        frames.append(g["likely_non_english"].mean().round(4).rename("comments_non_english_share"))
    if recent is not None and not recent.empty:
        r = latest(recent).set_index("product_id")
        frames.append(r.rename(columns={"cap_hit": "recent_cap_hit"})[
            ["videos_published_7d", "videos_7d_on_target", "recent_cap_hit"]])
    if not frames:
        return pd.DataFrame(columns=["product_id"])
    return pd.concat(frames, axis=1).reset_index().rename(columns={"index": "product_id"})


def issues_for(row):
    issues = []
    if pd.isna(row["trends_rows"]):
        issues.append("no Trends data")
    elif row["low_signal_product"] is True:
        issues.append(f"low signal (zero share {row['zero_share']:.0%})")
    if pd.isna(row["youtube_snapshots"]):
        issues.append("no YouTube data")
    elif row["low_on_target_share"] is True:
        issues.append(f"low on-target share ({row['on_target_share']:.0%})")
    return issues


def build(paths=config.PATHS):
    registry = pd.DataFrame(read_products(paths.active_registry_csv))
    pilot = set(pd.DataFrame(read_products(paths.pilot_products_csv))["product_id"]) \
        if paths.pilot_products_csv.exists() else set()
    aligned = read(paths.processed_dir / "aligned_daily.csv")
    daily = read(paths.processed_dir / "youtube_product_daily_clean.csv")
    videos = read(paths.processed_dir / "youtube_videos_clean.csv")
    comments = read(paths.processed_dir / "youtube_comments_clean.csv")
    recent = read(paths.processed_dir / "youtube_recent_window_clean.csv")
    missing_inputs = [name for name, df in [("aligned_daily.csv", aligned), ("youtube_product_daily_clean.csv", daily),
                                            ("youtube_videos_clean.csv", videos),
                                            ("youtube_comments_clean.csv", comments),
                                            ("youtube_recent_window_clean.csv", recent)] if df is None]

    report = registry[["product_id", "product_name", "brand", "product_type"]].copy()
    report["is_pilot"] = report["product_id"].isin(pilot)
    report = report.merge(trends_stats(aligned), on="product_id", how="left")
    report = report.merge(youtube_stats(daily, videos, comments, recent), on="product_id", how="left")
    for col in COLUMNS:
        if col not in report:
            report[col] = None
    report["low_signal_product"] = report["low_signal_product"].map(lambda v: v if isinstance(v, bool) else None)
    report["low_on_target_share"] = report["low_on_target_share"].map(
        lambda v: v if isinstance(v, bool) else (str(v).lower() == "true" if pd.notna(v) else None))
    report["issues"] = [", ".join(issues_for(r)) for _, r in report.iterrows()]
    report["usable"] = report["issues"] == ""
    for col in ("trends_rows", "youtube_snapshots", "youtube_videos", "youtube_comments", "videos_on_target",
                "video_views_total", "views_on_target_total", "videos_published_7d", "videos_7d_on_target"):
        report[col] = report[col].astype("Int64")
    return report[COLUMNS].sort_values("product_id").reset_index(drop=True), missing_inputs


def summary_markdown(report, missing_inputs):
    usable = report[report["usable"]]
    low_signal = report[report["low_signal_product"] == True]  # noqa: E712
    low_target = report[report["low_on_target_share"] == True]  # noqa: E712
    no_trends = report[report["trends_rows"].isna()]
    no_youtube = report[report["youtube_snapshots"].isna()]
    with_trends = report[report["trends_rows"].notna()]

    def ids(df, extra=None):
        if df.empty:
            return "none"
        return ", ".join(f"{r.product_id}" + (f" ({extra(r)})" if extra else "") for r in df.itertuples())

    lines = [
        "# Full product report (all products in the active registry)",
        "",
        f"Generated by `python3 src/full_report.py` from the cleaned and aligned tables; per-product rows are in "
        f"`data/processed/{REPORT_CSV}`. Re-running with the same inputs gives the same files.",
        "",
        "## Summary",
        "",
        f"- Products in the active registry ({config.ACTIVE_REGISTRY}): **{len(report)}**",
        f"- With Google Trends data: **{len(with_trends)}** "
        f"({with_trends['trends_rows'].sum()} daily rows, {with_trends['trends_first'].min()} to "
        f"{with_trends['trends_last'].max()})" if len(with_trends) else "- With Google Trends data: **0**",
        f"- With a YouTube main-pass snapshot: **{report['youtube_snapshots'].notna().sum()}**; "
        f"with a 7-day recent-pass count: **{report['videos_published_7d'].notna().sum()}**",
        f"- **Usable** (Trends not low-signal, YouTube on-target share at least {config.ON_TARGET_MIN_SHARE:.0%}): "
        f"**{len(usable)}**",
        f"- Low-signal Trends (zero share above {config.LOW_SIGNAL_ZERO_SHARE:.0%}): **{len(low_signal)}**",
        f"- Low YouTube on-target share (below {config.ON_TARGET_MIN_SHARE:.0%}): **{len(low_target)}**",
        "",
        "## Usable products",
        "",
        ids(usable),
        "",
        "## Low-signal Trends products (flagged, not dropped)",
        "",
        "Mostly products launched inside the window: their series is zero before launch, and pytrends returns integers, "
        "so a `<1` day also counts as 0. Their Trends values carry little information outside the launch period.",
        "",
        ids(low_signal.sort_values("zero_share", ascending=False), lambda r: f"{r.zero_share:.0%}, {r.product_type}"),
        "",
        "## Low YouTube on-target share (sibling-model noise)",
        "",
        ids(low_target.sort_values("on_target_share"), lambda r: f"{r.on_target_share:.0%}"),
        "",
        "## Missing sources",
        "",
        f"- No Trends data: {ids(no_trends)}",
        f"- No YouTube data: {ids(no_youtube)}",
        f"- Missing input files: {', '.join(missing_inputs) or 'none'}",
        "",
        "## By product type",
        "",
        "| product_type | products | usable | low-signal Trends | low on-target | median zero share | median on-target share |",
        "|---|--:|--:|--:|--:|--:|--:|",
    ]
    for ptype in ("RISING", "STABLE", "DECLINING"):
        g = report[report["product_type"] == ptype]
        zero = g["zero_share"].dropna()
        target = g["on_target_share"].dropna()
        lines.append(f"| {ptype} | {len(g)} | {int(g['usable'].sum())} | {int((g['low_signal_product'] == True).sum())} "  # noqa: E712
                     f"| {int((g['low_on_target_share'] == True).sum())} "  # noqa: E712
                     f"| {f'{zero.median():.0%}' if len(zero) else '–'} | {f'{target.median():.0%}' if len(target) else '–'} |")
    lines += [
        "",
        "## Read before using",
        "",
        "- Trends values are relative to each product's own peak (100), so levels and means are not comparable "
        "across products; shapes and changes within a product are.",
        "- `last_7d_mean` excludes the provisional last day (2026-10-03), which Google may still revise.",
        "- YouTube totals cover a relevance-ranked sample of at most 50 videos per snapshot; use the on-target columns "
        "for modelling. `video_result_count` is capped at 50 and carries no signal.",
        "",
    ]
    return "\n".join(lines)


def main(paths=config.PATHS):
    report, missing_inputs = build(paths)
    paths.processed_dir.mkdir(parents=True, exist_ok=True)
    report.to_csv(paths.processed_dir / REPORT_CSV, index=False, lineterminator="\n")
    (paths.root / "docs").mkdir(exist_ok=True)
    (paths.root / "docs" / REPORT_MD).write_text(summary_markdown(report, missing_inputs), encoding="utf-8")
    print(f"  {REPORT_CSV}: {len(report)} products; usable {int(report['usable'].sum())}, "
          f"low-signal Trends {int((report['low_signal_product'] == True).sum())}, "  # noqa: E712
          f"low on-target {int((report['low_on_target_share'] == True).sum())}, "  # noqa: E712
          f"no Trends {int(report['trends_rows'].isna().sum())}, no YouTube {int(report['youtube_snapshots'].isna().sum())}")
    if missing_inputs:
        print(f"  missing inputs: {', '.join(missing_inputs)}")
    print(f"Report: {paths.processed_dir / REPORT_CSV}; summary: docs/{REPORT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
