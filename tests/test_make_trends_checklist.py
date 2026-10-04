import csv

import make_trends_checklist as mtc
from conftest import PRODUCTS, read_csv


def write_pilot(paths, ids):
    with open(paths.pilot_products_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(PRODUCTS[0]))
        writer.writeheader()
        writer.writerows(p for p in PRODUCTS if p["product_id"] in ids)


def test_checklist_pilot_first_and_filenames(paths, capsys):
    write_pilot(paths, {"P002"})

    assert mtc.main(paths, today="2026-10-03") == 0

    rows = read_csv(paths.trends_checklist_csv)
    assert list(rows[0]) == mtc.HEADER
    assert [(r["product_id"], r["is_pilot"]) for r in rows] == [("P002", "true"), ("P001", "false")]
    assert rows[0]["expected_filename"] == "raw/trends/P002_2026-10-03.csv"
    assert rows[0]["done"] == "" and rows[0]["notes"] == ""
    out = capsys.readouterr().out
    assert "2026-01-08 to 2026-10-03 (269 days, daily)" in out
    assert "still missing" in out and "2 of 2 (pilot: 1 of 1)" in out


def test_checklist_keeps_human_edits_and_counts_existing_exports(paths, capsys):
    write_pilot(paths, {"P001"})
    mtc.main(paths, today="2026-10-03")
    rows = read_csv(paths.trends_checklist_csv)
    rows[0].update(done="yes", notes="downloaded by A")
    with open(paths.trends_checklist_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=mtc.HEADER)
        writer.writeheader()
        writer.writerows(rows)
    paths.raw_trends_dir.mkdir(parents=True)
    (paths.raw_trends_dir / "P001_2026-10-03.csv").write_text("x")

    mtc.main(paths, today="2026-10-04")

    rows = read_csv(paths.trends_checklist_csv)
    assert (rows[0]["product_id"], rows[0]["done"], rows[0]["notes"]) == ("P001", "yes", "downloaded by A")
    assert rows[0]["expected_filename"] == "raw/trends/P001_2026-10-04.csv"
    assert "1 of 2 (pilot: 0 of 1)" in capsys.readouterr().out
