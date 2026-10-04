import csv

import clean_trends as ct
from conftest import read_csv

HEADER = ["product_id", "date", "search_interest", "granularity", "is_below_threshold", "source_file",
          "collection_date"]
ROWS = [
    # older export of P001: superseded by the 2026-10-03 one
    ["P001", "2026-09-01", "70", "daily", "false", "P001_2026-09-05.csv", "2026-09-05"],
    ["P001", "2026-09-01", "40", "daily", "false", "P001_2026-10-03.csv", "2026-10-03"],
    ["P001", "2026-09-01", "40", "daily", "false", "P001_2026-10-03.csv", "2026-10-03"],  # exact dup
    ["P001", "2026-09-02", "0", "daily", "false", "P001_2026-10-03.csv", "2026-10-03"],  # real zero
    ["P001", "2026-09-02", "11", "daily", "false", "P001_2026-10-03.csv", "2026-10-03"],  # key dup
    ["P001", "2026-09-05", "0.5", "daily", "true", "P001_2026-10-03.csv", "2026-10-03"],  # "<1"; gap 03..04
    ["P001", "2026-09-06", "100", "daily", "false", "P001_2026-10-03.csv", "2026-10-03"],  # outlier peak kept
    ["P001", "2026-09-07", "", "daily", "false", "P001_2026-10-03.csv", "2026-10-03"],  # missing
    ["p2", "2026/09/01", "250", "daily", "false", "P002_2026-10-03.csv", "2026-10-03"],  # out of range
    ["P002", "2026-09-02", "3", "daily", "TRUE", "P002_2026-10-03.csv", "2026-10-03"],  # below, wrong value
    ["X9", "2026-09-01", "5", "daily", "false", "x.csv", "2026-10-03"],  # bad id
    ["P002", "not a date", "5", "daily", "false", "P002_2026-10-03.csv", "2026-10-03"],  # bad date
]


def setup(paths, rows=ROWS, export=True):
    if export:
        paths.raw_trends_dir.mkdir(parents=True)
        (paths.raw_trends_dir / "P001_2026-10-03.csv").write_text("Day,x\n")
    with open(paths.trends_long_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)


def step(paths, name, column=""):
    return next(r for r in read_csv(paths.processed_dir / ct.REPORT_OUT) if r["step"] == name and r["column"] == column)


def test_clean_trends(paths):
    setup(paths)
    assert ct.main(paths) == 0
    out = {(r["product_id"], r["date"]): r for r in read_csv(paths.processed_dir / ct.OUT)}

    assert sorted(out) == [("P001", "2026-09-01"), ("P001", "2026-09-02"), ("P001", "2026-09-05"),
                           ("P001", "2026-09-06"), ("P001", "2026-09-07"), ("P002", "2026-09-01"),
                           ("P002", "2026-09-02")]
    assert out[("P001", "2026-09-01")]["search_interest"] == "40.0"  # latest export only
    assert out[("P001", "2026-09-02")]["search_interest"] == "0.0"  # first seen kept; zero is a value
    assert out[("P001", "2026-09-02")]["is_zero"] == "True"
    assert (out[("P001", "2026-09-05")]["search_interest"], out[("P001", "2026-09-05")]["is_below_threshold"]) == \
        ("0.5", "True")
    assert out[("P001", "2026-09-06")]["search_interest"] == "100.0"
    assert out[("P001", "2026-09-07")]["search_interest"] == "" and out[("P001", "2026-09-07")]["is_zero"] == ""
    assert out[("P002", "2026-09-01")]["search_interest"] == ""  # 250 out of range -> missing
    assert out[("P002", "2026-09-02")]["search_interest"] == "0.5"  # below threshold enforced

    assert step(paths, "select_latest_export")["rows_dropped"] == "1"
    assert "P001 2026-09-05: 1" in step(paths, "select_latest_export")["detail"]
    assert step(paths, "drop_exact_duplicates")["rows_dropped"] == "1"
    assert step(paths, "drop_key_duplicates")["rows_dropped"] == "1"
    assert step(paths, "normalize_product_id")["rows_dropped"] == "1"
    assert step(paths, "standardize_dates")["rows_dropped"] == "1"
    assert "1 corrected" in step(paths, "types_zero_vs_missing")["detail"]
    p1 = step(paths, "product_summary", "P001")["detail"]
    assert "days=5" in p1 and "zero=1" in p1 and "below_threshold=1" in p1 and "missing=1" in p1
    assert "gap_days=2 (2026-09-03..2026-09-04)" in p1


def test_idempotent_and_input_untouched(paths):
    setup(paths)
    original = paths.trends_long_csv.read_bytes()
    ct.main(paths)
    first = {p.name: p.read_bytes() for p in paths.processed_dir.iterdir()}
    ct.main(paths)
    assert {p.name: p.read_bytes() for p in paths.processed_dir.iterdir()} == first
    assert paths.trends_long_csv.read_bytes() == original


def test_no_exports_exits_cleanly_and_writes_nothing(paths, capsys):
    paths.raw_trends_dir.mkdir(parents=True)
    (paths.raw_trends_dir / ".gitkeep").touch()
    assert ct.main(paths) == 0
    assert "No Google Trends exports" in capsys.readouterr().out
    assert not paths.processed_dir.exists()


def test_exports_without_loader_output(paths, capsys):
    paths.raw_trends_dir.mkdir(parents=True)
    (paths.raw_trends_dir / "P001_2026-10-03.csv").write_text("Day,x\n")
    assert ct.main(paths) == 1
    assert "load_trends.py first" in capsys.readouterr().err
