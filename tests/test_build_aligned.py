import csv

import build_aligned as ba
from conftest import read_csv

TRENDS = [
    ["product_id", "date", "search_interest", "is_below_threshold", "is_zero", "granularity", "source_file",
     "collection_date"],
    ["P001", "2026-10-01", "40.0", "False", "False", "daily", "f", "2026-10-03"],
    ["P001", "2026-10-02", "", "False", "", "daily", "f", "2026-10-03"],  # missing Trends value
    ["P001", "2026-10-03", "0.5", "True", "False", "daily", "f", "2026-10-03"],
    ["P001", "2026-10-04", "60.0", "False", "False", "daily", "f", "2026-10-03"],
    ["P003", "2026-10-03", "10.0", "False", "False", "daily", "f", "2026-10-03"],  # not in registry
]
DAILY = [
    ["snapshot_date", "product_id", "query_version", "video_result_count", "video_views_total",
     "video_comments_total", "search_total_results_approx", "result_cap_hit", "videos_on_target",
     "views_on_target_total", "comments_on_target_total", "on_target_share", "low_on_target_share"],
    ["2026-10-02", "P001", "v1", "50", "1000", "10", "1000000", "True", "40", "900", "9", "0.8", "False"],
    ["2026-10-09", "P001", "v1", "50", "2000", "20", "1000000", "True", "40", "1800", "18", "0.8", "False"],
    ["2026-10-02", "P002", "v1", "50", "5", "1", "10", "False", "1", "5", "1", "0.02", "True"],  # no Trends
]
RECENT = [
    ["snapshot_date", "product_id", "query_version", "window_days", "published_after", "videos_published_7d",
     "videos_7d_on_target", "cap_hit", "search_total_results_approx"],
    ["2026-10-03", "P001", "v1", "7", "2026-09-26T00:00:00Z", "50", "20", "True", "300"],
]


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def setup(paths, daily=True):
    write(paths.processed_dir / ba.TRENDS_IN, TRENDS)
    if daily:
        write(paths.processed_dir / ba.DAILY_IN, DAILY)
        write(paths.processed_dir / ba.RECENT_IN, RECENT)


def test_left_join_on_trends_index_without_forward_fill(paths):
    setup(paths)
    assert ba.main(paths) == 0
    rows = read_csv(paths.processed_dir / ba.ALIGNED_OUT)
    by = {(r["product_id"], r["date"]): r for r in rows}

    assert len(rows) == 5  # exactly the Trends rows
    assert by[("P001", "2026-10-01")]["product_name"] == "Apple iPhone 18 Pro"
    assert by[("P001", "2026-10-01")]["product_type"] == "RISING"
    assert by[("P001", "2026-10-02")]["youtube_observed"] == "True"
    assert by[("P001", "2026-10-02")]["views_on_target_total"] == "900"
    assert by[("P001", "2026-10-02")]["video_views_total"] == "1000"  # all-video total kept too
    assert by[("P001", "2026-10-02")]["trends_observed"] == "False"
    for day in ("2026-10-03", "2026-10-04"):  # not forward-filled
        assert by[("P001", day)]["youtube_observed"] == "False"
        assert by[("P001", day)]["video_views_total"] == ""
    assert by[("P001", "2026-10-03")]["youtube_recent_observed"] == "True"
    assert by[("P001", "2026-10-03")]["videos_7d_on_target"] == "20"
    assert by[("P001", "2026-10-04")]["youtube_recent_observed"] == "False"
    assert by[("P003", "2026-10-03")]["product_name"] == ""  # not in the test registry
    assert "growth" not in rows[0] and "surge_label" not in rows[0]


def test_alignment_report(paths):
    setup(paths)
    ba.main(paths)
    report = {r["product_id"]: r for r in read_csv(paths.processed_dir / ba.REPORT_OUT)}

    p1 = report["P001"]
    assert (p1["trends_days"], p1["trends_missing_values"]) == ("4", "1")
    assert (p1["youtube_snapshots"], p1["youtube_matched"], p1["youtube_unmatched"]) == ("2", "1", "1")
    assert p1["youtube_coverage"] == "0.2500" and p1["recent_matched"] == "1"
    p2 = report["P002"]
    assert (p2["trends_days"], p2["youtube_unmatched"], p2["youtube_coverage"]) == ("0", "1", "")
    assert report["P003"]["in_registry"] == "False"
    total = report["ALL"]
    assert (total["trends_days"], total["youtube_matched"], total["youtube_unmatched"]) == ("5", "1", "2")


def test_without_youtube_data(paths, capsys):
    setup(paths, daily=False)
    assert ba.main(paths) == 0
    rows = read_csv(paths.processed_dir / ba.ALIGNED_OUT)
    assert len(rows) == 5 and all(r["youtube_observed"] == "False" for r in rows)
    assert "YouTube columns will be empty" in capsys.readouterr().out


def test_exits_cleanly_without_trends(paths, capsys):
    assert ba.main(paths) == 0
    assert "not available yet" in capsys.readouterr().out
    assert not (paths.processed_dir / ba.ALIGNED_OUT).exists()


def test_end_to_end_from_trends_export(paths):
    """raw/trends export -> load_trends -> clean_trends -> build_aligned."""
    import shutil
    from pathlib import Path

    import clean_trends
    import load_trends

    fixture = Path(__file__).parent / "fixtures" / "trends" / "P001_2026-10-03.csv"
    paths.raw_trends_dir.mkdir(parents=True)
    shutil.copy(fixture, paths.raw_trends_dir / fixture.name)
    write(paths.processed_dir / ba.DAILY_IN, [DAILY[0], ["2026-10-02"] + DAILY[1][1:]])

    assert load_trends.main([], paths=paths) == 0
    assert clean_trends.main(paths) == 0
    assert ba.main(paths) == 0

    rows = read_csv(paths.processed_dir / ba.ALIGNED_OUT)
    assert [r["date"] for r in rows] == ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    assert [r["youtube_observed"] for r in rows] == ["False"] * 4 + ["True"]
    assert rows[1]["search_interest"] == "0.5" and rows[1]["is_below_threshold"] == "True"
