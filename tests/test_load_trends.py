import shutil
from pathlib import Path

import pytest

import load_trends
from conftest import read_csv

FIXTURE = Path(__file__).parent / "fixtures" / "trends" / "P001_2026-10-03.csv"


def test_parse_export_skips_preamble_and_handles_below_threshold():
    term, granularity, records = load_trends.parse_export(FIXTURE.read_text(encoding="utf-8"))
    assert term == "iphone 18 pro"  # ": (India)" suffix stripped
    assert granularity == "weekly"
    assert records == [
        ("2026-08-30", 12, False),
        ("2026-09-06", 0.5, True),
        ("2026-09-13", 100, False),
        ("2026-09-20", 64, False),
    ]


def test_parse_export_time_header_infers_granularity():
    text = '﻿"Time","pixel 11 (India)"\n"2026-09-01","5"\n"2026-09-02","<1"\n"2026-09-03","7"\n'
    term, granularity, records = load_trends.parse_export(text.lstrip("﻿"))
    assert term == "pixel 11"
    assert granularity == "daily"
    assert records[1] == ("2026-09-02", 0.5, True)


def test_parse_export_monthly_dates_normalized():
    _, granularity, records = load_trends.parse_export("Month,pixel 11: (India)\n2026-08,40\n2026-09,55\n")
    assert granularity == "monthly"
    assert records[0][0] == "2026-08-01"


@pytest.mark.parametrize("text", [
    "Category: All categories\n\n2026-08-30,12\n",  # no header row
    "Week,a: (India),b: (India)\n2026-08-30,1,2\n",  # comparison export with two terms
    "Week,pixel 11: (India)\n2026-08-30,abc\n",  # non-numeric value
])
def test_parse_export_rejects_malformed(text):
    with pytest.raises(load_trends.TrendsFileError):
        load_trends.parse_export(text)


@pytest.mark.parametrize("name", ["iphone.csv", "P1_2026-10-03.csv", "P001_2026-13-40.csv", "P001_2026-10-03.txt"])
def test_parse_filename_rejects_bad_names(name):
    with pytest.raises(load_trends.TrendsFileError):
        load_trends.parse_filename(name)


def test_main_writes_long_table_and_logs(paths):
    paths.raw_trends_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, paths.raw_trends_dir / FIXTURE.name)

    assert load_trends.main(paths) == 0

    rows = read_csv(paths.trends_long_csv)
    assert len(rows) == 4
    assert rows[1] == {
        "product_id": "P001", "date": "2026-09-06", "search_interest": "0.5", "granularity": "weekly",
        "is_below_threshold": "true", "source_file": "P001_2026-10-03.csv", "collection_date": "2026-10-03",
    }
    log = read_csv(paths.collection_log_csv)
    assert [(r["source"], r["product_id"], r["records_collected"], r["status"]) for r in log] == [
        ("google_trends", "P001", "4", "success")]
    # the original export is untouched
    assert (paths.raw_trends_dir / FIXTURE.name).read_bytes() == FIXTURE.read_bytes()


def test_main_fails_loudly_on_bad_file_name(paths):
    paths.raw_trends_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, paths.raw_trends_dir / "iphone 18 pro.csv")

    assert load_trends.main(paths) == 1
    assert not paths.trends_long_csv.exists()
    log = read_csv(paths.collection_log_csv)
    assert log[0]["status"] == "failed" and "bad file name" in log[0]["error_message"]


def test_main_rejects_export_for_wrong_search_term(paths):
    paths.raw_trends_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, paths.raw_trends_dir / "P002_2026-10-03.csv")  # P002 is pixel 11

    assert load_trends.main(paths) == 1
    log = read_csv(paths.collection_log_csv)
    assert log[0]["product_id"] == "P002" and log[0]["status"] == "failed"
