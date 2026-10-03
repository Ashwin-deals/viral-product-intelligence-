"""Validate data/products.csv, the product registry.

Usage (from the repo root):
    python src/validate_products.py [path/to/products.csv]

Prints a PASS/FAIL line for each check, with the CSV line numbers of any
problems (line 1 is the header), and exits with status 1 if any check fails.
"""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "products.csv"

REQUIRED_COLUMNS = [
    "product_id",
    "product_name",
    "brand",
    "category",
    "trends_query",
    "youtube_query",
    "launch_period",
    "product_type",
    "notes",
]
PRODUCT_TYPES = {"RISING", "STABLE", "DECLINING"}
LAUNCH_PERIOD_PATTERN = re.compile(r"^(\d{4}-(0[1-9]|1[0-2])|established|unverified)$")
YOUTUBE_SUFFIX = " review"


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows = [(i, row) for i, row in enumerate(reader, start=2)]
    return header, rows


def check_columns(header, rows):
    problems = []
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        problems.append(f"missing columns: {', '.join(missing)}")
    elif header != REQUIRED_COLUMNS:
        problems.append(f"columns out of order: {header}")
    for line, row in rows:
        if len(row) != len(header):
            problems.append(f"line {line}: expected {len(header)} fields, found {len(row)}")
    return problems


def check_whitespace(records):
    problems = []
    for line, rec in records:
        for col, value in rec.items():
            if value != value.strip():
                problems.append(f"line {line}: leading/trailing space in {col}")
    return problems


def check_ascii(records):
    return [
        f"line {line}: non-ASCII character in {col}"
        for line, rec in records
        for col, value in rec.items()
        if not value.isascii()
    ]


def check_product_ids(records):
    problems = []
    seen = {}
    for expected, (line, rec) in enumerate(records, start=1):
        pid = rec["product_id"]
        if pid in seen:
            problems.append(f"line {line}: duplicate product_id {pid} (first on line {seen[pid]})")
            continue
        seen[pid] = line
        if pid != f"P{expected:03d}":
            problems.append(f"line {line}: product_id {pid!r}, expected P{expected:03d}")
    return problems


def check_queries(records):
    problems = []
    for col in ("trends_query", "youtube_query"):
        seen = {}
        for line, rec in records:
            query = rec[col]
            if not query.strip():
                problems.append(f"line {line}: empty {col}")
                continue
            key = query.strip().casefold()
            if key in seen:
                problems.append(f"line {line}: duplicate {col} {query!r} (first on line {seen[key]})")
            else:
                seen[key] = line
    for line, rec in records:
        expected = rec["trends_query"] + YOUTUBE_SUFFIX
        if rec["trends_query"] and rec["youtube_query"] != expected:
            problems.append(f"line {line}: youtube_query {rec['youtube_query']!r}, expected {expected!r}")
    return problems


def check_product_types(records):
    return [
        f"line {line}: invalid product_type {rec['product_type']!r}"
        for line, rec in records
        if rec["product_type"] not in PRODUCT_TYPES
    ]


def check_launch_periods(records):
    return [
        f"line {line}: invalid launch_period {rec['launch_period']!r}"
        for line, rec in records
        if not LAUNCH_PERIOD_PATTERN.match(rec["launch_period"])
    ]


def check_consistent_spelling(records, col):
    """Values that differ only in case or spacing must be spelled identically."""
    variants = defaultdict(lambda: defaultdict(list))
    for line, rec in records:
        key = re.sub(r"\s+", "", rec[col]).casefold()
        variants[key][rec[col]].append(line)
    problems = []
    for spellings in variants.values():
        if len(spellings) > 1:
            detail = "; ".join(f"{s!r} on lines {', '.join(map(str, ls))}" for s, ls in spellings.items())
            problems.append(f"inconsistent {col} spelling: {detail}")
    return problems


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    header, rows = load(path)
    print(f"Validating {path} ({len(rows)} rows)\n")

    column_problems = check_columns(header, rows)
    results = [("required columns present and well-formed", column_problems)]
    if not column_problems:
        records = [(line, dict(zip(header, row))) for line, row in rows]
        results += [
            ("product_id unique and sequential", check_product_ids(records)),
            ("queries non-empty, unique, fixed YouTube pattern", check_queries(records)),
            ("product_type in RISING/STABLE/DECLINING", check_product_types(records)),
            ("launch_period is YYYY-MM, established or unverified", check_launch_periods(records)),
            ("brand spelling consistent", check_consistent_spelling(records, "brand")),
            ("category spelling consistent", check_consistent_spelling(records, "category")),
            ("no leading/trailing spaces", check_whitespace(records)),
            ("plain ASCII only", check_ascii(records)),
        ]

    failed = False
    for name, problems in results:
        print(f"[{'FAIL' if problems else 'PASS'}] {name}")
        for p in problems:
            print(f"       - {p}")
        failed = failed or bool(problems)

    if not column_problems:
        types = defaultdict(int)
        brands = defaultdict(int)
        for _, rec in records:
            types[rec["product_type"]] += 1
            brands[rec["brand"]] += 1
        print("\nproduct_type counts: " + ", ".join(f"{t}={types[t]}" for t in sorted(types)))
        print("brand counts: " + ", ".join(f"{b}={n}" for b, n in sorted(brands.items())))

    print(f"\nOVERALL: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
