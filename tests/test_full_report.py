import csv

import full_report as fr
from conftest import PRODUCTS, read_csv

ALIGNED_HEADER = ["product_id", "date", "product_name", "brand", "category", "product_type", "trends_observed",
                  "search_interest", "is_below_threshold", "is_zero", "is_provisional", "zero_share_product",
                  "low_signal_product"]


def write(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def aligned_rows(pid, values, low_signal):
    zeros = sum(v == 0 for v in values)
    return [[pid, f"2026-09-{i + 1:02d}", "n", "b", "c", "RISING", "True", v, "False", str(v == 0),
             str(i == len(values) - 1), round(zeros / len(values), 4), str(low_signal)]
            for i, v in enumerate(values)]


def setup(paths, with_youtube=True):
    with open(paths.pilot_products_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(PRODUCTS[0]))
        w.writeheader()
        w.writerow(PRODUCTS[0])
    p1 = list(range(10, 100, 10))  # 9 days, peak 90 on the 9th (provisional) day
    p2 = [0, 0, 0, 0, 0, 0, 0, 5, 100]
    write(paths.processed_dir / "aligned_daily.csv", ALIGNED_HEADER,
          aligned_rows("P001", p1, False) + aligned_rows("P002", p2, True))
    if not with_youtube:
        return
    write(paths.processed_dir / "youtube_product_daily_clean.csv",
          ["snapshot_date", "product_id", "query_version", "video_result_count", "video_views_total",
           "video_comments_total", "search_total_results_approx", "result_cap_hit", "videos_on_target",
           "views_on_target_total", "comments_on_target_total", "on_target_share", "low_on_target_share"],
          [["2026-10-03", "P001", "v1", 50, 100, 10, 1000, True, 20, 40, 4, 0.4, True],
           ["2026-10-04", "P001", "v1", 50, 200, 20, 1000, True, 40, 160, 16, 0.8, False],
           ["2026-10-04", "P002", "v1", 50, 50, 5, 1000, True, 25, 30, 3, 0.5, True]])
    write(paths.processed_dir / "youtube_videos_clean.csv", ["snapshot_date", "product_id", "video_id"],
          [["2026-10-03", "P001", "a"], ["2026-10-04", "P001", "a"], ["2026-10-04", "P001", "b"],
           ["2026-10-04", "P002", "c"]])
    write(paths.processed_dir / "youtube_comments_clean.csv",
          ["snapshot_date", "product_id", "comment_id", "is_emoji_only", "likely_non_english"],
          [["2026-10-04", "P001", "k1", True, False], ["2026-10-04", "P001", "k2", False, False],
           ["2026-10-03", "P001", "old", False, True]])


def test_full_report_per_product(paths):
    setup(paths)
    assert fr.main(paths) == 0
    rows = {r["product_id"]: r for r in read_csv(paths.processed_dir / fr.REPORT_CSV)}

    assert list(rows) == ["P001", "P002"]  # every registry product, even without data
    p1, p2 = rows["P001"], rows["P002"]
    assert list(p1) == fr.COLUMNS
    assert (p1["trends_rows"], p1["zero_share"], p1["peak_date"], p1["is_pilot"]) == ("9", "0.0", "2026-09-09", "True")
    assert p1["last_7d_mean"] == "50.0"  # days 2-8 (10..90 step 10 -> 20..80), provisional day 9 excluded
    assert (p1["youtube_snapshots"], p1["youtube_latest_snapshot"], p1["on_target_share"]) == ("2", "2026-10-04", "0.8")
    assert (p1["youtube_videos"], p1["youtube_comments"], p1["comments_emoji_only_share"]) == ("2", "2", "0.5")
    assert p1["usable"] == "True" and p1["issues"] == ""
    assert p2["low_signal_product"] == "True" and p2["usable"] == "False"
    assert "low signal (zero share 78%)" in p2["issues"] and "low on-target share (50%)" in p2["issues"]
    assert p2["youtube_comments"] == "" and p2["videos_published_7d"] == ""  # missing, not 0

    md = (paths.root / "docs" / fr.REPORT_MD).read_text()
    assert "**Usable** (Trends not low-signal" in md and "P002 (78%, RISING)" in md


def test_full_report_without_youtube_or_trends(paths):
    setup(paths, with_youtube=False)
    (paths.processed_dir / "aligned_daily.csv").unlink()
    assert fr.main(paths) == 0
    rows = read_csv(paths.processed_dir / fr.REPORT_CSV)
    assert [r["issues"] for r in rows] == ["no Trends data, no YouTube data"] * 2
    md = (paths.root / "docs" / fr.REPORT_MD).read_text()
    assert "aligned_daily.csv" in md and "youtube_product_daily_clean.csv" in md


def test_full_report_is_idempotent(paths):
    setup(paths)
    fr.main(paths)
    first = (paths.processed_dir / fr.REPORT_CSV).read_bytes(), (paths.root / "docs" / fr.REPORT_MD).read_bytes()
    fr.main(paths)
    assert ((paths.processed_dir / fr.REPORT_CSV).read_bytes(), (paths.root / "docs" / fr.REPORT_MD).read_bytes()) == first
