import csv

import pytest

import clean_youtube as cy
from conftest import read_csv

VIDEOS = [  # legacy schema: no query_version column
    ["snapshot_date", "product_id", "video_id", "title", "channel_id", "published_at",
     "view_count", "like_count", "comment_count"],
    ["2026-10-03", "P001", "a", "Tom &amp; Jerry <b>review</b>", "c1", "2026-09-20T10:00:00+05:30", "100", "", "0"],
    ["2026-10-03", "P001", "a", "Tom &amp; Jerry <b>review</b>", "c1", "2026-09-20T10:00:00+05:30", "100", "", "0"],
    ["2026-10-03", "P001", "a", "later copy", "c1", "2026-09-20T10:00:00+05:30", "999", "5", "1"],
    ["2026/10/03", "p2", "b", "  spaced   title ", "c2", "2026-09-21 08:00:00", "abc", "-5", "7"],
    ["2026-10-03", "X9", "c", "bad id", "c3", "", "1", "1", "1"],
    ["not a date", "P001", "d", "bad date", "c4", "", "1", "1", "1"],
    ["2026-10-03", "P002", "e", "huge outlier", "c5", "garbage", "99999999999", "0", "0"],
]
COMMENTS = [
    ["snapshot_date", "product_id", "query_version", "video_id", "comment_id", "text", "like_count", "published_at"],
    ["2026-10-03", "P001", "v1", "a", "k1", "I &lt;3 this<br>phone​", "3", "2026-09-22T01:02:03Z"],
    ["2026-10-03", "P001", "v1", "a", "k1", "duplicate key, later", "9", "2026-09-22T01:02:03Z"],
    ["2026-10-03", "P001", "v1", "a", "k2", "<br>   ", "0", "2026-09-22T01:02:03Z"],
    ["2026-10-03", "P001", "v1", "a", "k3", "🔥🔥 ❤️", "", "2026-09-22T01:02:03Z"],
    ["2026-10-03", "P001", "v1", "a", "k4", "यह फोन अच्छा है", "1", "2026-09-22T01:02:03Z"],
    ["2026-10-03", "P001", "v2", "a", "k1", "same id, other query version", "1", "2026-09-22T01:02:03Z"],
]
DAILY = [
    ["Snapshot Date", "productID", "video_result_count", "video_views_total", "video_comments_total"],
    ["2026-10-03", "P001", "50", "1000", "10"],
    ["2026-10-03", "P001", "50", "1000", "10"],
]


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


@pytest.fixture
def inputs(paths):
    write(paths.videos_snapshot_csv, VIDEOS)
    write(paths.comments_snapshot_csv, COMMENTS)
    write(paths.product_daily_csv, DAILY)
    return paths


def report_step(paths, table, step):
    return next(r for r in read_csv(paths.processed_dir / cy.REPORT_OUT) if r["table"] == table and r["step"] == step)


def test_videos_cleaned(inputs):
    paths = inputs
    assert cy.main(paths) == 0
    out = read_csv(paths.processed_dir / cy.VIDEOS_OUT)
    by_id = {r["video_id"]: r for r in out}

    assert sorted(by_id) == ["a", "b", "e"]  # c: bad product_id, d: bad snapshot_date
    a, b, e = by_id["a"], by_id["b"], by_id["e"]
    assert a["title"] == "Tom & Jerry review" and a["view_count"] == "100"  # first seen kept
    assert a["published_at"] == "2026-09-20T04:30:00Z"  # +05:30 converted to UTC
    assert a["like_count"] == "" and a["comment_count"] == "0"  # missing stays missing, zero stays zero
    assert a["query_version"] == "v1" and a["is_active_query"] == "True"
    assert (b["product_id"], b["snapshot_date"], b["title"]) == ("P002", "2026-10-03", "spaced title")
    assert b["published_at"] == "2026-09-21T08:00:00Z"
    assert b["view_count"] == "" and b["like_count"] == ""  # non-numeric and negative -> missing
    assert e["view_count"] == "99999999999" and e["published_at"] == ""  # outlier kept

    assert report_step(paths, "videos", "drop_exact_duplicates")["rows_dropped"] == "1"
    assert report_step(paths, "videos", "drop_key_duplicates")["rows_dropped"] == "1"
    assert report_step(paths, "videos", "normalize_product_id")["rows_dropped"] == "1"
    assert report_step(paths, "videos", "standardize_dates")["rows_dropped"] == "1"
    assert report_step(paths, "videos", "output")["rows_after"] == "3"


def test_comments_cleaned_and_flagged(inputs):
    paths = inputs
    cy.main(paths)
    out = {(r["query_version"], r["comment_id"]): r for r in read_csv(paths.processed_dir / cy.COMMENTS_OUT)}

    assert sorted(out) == [("v1", "k1"), ("v1", "k3"), ("v1", "k4"), ("v2", "k1")]  # k2 empty after cleaning
    assert out[("v1", "k1")]["text"] == "I <3 this phone" and out[("v1", "k1")]["like_count"] == "3"
    assert out[("v1", "k3")]["is_emoji_only"] == "True" and out[("v1", "k3")]["like_count"] == ""
    assert out[("v1", "k4")]["likely_non_english"] == "True" and out[("v1", "k4")]["is_emoji_only"] == "False"
    assert out[("v1", "k1")]["likely_non_english"] == "False"
    assert out[("v2", "k1")]["is_active_query"] == "False"  # v2 is not P001's active query version
    assert report_step(paths, "comments", "drop_empty_text")["rows_dropped"] == "1"
    assert report_step(paths, "comments", "drop_key_duplicates")["rows_dropped"] == "1"


def test_product_daily_column_names_and_legacy_columns(inputs):
    paths = inputs
    cy.main(paths)
    out = read_csv(paths.processed_dir / cy.DAILY_OUT)
    assert len(out) == 1
    assert list(out[0])[:3] == ["snapshot_date", "product_id", "video_result_count"]
    assert out[0]["search_total_results_approx"] == "" and out[0]["result_cap_hit"] == ""
    missing = next(r for r in read_csv(paths.processed_dir / cy.REPORT_OUT)
                   if r["table"] == "product_daily" and r["column"] == "result_cap_hit")
    assert (missing["missing_before"], missing["missing_after"]) == ("2", "1")


def test_inputs_untouched_and_idempotent(inputs):
    paths = inputs
    originals = {p: p.read_bytes() for p in (paths.videos_snapshot_csv, paths.comments_snapshot_csv,
                                             paths.product_daily_csv)}
    cy.main(paths)
    outputs = sorted(paths.processed_dir.iterdir()) + [paths.root / "docs" / cy.SUMMARY_DOC]
    first = {p: p.read_bytes() for p in outputs}
    cy.main(paths)
    assert {p: p.read_bytes() for p in outputs} == first
    assert {p: p.read_bytes() for p in originals} == originals
    summary = (paths.root / "docs" / cy.SUMMARY_DOC).read_text()
    assert "first row seen" in summary and "Outliers are never removed" in summary


def test_text_cleaning_is_stable_on_clean_text():
    for text in ["I <3 this phone", "Tom & Jerry review", "❤️‍🔥 ok"]:
        assert cy.clean_text(cy.clean_text(text)) == cy.clean_text(text)
    assert cy.clean_text("❤️‍🔥") == "❤️‍🔥"  # zero-width joiner inside emoji kept


def test_missing_inputs_reported(paths, capsys):
    assert cy.main(paths) == 1
    assert "Missing input" in capsys.readouterr().err


def test_normalize_product_id():
    assert [cy.normalize_product_id(v) for v in ["P001", "p44", " 7 ", "P0044", "X9", "P1234"]] == [
        "P001", "P044", "P007", "P044", None, None]


def test_trends_cleaning_is_a_marked_stub(paths):
    import clean_trends
    with pytest.raises(NotImplementedError):
        clean_trends.clean_trends(paths)
    assert clean_trends.main(paths) == 1
