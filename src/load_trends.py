"""Load manually exported Google Trends CSVs into data/trends_long.csv.

Exports live in raw/trends/<product_id>_<YYYY-MM-DD>.csv (YYYY-MM-DD = the day the file was
downloaded). They are originals and are only read, never modified. data/trends_long.csv is
rebuilt from all of them on every run. See docs/trends_export_guide.md for the export steps.

"<1" values: Google Trends reports "<1" when interest is above zero but below 1 on its 0-100
scale. We store search_interest = 0.5 (midpoint of the open interval 0..1) and set
is_below_threshold = true so analyses can treat these points separately.

Checks (any failure stops the run, logs it and writes nothing):
- every file must have daily granularity (config.TRENDS_EXPECTED_GRANULARITY); pass
  --allow-weekly to also accept weekly files; monthly and hourly files always fail;
- the latest export of every product must cover the same date range, so products are comparable.

Usage: python src/load_trends.py [--allow-weekly]
"""

import argparse
import csv
import io
import re
import statistics
import sys
from datetime import date

import config
from common import log_collection, read_products

SOURCE = "google_trends"
BELOW_THRESHOLD_VALUE = 0.5
FILENAME_PATTERN = re.compile(r"^(P\d{3})_(\d{4}-\d{2}-\d{2})\.csv$")
HEADER_GRANULARITY = {"day": "daily", "week": "weekly", "month": "monthly", "time": None}
# "iphone 17: (India)" (classic export) or "iphone 17 (India)" -> "iphone 17"
LOCATION_SUFFIX = re.compile(r"\s*:?\s*\(India\)\s*$", re.IGNORECASE)
OUTPUT_HEADER = [
    "product_id", "date", "search_interest", "granularity",
    "is_below_threshold", "source_file", "collection_date",
]


class TrendsFileError(Exception):
    pass


def parse_filename(name):
    """Return (product_id, collection_date) from '<product_id>_<YYYY-MM-DD>.csv'."""
    match = FILENAME_PATTERN.match(name)
    if not match:
        raise TrendsFileError(
            f"bad file name {name!r}: expected <product_id>_<YYYY-MM-DD>.csv, e.g. P001_2026-10-03.csv")
    product_id, collection_date = match.groups()
    try:
        date.fromisoformat(collection_date)
    except ValueError:
        raise TrendsFileError(f"bad file name {name!r}: {collection_date} is not a valid date") from None
    return product_id, collection_date


def parse_value(raw):
    """Return (search_interest, is_below_threshold) for one cell."""
    value = raw.strip()
    if value == "<1":
        return BELOW_THRESHOLD_VALUE, True
    try:
        number = int(value)
    except ValueError:
        raise TrendsFileError(f"unexpected search interest value {raw!r}") from None
    if not 0 <= number <= 100:
        raise TrendsFileError(f"search interest {number} outside 0-100")
    return number, False


def normalize_date(raw):
    value = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}", value):  # monthly exports give YYYY-MM
        value += "-01"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2})?)?", value):
        raise TrendsFileError(f"unexpected date {raw!r}")
    return value


def infer_granularity(dates):
    """For exports whose header says 'Time', infer granularity from the date spacing."""
    if any("T" in d for d in dates):
        return "hourly"
    if len(dates) < 2:
        raise TrendsFileError("cannot infer granularity from fewer than two rows")
    days = [date.fromisoformat(d) for d in dates]
    gap = statistics.median((b - a).days for a, b in zip(days, days[1:]))
    if gap == 1:
        return "daily"
    if gap == 7:
        return "weekly"
    if 28 <= gap <= 31:
        return "monthly"
    raise TrendsFileError(f"cannot infer granularity from a {gap}-day spacing")


def parse_export(text):
    """Parse a Google Trends export. Returns (search_term, granularity, [(date, interest, below)])."""
    rows = list(csv.reader(io.StringIO(text)))
    header_index = next(
        (i for i, row in enumerate(rows) if row and row[0].strip().lower() in HEADER_GRANULARITY), None)
    if header_index is None:
        raise TrendsFileError("no header row starting with Day, Week, Month or Time")
    header = [cell.strip() for cell in rows[header_index]]
    if len(header) != 2:
        raise TrendsFileError(f"expected one search term column, found {len(header) - 1}: {header[1:]}")
    search_term = LOCATION_SUFFIX.sub("", header[1]).strip()

    records = []
    for row in rows[header_index + 1:]:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != 2:
            raise TrendsFileError(f"expected 2 columns, found {len(row)}: {row}")
        interest, below = parse_value(row[1])
        records.append((normalize_date(row[0]), interest, below))
    if not records:
        raise TrendsFileError("no data rows after the header")

    granularity = HEADER_GRANULARITY[header[0].lower()] or infer_granularity([r[0] for r in records])
    return search_term, granularity, records


def load_file(path, products_by_id):
    product_id, collection_date = parse_filename(path.name)
    product = products_by_id.get(product_id)
    if product is None:
        raise TrendsFileError(f"{product_id} is not in products.csv")
    search_term, granularity, records = parse_export(path.read_text(encoding="utf-8-sig"))
    if search_term.casefold() != product["trends_query"].casefold():
        raise TrendsFileError(
            f"export is for {search_term!r} but {product_id} trends_query is {product['trends_query']!r}")
    return [
        {
            "product_id": product_id,
            "date": day,
            "search_interest": interest,
            "granularity": granularity,
            "is_below_threshold": "true" if below else "false",
            "source_file": path.name,
            "collection_date": collection_date,
        }
        for day, interest, below in records
    ]


def check_granularity(rows, allow_weekly):
    granularity = rows[0]["granularity"]
    allowed = {config.TRENDS_EXPECTED_GRANULARITY} | ({"weekly"} if allow_weekly else set())
    if granularity not in allowed:
        hint = "" if allow_weekly or granularity != "weekly" else " (use --allow-weekly to accept weekly data)"
        raise TrendsFileError(
            f"{granularity} data, expected {' or '.join(sorted(allowed))}{hint}. Re-export with a custom "
            f"range of {config.TRENDS_WINDOW_DAYS} days or less (see docs/trends_export_guide.md)")


def check_date_ranges(loaded):
    """All products' latest exports must cover the same date range. Returns {file name: error}."""
    latest = {}
    for name, rows in loaded.items():
        pid, collection_date = rows[0]["product_id"], rows[0]["collection_date"]
        if pid not in latest or collection_date > latest[pid][0]:
            latest[pid] = (collection_date, name)
    ranges = {}
    for _, name in latest.values():
        dates = [r["date"] for r in loaded[name]]
        ranges.setdefault((min(dates), max(dates)), []).append(name)
    if len(ranges) <= 1:
        return {}
    summary = "; ".join(f"{a} to {b}: {len(names)} file(s)" for (a, b), names in sorted(ranges.items()))
    majority = max(ranges, key=lambda r: len(ranges[r]))
    return {
        name: f"date range {a} to {b} differs from {majority[0]} to {majority[1]} (ranges found: {summary})"
        for (a, b), names in ranges.items() if (a, b) != majority
        for name in names
    }


def main(argv=None, paths=config.PATHS):
    parser = argparse.ArgumentParser(description="Load Google Trends exports into data/trends_long.csv")
    parser.add_argument("--allow-weekly", action="store_true", default=config.TRENDS_ALLOW_WEEKLY,
                        help="accept weekly files as well as daily ones")
    args = parser.parse_args(argv)

    files = sorted(p for p in paths.raw_trends_dir.glob("*") if p.is_file() and not p.name.startswith("."))
    if not files:
        print(f"No exports found in {paths.raw_trends_dir}. See docs/trends_export_guide.md.")
        return 0

    bad_names = []
    for path in files:
        try:
            parse_filename(path.name)
        except TrendsFileError as error:
            bad_names.append((path, error))
    if bad_names:
        for path, error in bad_names:
            log_collection(paths, SOURCE, "", 0, "failed", str(error))
            print(f"ERROR {path.name}: {error}", file=sys.stderr)
        print(f"\nFAILED: {len(bad_names)} badly named file(s) in {paths.raw_trends_dir}. "
              "Rename them and run again; nothing was written.", file=sys.stderr)
        return 1

    products_by_id = {p["product_id"]: p for p in read_products(paths.products_csv)}
    loaded, errors = {}, {}
    for path in files:
        try:
            rows = load_file(path, products_by_id)
            check_granularity(rows, args.allow_weekly)
            loaded[path.name] = rows
        except (TrendsFileError, UnicodeDecodeError) as error:
            errors[path.name] = str(error)
    errors.update(check_date_ranges(loaded))

    if errors:
        for path in files:
            pid = parse_filename(path.name)[0]
            if path.name in errors:
                log_collection(paths, SOURCE, pid, 0, "failed", f"{path.name}: {errors[path.name]}")
                print(f"ERROR {path.name}: {errors[path.name]}", file=sys.stderr)
            else:
                log_collection(paths, SOURCE, pid, 0, "not_loaded",
                               f"{path.name}: valid, but other files failed validation")
        print(f"\nFAILED: {len(errors)} of {len(files)} file(s) failed validation; "
              f"{paths.trends_long_csv.name} was not written.", file=sys.stderr)
        return 1

    output = []
    for path in files:
        rows = loaded[path.name]
        output.extend(rows)
        log_collection(paths, SOURCE, rows[0]["product_id"], len(rows), "success")
        below = sum(r["is_below_threshold"] == "true" for r in rows)
        print(f"  {path.name}: {len(rows)} rows, {rows[0]['granularity']}, "
              f"{rows[0]['date']} to {rows[-1]['date']}, {below} '<1' values")

    with open(paths.trends_long_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADER)
        writer.writeheader()
        writer.writerows(output)
    print(f"\nWrote {len(output)} rows to {paths.trends_long_csv}.")
    print(f'Note: "<1" values are stored as search_interest={BELOW_THRESHOLD_VALUE} with is_below_threshold=true.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
