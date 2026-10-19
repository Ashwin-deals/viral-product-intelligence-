import csv

import build_master as bm
from conftest import read_csv

ALIGNED_HEADER = [
    "product_id", "date", "product_name", "brand", "category", "product_type", "trends_observed", "search_interest",
    "is_below_threshold", "is_zero", "is_provisional", "zero_share_product", "low_signal_product", "youtube_observed",
    "youtube_query_version", "video_result_count", "video_views_total", "video_comments_total", "videos_on_target",
    "views_on_target_total", "comments_on_target_total", "on_target_share", "low_on_target_share", "result_cap_hit",
    "youtube_recent_observed", "videos_published_7d", "videos_7d_on_target", "recent_cap_hit"]
SNAPSHOT = ["v1", "50", "1000", "10", "40", "900", "9", "0.8", "False", "True", "True", "5", "4", "False"]
NO_SNAPSHOT = ["", "", "", "", "", "", "", "", "", "", "False", "", "", ""]
SPINE = [("P001", "Apple iPhone 18 Pro", "Apple", "RISING", ["10.0", "20.0", "50.0"]),
         ("P002", "Google Pixel 11", "Google", "STABLE", ["30.0", "30.0", "0.0"])]
DATES = ["2026-10-01", "2026-10-02", "2026-10-03"]
VIDEOS = [
    ["snapshot_date", "product_id", "query_version", "video_id", "title", "channel_id", "published_at",
     "view_count", "like_count", "comment_count"],
    # 20:00 UTC on 10-01 is 01:30 on 10-02 in India
    ["2026-10-03", "P001", "v1", "vA", "iPhone 18 Pro review", "c", "2026-10-01T20:00:00Z", "1", "1", "1"],
    ["2026-10-04", "P001", "v1", "vA", "iPhone 18 Pro review", "c", "2026-10-01T20:00:00Z", "2", "1", "1"],  # same video, 2nd snapshot
    ["2026-10-03", "P001", "v1", "vB", "iPhone 18 Pro Max review", "c", "2026-10-02T05:00:00Z", "1", "1", "1"],  # sibling
    ["2026-10-03", "P002", "v1", "vC", "Pixel 11 review", "c", "2026-09-01T05:00:00Z", "1", "1", "1"],  # before window
]
COMMENTS = [
    ["snapshot_date", "product_id", "query_version", "video_id", "comment_id", "text", "like_count", "published_at",
     "is_emoji_only", "likely_non_english"],
    ["2026-10-03", "P001", "v1", "vA", "k1", "great phone", "0", "2026-10-02T19:00:00Z", "False", "False"],
    ["2026-10-04", "P001", "v1", "vA", "k1", "great phone", "0", "2026-10-02T19:00:00Z", "False", "False"],  # repeat
    ["2026-10-03", "P001", "v1", "vA", "k2", "🔥🔥", "0", "2026-10-03T03:00:00Z", "True", "False"],
    ["2026-10-03", "P002", "v1", "vC", "k3", "बहुत अच्छा", "0", "2026-10-01T10:00:00Z", "False", "True"],
]
FULL_REPORT = [
    ["product_id", "youtube_latest_snapshot", "videos_on_target", "views_on_target_total", "on_target_share",
     "low_on_target_share", "videos_7d_on_target", "usable"],
    ["P001", "2026-10-04", "40", "900", "0.8", "False", "4", "True"],
    ["P002", "2026-10-04", "10", "100", "0.5", "True", "1", "False"],
]


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def setup(paths, duplicate_row=False):
    aligned = [ALIGNED_HEADER]
    for pid, name, brand, ptype, values in SPINE:
        for date, value in zip(DATES, values):
            youtube = ["True"] + SNAPSHOT if date == "2026-10-03" else ["False"] + NO_SNAPSHOT
            aligned.append([pid, date, name, brand, "Smartphones", ptype, "True", value, "False",
                            str(value == "0.0"), str(date == "2026-10-03"), "0.0", "False"] + youtube)
    if duplicate_row:
        aligned.append(aligned[1])
    write(paths.processed_dir / bm.ALIGNED_IN, aligned)
    write(paths.processed_dir / bm.VIDEOS_IN, VIDEOS)
    write(paths.processed_dir / bm.COMMENTS_IN, COMMENTS)
    write(paths.processed_dir / bm.FULL_REPORT_IN, FULL_REPORT)


def test_builds_daily_activity_target_and_passes_validation(paths):
    setup(paths)
    assert bm.main(paths) == 0
    rows = read_csv(paths.root / "processed" / bm.MASTER_OUT)
    by = {(r["product_id"], r["date"]): r for r in rows}

    assert len(rows) == 6
    assert by[("P001", "2026-10-01")]["launch_period"] == "2026-09"
    assert by[("P001", "2026-10-01")]["videos_published"] == "0"  # 20:00 UTC counts on the India day
    assert by[("P001", "2026-10-02")]["videos_published"] == "2"  # vA once (2 snapshots), vB once
    assert by[("P001", "2026-10-02")]["videos_published_on_target"] == "1"  # vB names the Pro Max
    assert by[("P001", "2026-10-03")]["comments_published"] == "2"  # k1 once + k2
    assert by[("P001", "2026-10-03")]["comments_published_usable"] == "1"  # k2 is emoji-only
    assert (by[("P002", "2026-10-01")]["comments_published"], by[("P002", "2026-10-01")]["comments_published_usable"]) \
        == ("1", "0")  # flagged non-English
    assert by[("P001", "2026-10-02")]["search_interest_growth"] == "1.0"  # 10 -> 20, same rule as Unit 2
    assert {r["surge_label"] for r in rows} <= {"0", "1"}
    assert by[("P001", "2026-10-03")]["video_views_total"] == "1000.0"  # snapshot kept, not forward-filled
    assert by[("P001", "2026-10-02")]["video_views_total"] == ""
    # The latest snapshot (taken after the window) is on every row of the product
    assert {by[("P002", d)]["yt_snapshot_date"] for d in DATES} == {"2026-10-04"}
    assert by[("P002", "2026-10-01")]["yt_low_on_target_share"] == "True"

    report = {r["check"]: r for r in read_csv(paths.root / "processed" / bm.REPORT_OUT)}
    assert "FAIL" not in {r["status"] for r in report.values()}
    detail = report["videos reconcile with source"]["detail"]
    assert "4 rows in the cleaned table = 3 unique videos + 1 seen again" in detail  # vA in 2 snapshots
    assert "1 earlier" in detail  # vC is before the window
    assert len(read_csv(paths.root / "processed" / bm.SAMPLE_OUT)) == 2  # 2 per product_type, snapshot day only


def test_duplicate_key_fails_and_writes_no_master(paths):
    setup(paths, duplicate_row=True)
    assert bm.main(paths) == 1
    assert not (paths.root / "processed" / bm.MASTER_OUT).exists()
    report = {r["check"]: r["status"] for r in read_csv(paths.root / "processed" / bm.REPORT_OUT)}
    assert report["no duplicate (product_id, date)"] == "FAIL"
    assert report["rows = products x days"] == "FAIL"


def test_missing_input_exits_1(paths):
    assert bm.main(paths) == 1
