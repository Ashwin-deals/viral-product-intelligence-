"""Go/no-go report for the pilot: coverage, joins, quota and data-quality warnings.

Reads data/pilot_products.csv, data/trends_long.csv and data/youtube/*.csv. Missing files are
reported as missing, never as a crash. Prints the report; writes nothing.

Usage: python src/pilot_report.py
"""

import re
import sys

import pandas as pd

import config
from collect_youtube import estimate_product_cost

MIN_VIDEOS = 10
FLAT_SHARE = 0.5  # warn when at least half of a product's Trends days are 0 or "<1"
ON_TARGET_MIN_SHARE = 0.5  # warn when fewer than half of the video titles name the exact model
SIBLING_MAX_SHARE = 0.25  # warn when a quarter or more of the titles name a sibling model instead
# Words that, right after the search phrase, mean a sibling model (e.g. "vivo t5" + "pro").
VARIANT_WORDS = {"pro", "max", "plus", "ultra", "lite", "fusion", "neo", "fold", "xl", "fe", "mini",
                 "power", "prime", "edge", "flip", "air", "duo", "c", "e", "r", "s", "x"}
YOUTUBE_FAILURE_STATUSES = {"failed", "quota_exceeded", "aborted_budget"}


def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def latest_trends(trends):
    """Rows from each product's most recent export (the one the loader checks)."""
    if trends.empty:
        return trends
    latest = trends.groupby("product_id")["collection_date"].transform("max")
    return trends[trends["collection_date"] == latest]


def missing_days(dates):
    """Number of calendar days missing between the first and last date."""
    days = pd.to_datetime(pd.Series(sorted(set(dates))).str[:10])
    if len(days) < 2:
        return 0
    return int((days.iloc[-1] - days.iloc[0]).days + 1 - days.nunique())


def normalize_title(title):
    title = title.lower().replace("+", " plus ")
    return re.sub(r"[^a-z0-9]+", " ", title).strip()


def model_phrase(query):
    """Regex for the model in a title. Queries of 3+ words drop the leading brand/series word
    ("galaxy z flip 8" -> "z flip 8", "motorola edge 70" -> "edge 70") because titles often omit
    it, and spaces between words are optional ("flip8", "edge70")."""
    tokens = normalize_title(query).split()
    core = tokens[1:] if len(tokens) >= 3 else tokens
    return r"\s*".join(re.escape(t) for t in core)


def classify_titles(query, titles):
    """Share of titles that name the exact model, a sibling model, or neither."""
    phrase = model_phrase(query)
    exact = re.compile(rf"\b{phrase}\b(?!\s+(?:{'|'.join(sorted(VARIANT_WORDS))})\b)")
    any_mention = re.compile(rf"\b{phrase}\b")
    counts = {"on_target": 0, "sibling": 0, "off_topic": 0}
    for title in titles:
        t = normalize_title(title)
        if exact.search(t):
            counts["on_target"] += 1
        elif any_mention.search(t):
            counts["sibling"] += 1
        else:
            counts["off_topic"] += 1
    total = max(len(titles), 1)
    return {k: v / total for k, v in counts.items()}


def duplicate_count(df, keys):
    if df.empty:
        return 0
    return int(df.duplicated(list(keys)).sum())


def build_report(paths=config.PATHS):
    pilot = read_csv(paths.pilot_products_csv)
    if pilot.empty:
        raise SystemExit(f"{paths.pilot_products_csv} is missing; run python src/select_pilot.py first.")
    pilot_ids = set(pilot["product_id"])
    trends = read_csv(paths.trends_long_csv)
    trends = trends[trends["product_id"].isin(pilot_ids)] if not trends.empty else trends
    trends_latest = latest_trends(trends)
    videos = read_csv(paths.videos_snapshot_csv)
    comments = read_csv(paths.comments_snapshot_csv)
    daily = read_csv(paths.product_daily_csv)
    quota = read_csv(paths.quota_usage_csv)
    log = read_csv(paths.collection_log_csv)

    report = {"products": [], "warnings": [], "files": {}}
    for name, df, path in [("trends_long", trends, paths.trends_long_csv),
                           ("videos_snapshot", videos, paths.videos_snapshot_csv),
                           ("comments_snapshot", comments, paths.comments_snapshot_csv),
                           ("product_daily", daily, paths.product_daily_csv),
                           ("quota_usage", quota, paths.quota_usage_csv)]:
        report["files"][name] = "missing" if df.empty and not path.exists() else f"{len(df)} rows"

    latest_snapshot = daily["snapshot_date"].max() if not daily.empty else None
    for _, p in pilot.sort_values("product_id").iterrows():
        pid = p["product_id"]
        t = trends_latest[trends_latest["product_id"] == pid] if not trends_latest.empty else trends_latest
        v = videos[(videos["product_id"] == pid) & (videos["snapshot_date"] == latest_snapshot)] \
            if not videos.empty else videos
        c = comments[(comments["product_id"] == pid) & (comments["snapshot_date"] == latest_snapshot)] \
            if not comments.empty else comments
        d = daily[daily["product_id"] == pid] if not daily.empty else daily
        row = {
            "product_id": pid,
            "product_type": p["product_type"],
            "product_name": p["product_name"],
            "trends_days": int(t["date"].nunique()) if not t.empty else 0,
            "trends_range": f"{t['date'].min()}..{t['date'].max()}" if not t.empty else "",
            "youtube_snapshots": int(d["snapshot_date"].nunique()) if not d.empty else 0,
            "videos": int(v["video_id"].nunique()) if not v.empty else 0,
            "comments": int(c["comment_id"].nunique()) if not c.empty else 0,
            "on_target_share": None,
            "missing": [],
        }
        if t.empty:
            row["missing"].append("trends")
        if d.empty:
            row["missing"].append("youtube")
        elif c.empty:
            row["missing"].append("comments")
        report["products"].append(row)

        # warnings
        if not t.empty:
            interest = pd.to_numeric(t["search_interest"])
            low = ((interest == 0) | (t["is_below_threshold"] == "true")).mean()
            if interest.max() == 0 or interest.nunique() == 1:
                report["warnings"].append(f"{pid}: Trends series is flat (all values {interest.iloc[0]:g})")
            elif low >= FLAT_SHARE:
                report["warnings"].append(f"{pid}: {low:.0%} of Trends days are 0 or '<1' (low volume)")
            gaps = missing_days(t["date"])
            if gaps:
                report["warnings"].append(f"{pid}: Trends series is missing {gaps} day(s)")
            if set(t["granularity"]) != {config.TRENDS_EXPECTED_GRANULARITY}:
                report["warnings"].append(f"{pid}: Trends granularity {sorted(set(t['granularity']))}, "
                                          f"expected {config.TRENDS_EXPECTED_GRANULARITY}")
        if not d.empty and row["videos"] < MIN_VIDEOS:
            report["warnings"].append(f"{pid}: only {row['videos']} videos in the latest snapshot (< {MIN_VIDEOS})")
        if not v.empty:
            shares = classify_titles(p["trends_query"], v["title"].tolist())
            row["on_target_share"] = shares["on_target"]
            if shares["on_target"] < ON_TARGET_MIN_SHARE:
                report["warnings"].append(
                    f"{pid}: only {shares['on_target']:.0%} of video titles name {p['trends_query']!r} exactly "
                    f"({shares['sibling']:.0%} name a sibling model, {shares['off_topic']:.0%} neither)")
            elif shares["sibling"] >= SIBLING_MAX_SHARE:
                report["warnings"].append(
                    f"{pid}: {shares['sibling']:.0%} of video titles name a sibling model of "
                    f"{p['trends_query']!r} (e.g. Pro/Fusion/Max); YouTube totals mix models")
        if not d.empty:
            gaps = missing_days(d["snapshot_date"])
            if gaps:
                report["warnings"].append(f"{pid}: {gaps} day(s) without a YouTube snapshot between the first "
                                          "and last snapshot (expected for a weekly schedule)")

    for name, df, keys in [
        ("videos_snapshot", videos, ("snapshot_date", "product_id", "video_id")),
        ("comments_snapshot", comments, ("snapshot_date", "product_id", "comment_id")),
        ("product_daily", daily, ("snapshot_date", "product_id")),
        ("trends_long", trends, ("product_id", "date", "collection_date")),
    ]:
        dups = duplicate_count(df, keys)
        if dups:
            report["warnings"].append(f"{name}: {dups} duplicate key row(s) on {', '.join(keys)}")

    if not log.empty:
        yt_failures = log[(log["source"] == "youtube") & log["product_id"].isin(pilot_ids)
                          & log["status"].isin(YOUTUBE_FAILURE_STATUSES)]
        for _, r in yt_failures.iterrows():
            report["warnings"].append(f"{r['product_id']}: YouTube {r['status']} at {r['run_timestamp']}")
        report["youtube_failures"] = len(yt_failures)
    else:
        report["youtube_failures"] = 0

    # join check: Trends (product_id, date) vs YouTube product_daily (product_id, snapshot_date)
    trends_keys = set(zip(trends_latest["product_id"], trends_latest["date"])) if not trends_latest.empty else set()
    yt_keys = set(zip(daily["product_id"], daily["snapshot_date"])) if not daily.empty else set()
    yt_keys = {k for k in yt_keys if k[0] in pilot_ids}
    report["join"] = {
        "trends_rows": len(trends_keys),
        "youtube_rows": len(yt_keys),
        "matched": len(trends_keys & yt_keys),
        "youtube_unmatched": len(yt_keys - trends_keys),
        "trends_unmatched": len(trends_keys - yt_keys),
        "products_in_both": len({k[0] for k in trends_keys} & {k[0] for k in yt_keys}),
    }

    # quota
    if not quota.empty:
        quota = quota.assign(units=pd.to_numeric(quota["units"]), search_calls=pd.to_numeric(quota["search_calls"]))
        per_day = quota.groupby("date")[["units", "search_calls"]].sum()
        report["quota_by_day"] = {d: (int(r.units), int(r.search_calls)) for d, r in per_day.iterrows()}
    else:
        report["quota_by_day"] = {}
    measured = None
    if not log.empty:
        ok = log[(log["source"] == "youtube") & log["status"].isin({"success", "comments_disabled"})]
        if not ok.empty:
            measured = pd.to_numeric(ok["quota_units"]).mean()
    est_units, est_search = estimate_product_cost(
        config.YOUTUBE_MAX_VIDEOS, config.YOUTUBE_COMMENT_VIDEOS, config.YOUTUBE_COMMENTS_PER_VIDEO)
    units_per_product = measured if measured is not None else est_units
    n_all = len(read_csv(paths.products_csv)) or 60
    per_run_units = n_all * units_per_product
    per_run_search = n_all * est_search
    max_products_per_day = int(min(config.DEFAULT_DAILY_BUDGET_UNITS // units_per_product,
                                   config.DEFAULT_DAILY_SEARCH_CALL_BUDGET // est_search))
    report["quota"] = {
        "units_per_product": units_per_product,
        "units_per_product_source": "measured in pilot" if measured is not None else "estimate",
        "search_calls_per_product": est_search,
        "all_products": n_all,
        "per_snapshot_units": per_run_units,
        "per_snapshot_search_calls": per_run_search,
        "weekly_units_per_week": per_run_units,
        "daily_units_per_week": per_run_units * 7,
        "budget_units": config.DEFAULT_DAILY_BUDGET_UNITS,
        "budget_search_calls": config.DEFAULT_DAILY_SEARCH_CALL_BUDGET,
        "max_products_per_day": max_products_per_day,
        "fits_in_one_day": per_run_units <= config.DEFAULT_DAILY_BUDGET_UNITS
                           and per_run_search <= config.DEFAULT_DAILY_SEARCH_CALL_BUDGET,
    }

    # verdicts
    youtube_ok = all(r["videos"] >= MIN_VIDEOS for r in report["products"]) and report["youtube_failures"] == 0 \
        and not any("duplicate" in w for w in report["warnings"])
    trends_missing = sum("trends" in r["missing"] for r in report["products"])
    report["verdict"] = {
        "youtube": "GO" if youtube_ok else "CHECK",
        "trends": "PENDING" if trends_missing else
                  ("GO" if not any("Trends" in w for w in report["warnings"]) else "CHECK"),
        "trends_missing": trends_missing,
        "join": "GO" if report["join"]["matched"] else "PENDING",
    }
    return report


def format_report(report):
    lines = ["PILOT REPORT", "=" * 12, ""]
    v = report["verdict"]
    lines.append(f"YouTube: {v['youtube']}   Google Trends: {v['trends']}"
                 + (f" ({v['trends_missing']} of {len(report['products'])} exports missing)" if v["trends_missing"] else "")
                 + f"   Join: {v['join']}")
    lines.append("")
    lines.append("Files: " + ", ".join(f"{k} {s}" for k, s in report["files"].items()))
    lines.append("")
    lines.append(f"{'product':<8}{'type':<10}{'trends days':<13}{'yt snaps':<10}{'videos':<8}"
                 f"{'comments':<10}{'on-target':<11}missing")
    for r in report["products"]:
        share = f"{r['on_target_share']:.0%}" if r["on_target_share"] is not None else "-"
        lines.append(f"{r['product_id']:<8}{r['product_type']:<10}{r['trends_days']:<13}{r['youtube_snapshots']:<10}"
                     f"{r['videos']:<8}{r['comments']:<10}{share:<11}{', '.join(r['missing']) or '-'}")
    lines.append("(on-target = share of video titles naming the exact model rather than a sibling or nothing)")

    j = report["join"]
    lines += ["", "Join check (product_id + date): Trends daily rows vs YouTube product_daily rows",
              f"  Trends rows {j['trends_rows']}, YouTube rows {j['youtube_rows']}, matched {j['matched']}, "
              f"YouTube unmatched {j['youtube_unmatched']}, Trends unmatched {j['trends_unmatched']}, "
              f"products in both {j['products_in_both']}",
              "  Note: Trends covers the past ~9 months while YouTube snapshots start on the first run, so"
              " only snapshot dates inside the Trends window can match."]

    q = report["quota"]
    lines += ["", "Quota"]
    for day, (units, calls) in sorted(report["quota_by_day"].items()):
        lines.append(f"  {day} (Pacific): {units}/{q['budget_units']} units, "
                     f"{calls}/{q['budget_search_calls']} search.list calls")
    if not report["quota_by_day"]:
        lines.append("  no quota used yet")
    lines += [
        f"  Per product: {q['units_per_product']:g} units ({q['units_per_product_source']}), "
        f"{q['search_calls_per_product']} search.list call(s)",
        f"  All {q['all_products']} products, one snapshot: {q['per_snapshot_units']:g} units, "
        f"{q['per_snapshot_search_calls']} search.list calls -> "
        f"{'fits in one day' if q['fits_in_one_day'] else 'needs more than one day'}",
        f"  Weekly snapshots: {q['weekly_units_per_week']:g} units/week; "
        f"daily snapshots: {q['per_snapshot_units']:g} units/day ({q['daily_units_per_week']:g}/week)",
        f"  Binding limit: at most {q['max_products_per_day']} products/day "
        f"(search.list call cap {q['budget_search_calls']}/day, unit budget {q['budget_units']}/day)",
    ]

    lines += ["", "Warnings"]
    lines += [f"  - {w}" for w in report["warnings"]] or ["  none"]
    return "\n".join(lines)


def main(paths=config.PATHS):
    print(format_report(build_report(paths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
