"""Validate the product registry.

Usage (from the repo root):
    python src/validate_products.py                          # every version in config.REGISTRY_FILES
    python src/validate_products.py data/products_v3.csv     # one file

Prints a PASS/FAIL line for each check, with the CSV line numbers of any
problems (line 1 is the header), and exits with status 1 if any check fails.
"""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

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
EXCLUSIONS_PATTERN = re.compile(r"( -[a-z0-9]+)+")


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
        query = rec["youtube_query"]
        if not rec["trends_query"] or query == expected:
            continue
        # Registry v2+ may narrow a query with YouTube's NOT operator: "<base> -term -term ..."
        exclusions = query[len(expected):] if query.startswith(expected + " ") else None
        if exclusions is None or not EXCLUSIONS_PATTERN.fullmatch(exclusions):
            problems.append(f"line {line}: youtube_query {query!r}, expected {expected!r} "
                            "optionally followed by exclusions like ' -pro -max'")
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


def validate_file(path):
    """Run every single-file check on one registry file, print the report, return True if all pass."""
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
    return not failed


def check_versions(versions):
    """Registry versions must list the same products in the same order and differ only in
    youtube_query. Returns (problems, {version: [changed product_ids vs the previous version]})."""
    problems, changes = [], {}
    previous_name, previous = None, None
    for name, path in versions:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if previous is not None:
            if [r["product_id"] for r in rows] != [r["product_id"] for r in previous]:
                problems.append(f"{name}: product_ids differ from {previous_name}")
            else:
                changed = []
                for old, new in zip(previous, rows):
                    for col in REQUIRED_COLUMNS:
                        if col != "youtube_query" and old.get(col) != new.get(col):
                            problems.append(f"{name}: {new['product_id']} {col} changed from {previous_name} "
                                            "(only youtube_query may change between versions)")
                    if old["youtube_query"] != new["youtube_query"]:
                        changed.append(new["product_id"])
                changes[name] = changed
        previous_name, previous = name, rows
    return problems, changes


def main(argv=None):
    """With a path: validate that file. Without: validate every registry version in
    config.REGISTRY_FILES and check them against each other."""
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        ok = validate_file(Path(argv[0]))
        print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    import config

    versions = [(name, config.PATHS.registry_csv(name)) for name in config.REGISTRY_FILES]
    ok = True
    for name, path in versions:
        active = " (ACTIVE)" if name == config.ACTIVE_REGISTRY else ""
        print(f"=== registry {name}{active} ===")
        ok = validate_file(path) and ok
        print()
    problems, changes = check_versions(versions)
    print("=== across versions ===")
    print(f"[{'FAIL' if problems else 'PASS'}] same products, trends_query unchanged, only youtube_query differs")
    for p in problems:
        print(f"       - {p}")
    for name, changed in changes.items():
        print(f"  {name}: youtube_query changed for {', '.join(changed) or 'no products'}")
    ok = ok and not problems
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'} (active registry: {config.ACTIVE_REGISTRY})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
