"""Write data/trends_export_checklist.csv: one row per product to export from Google Trends by hand.

Pilot products come first. expected_filename uses today's date (India time). If the checklist
already exists, the `done` and `notes` values typed into it are kept. Also prints the custom
date range for this batch and how many products still have no export in raw/trends/.

Usage: python src/make_trends_checklist.py
"""

import csv
import sys
from datetime import date, timedelta

import config
from common import read_products, today_in

HEADER = ["product_id", "product_name", "trends_query", "expected_filename", "is_pilot", "done", "notes"]


def batch_range(today):
    """Custom range of config.TRENDS_WINDOW_DAYS days ending today (both ends inclusive)."""
    end = date.fromisoformat(today)
    return end - timedelta(days=config.TRENDS_WINDOW_DAYS - 1), end


def existing_exports(paths):
    """{product_id: [file names]} for exports already in raw/trends/."""
    found = {}
    if paths.raw_trends_dir.exists():
        for path in sorted(paths.raw_trends_dir.glob("P*_*.csv")):
            found.setdefault(path.name.split("_")[0], []).append(path.name)
    return found


def build_checklist(products, pilot_ids, today, previous=None):
    previous = previous or {}
    rows = []
    for p in products:
        old = previous.get(p["product_id"], {})
        rows.append({
            "product_id": p["product_id"],
            "product_name": p["product_name"],
            "trends_query": p["trends_query"],
            "expected_filename": f"raw/trends/{p['product_id']}_{today}.csv",
            "is_pilot": "true" if p["product_id"] in pilot_ids else "false",
            "done": old.get("done", ""),
            "notes": old.get("notes", ""),
        })
    return sorted(rows, key=lambda r: (r["is_pilot"] != "true", r["product_id"]))


def main(paths=config.PATHS, today=None):
    today = today or today_in(config.SNAPSHOT_TIMEZONE)
    products = read_products(paths.products_csv)
    pilot_ids = (
        {p["product_id"] for p in read_products(paths.pilot_products_csv)}
        if paths.pilot_products_csv.exists() else set()
    )
    previous = (
        {r["product_id"]: r for r in read_products(paths.trends_checklist_csv)}
        if paths.trends_checklist_csv.exists() else {}
    )
    rows = build_checklist(products, pilot_ids, today, previous)
    with open(paths.trends_checklist_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    start, end = batch_range(today)
    exports = existing_exports(paths)
    missing = [r for r in rows if r["product_id"] not in exports]
    missing_pilot = [r for r in missing if r["is_pilot"] == "true"]
    print(f"Wrote {len(rows)} rows to {paths.trends_checklist_csv} ({len(pilot_ids)} pilot products first).")
    print(f"\nBatch settings: {config.TRENDS_GEO}, custom time range {start} to {end} "
          f"({config.TRENDS_WINDOW_DAYS} days, daily), All categories, Web Search.")
    print(f"\nExports still missing in {paths.raw_trends_dir}: {len(missing)} of {len(rows)} "
          f"(pilot: {len(missing_pilot)} of {len(pilot_ids)}).")
    for r in missing_pilot:
        print(f"  pilot  {r['product_id']}  {r['trends_query']!r:<28} -> {r['expected_filename']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
