import csv

import pilot_report
from conftest import PRODUCTS


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def setup_pilot(paths):
    write_csv(paths.pilot_products_csv, PRODUCTS)


def trends_rows(pid, values, start_day=1, skip=()):
    return [
        {"product_id": pid, "date": f"2026-10-{start_day + i:02d}", "search_interest": str(v),
         "granularity": "daily", "is_below_threshold": "true" if v == 0.5 else "false",
         "source_file": f"{pid}_2026-10-03.csv", "collection_date": "2026-10-03"}
        for i, v in enumerate(values) if i not in skip
    ]


def youtube(paths, pid, n_videos, snapshot="2026-10-03", titles=None):
    videos = [{"snapshot_date": snapshot, "product_id": pid, "video_id": f"{pid}v{i}",
               "title": (titles or [f"Video {i}"] * n_videos)[i], "channel_id": "c", "published_at": "",
               "view_count": "10", "like_count": "1", "comment_count": "1"} for i in range(n_videos)]
    comments = [{"snapshot_date": snapshot, "product_id": pid, "video_id": f"{pid}v0", "comment_id": f"{pid}c{i}",
                 "text": "hi", "like_count": "0", "published_at": ""} for i in range(3)]
    daily = [{"snapshot_date": snapshot, "product_id": pid, "video_result_count": str(n_videos),
              "video_views_total": "0", "video_comments_total": "0"}]
    return videos, comments, daily


def test_runs_with_only_pilot_file(paths):
    setup_pilot(paths)

    report = pilot_report.build_report(paths)
    text = pilot_report.format_report(report)

    assert report["verdict"]["trends"] == "PENDING" and report["verdict"]["trends_missing"] == 2
    assert all(r["missing"] == ["trends", "youtube"] for r in report["products"])
    assert report["files"]["trends_long"] == "missing"
    assert report["join"]["matched"] == 0
    assert "no quota used yet" in text
    assert report["quota"]["units_per_product_source"] == "estimate"


def test_youtube_without_trends_reports_trends_missing(paths):
    setup_pilot(paths)
    rows = [youtube(paths, pid, 12) for pid in ("P001", "P002")]
    write_csv(paths.videos_snapshot_csv, rows[0][0] + rows[1][0])
    write_csv(paths.comments_snapshot_csv, rows[0][1] + rows[1][1])
    write_csv(paths.product_daily_csv, rows[0][2] + rows[1][2])

    report = pilot_report.build_report(paths)

    assert [r["missing"] for r in report["products"]] == [["trends"], ["trends"]]
    assert [r["videos"] for r in report["products"]] == [12, 12]
    assert report["verdict"]["youtube"] == "GO"
    assert report["join"] == {"trends_rows": 0, "youtube_rows": 2, "matched": 0, "youtube_unmatched": 2,
                              "trends_unmatched": 0, "products_in_both": 0}


def test_join_counts_and_warnings(paths):
    setup_pilot(paths)
    # P001: 5 daily points with a gap on day 3; P002: flat zero series
    write_csv(paths.trends_long_csv, trends_rows("P001", [10, 20, 30, 40, 50], skip={2})
              + trends_rows("P002", [0, 0, 0, 0, 0]))
    v1, c1, d1 = youtube(paths, "P001", 12, snapshot="2026-10-04")
    v2, c2, d2 = youtube(paths, "P002", 4, snapshot="2026-10-04")
    write_csv(paths.videos_snapshot_csv, v1 + v2 + v2[:1])  # one duplicate key
    write_csv(paths.comments_snapshot_csv, c1 + c2)
    write_csv(paths.product_daily_csv, d1 + d2)
    write_csv(paths.quota_usage_csv, [{"date": "2026-10-03", "units": "7", "search_calls": "1",
                                       "method": "search.list", "run_timestamp": "r"}])
    write_csv(paths.collection_log_csv, [
        {"run_timestamp": "t", "source": "youtube", "product_id": "P001", "records_collected": "1",
         "status": "success", "error_message": "", "quota_units": "7"},
        {"run_timestamp": "t", "source": "youtube", "product_id": "P002", "records_collected": "0",
         "status": "failed", "error_message": "x", "quota_units": "1"},
    ])

    report = pilot_report.build_report(paths)
    warnings = "\n".join(report["warnings"])

    assert report["join"]["matched"] == 2  # both products have a 2026-10-04 Trends day and snapshot
    assert report["join"]["trends_unmatched"] == 4 + 5 - 2
    assert [r["trends_days"] for r in report["products"]] == [4, 5]
    assert "P001: Trends series is missing 1 day(s)" in warnings
    assert "P002: Trends series is flat" in warnings
    assert "P002: only 4 videos" in warnings
    assert "videos_snapshot: 1 duplicate key row(s)" in warnings
    assert "P002: YouTube failed" in warnings
    assert report["verdict"]["youtube"] == "CHECK"
    assert report["verdict"]["trends"] == "CHECK"
    assert report["quota_by_day"] == {"2026-10-03": (7, 1)}
    assert report["quota"]["units_per_product"] == 7 and report["quota"]["per_snapshot_units"] == 2 * 7


def test_classify_titles_separates_exact_sibling_and_off_topic():
    titles = ["Samsung Z Flip8 review", "Galaxy Z Flip 8 Pro?", "Z Flip 8 5G unboxing", "Fold 8 review"]
    shares = pilot_report.classify_titles("galaxy z flip 8", titles)
    assert shares == {"on_target": 0.5, "sibling": 0.25, "off_topic": 0.25}

    shares = pilot_report.classify_titles("pixel 9", ["Pixel 9 review", "Pixel 9 Pro XL", "Pixel 9a long term"])
    assert round(shares["on_target"], 2) == 0.33 and round(shares["sibling"], 2) == 0.33


def test_main_prints_report(paths, capsys):
    setup_pilot(paths)
    assert pilot_report.main(paths) == 0
    assert "PILOT REPORT" in capsys.readouterr().out
