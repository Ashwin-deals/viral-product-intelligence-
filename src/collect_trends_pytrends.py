"""Collect Google Trends interest-over-time with pytrends (unofficial), gently.

pytrends calls Google's undocumented web endpoints (there is no official Google Trends API for
this). It can break or be rate-limited at any time, so this collector is deliberately slow:
- one search term per request, the settings of docs/trends_export_guide.md (India, All
  categories, Web Search, custom range config.TRENDS_BATCH_START..TRENDS_BATCH_END, daily);
- a random 30-75 s pause between products; no browser spoofing, proxies or identity rotation;
- on HTTP 429 or another transient error: wait 60 s, then 120 s, then 240 s (4 attempts);
- STOP RULE: after 3 products in a row fail, the run stops cleanly. Re-running resumes: products
  whose files exist are skipped ("skipped_cached").

Per product two files are written once (mode "x", never overwritten):
- raw/trends/pytrends_native/<product_id>_<date>.csv  the DataFrame pytrends returned, untouched
  (date index, the term's integer values, isPartial);
- raw/trends/<product_id>_<date>.csv  the same values in the layout of Google's website CSV
  export ("Category: All categories", blank line, "Day,<term>: (India)", date,value rows), so
  src/load_trends.py reads it unchanged.
<date> is config.TRENDS_FILE_DATE (the batch end date used by data/trends_export_checklist.csv);
the real collection time is in data/trends_collection_manifest.csv.

Known difference to the website export: pytrends returns integers, so interest above zero but
below 1 (shown as "<1" on the website) comes back as 0. is_below_threshold can therefore never
be true for this method, and its zeros mix true zeros with "<1".

If a website export already exists for a product (no native file), it is a manual export: the
product is skipped and recorded with method "manual"; methods are never mixed silently.

Every attempt is logged to data/collection_log.csv (source google_trends_pytrends).

Usage (from the repo root):
    python src/collect_trends_pytrends.py --dry-run        # plan + estimated duration, no requests
    python src/collect_trends_pytrends.py --smoke-test     # first pilot product only
    python src/collect_trends_pytrends.py                  # pilot products (= --pilot)
    python src/collect_trends_pytrends.py --all            # every product in the active registry
    python src/collect_trends_pytrends.py --only P003,P006 --max-products 2
"""

import argparse
import csv
import io
import json
import random
import sys
import time
import warnings
from datetime import date, datetime, timezone

import pandas as pd

import config
from common import log_collection, now_utc_iso, read_products, redact

SOURCE = "google_trends_pytrends"
MANIFEST_HEADER = ["product_id", "query", "method", "timeframe", "geo", "collected_at_utc", "rows",
                   "first_date", "last_date", "partial_rows", "status"]
REQUEST_SECONDS = 5  # rough time for the two requests per product, for duration estimates


class RateLimited(Exception):
    pass


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def timeframe():
    return f"{config.TRENDS_BATCH_START} {config.TRENDS_BATCH_END}"


def expected_days():
    return (date.fromisoformat(config.TRENDS_BATCH_END) - date.fromisoformat(config.TRENDS_BATCH_START)).days + 1


def native_path(paths, product_id):
    return paths.raw_trends_native_dir / f"{product_id}_{config.TRENDS_FILE_DATE}.csv"


def export_path(paths, product_id):
    return paths.raw_trends_dir / f"{product_id}_{config.TRENDS_FILE_DATE}.csv"


def classify(error):
    """Map an exception from pytrends/requests to RateLimited, TransientError or PermanentError."""
    if isinstance(error, (RateLimited, TransientError, PermanentError)):
        return error
    status = getattr(getattr(error, "response", None), "status_code", None)
    name = type(error).__name__
    message = redact(f"{name}: {error}")
    if status == 429 or name == "TooManyRequestsError":
        return RateLimited(message)
    if status is not None and status >= 500:
        return TransientError(message)
    try:
        import requests
        if isinstance(error, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return TransientError(message)
    except ImportError:
        pass
    if isinstance(error, json.JSONDecodeError):  # Google sometimes answers with an HTML page
        return TransientError(message)
    return PermanentError(message)


class PytrendsFetcher:
    """Calls pytrends for one term. The client is created on first use (it fetches a cookie).

    Compatibility with urllib3 2.x: pytrends 4.9.2 builds urllib3 Retry(method_whitelist=...),
    an argument urllib3 2 removed, whenever retries or backoff_factor > 0. We keep both at 0 and
    do our own backoff. pandas >= 2.2 warns about a fillna downcast inside pytrends; that
    FutureWarning is silenced around the call only.
    """

    def __init__(self):
        self.client = None

    def __call__(self, term):
        from pytrends.request import TrendReq

        if self.client is None:
            self.client = TrendReq(hl=config.PYTRENDS_HL, tz=config.PYTRENDS_TZ, timeout=config.PYTRENDS_TIMEOUT,
                                   retries=0, backoff_factor=0)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            self.client.build_payload([term], cat=config.TRENDS_CATEGORY, timeframe=timeframe(),
                                      geo=config.TRENDS_GEO_CODE, gprop=config.TRENDS_GPROP)
            return self.client.interest_over_time()


def backoff_schedule(attempts=config.PYTRENDS_MAX_ATTEMPTS, start=config.PYTRENDS_BACKOFF_START_SECONDS):
    """Seconds to wait after failed attempt 1, 2, ... (no wait after the last attempt)."""
    return [start * 2 ** k for k in range(attempts - 1)]


def to_export_text(df, term):
    """The website-export layout for one term, from a pytrends DataFrame. Values are unchanged."""
    lines = ["Category: All categories", "", f"Day,{term}: (India)"]
    for day, value in zip(df.index, df[term]):
        lines.append(f"{pd.Timestamp(day).strftime('%Y-%m-%d')},{int(value)}")
    return "\n".join(lines) + "\n"


def validate(df, term):
    """Facts about one result and a list of problems (reported, never fixed)."""
    days = pd.to_datetime(pd.Series(df.index)).dt.normalize()
    values = df[term] if term in df else pd.Series(dtype=float)
    partial = int(df["isPartial"].astype(str).str.lower().eq("true").sum()) if "isPartial" in df else 0
    facts = {
        "rows": len(df),
        "first_date": days.min().strftime("%Y-%m-%d") if len(df) else "",
        "last_date": days.max().strftime("%Y-%m-%d") if len(df) else "",
        "partial_rows": partial,
        "zero_share": float((values == 0).mean()) if len(values) else None,
    }
    problems = []
    if term not in df:
        problems.append(f"no column for {term!r}")
    if len(df) != expected_days():
        problems.append(f"{len(df)} rows, expected {expected_days()}")
    gaps = days.diff().dropna().dt.days
    if len(gaps) and not (gaps == 1).all():
        kind = "weekly" if (gaps == 7).all() else "monthly" if gaps.between(28, 31).all() else "irregular"
        problems.append(f"not daily ({kind} spacing)")
    if facts["first_date"] != config.TRENDS_BATCH_START or facts["last_date"] != config.TRENDS_BATCH_END:
        problems.append(f"range {facts['first_date']}..{facts['last_date']}, expected "
                        f"{config.TRENDS_BATCH_START}..{config.TRENDS_BATCH_END}")
    if partial:
        problems.append(f"{partial} isPartial row(s)")
    return facts, problems


def read_native(path):
    return pd.read_csv(path, index_col=0, parse_dates=True)


def read_export_rows(path):
    """(rows, first_date, last_date) of a website-layout export, without validating it."""
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        cells = next(csv.reader([line])) if line.strip() else []
        if len(cells) == 2 and cells[0][:4].isdigit():
            rows.append(cells[0])
    return len(rows), (min(rows) if rows else ""), (max(rows) if rows else "")


def write_once(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="") as f:
        f.write(text)


class Manifest:
    """data/trends_collection_manifest.csv: one row per product (latest state), upserted."""

    def __init__(self, paths, enabled=True):
        self.path = paths.trends_manifest_csv
        self.enabled = enabled
        self.rows = {}
        if self.path.exists() and self.path.stat().st_size:
            with open(self.path, newline="", encoding="utf-8") as f:
                self.rows = {r["product_id"]: r for r in csv.DictReader(f)}

    def upsert(self, **row):
        if self.rows.get(row["product_id"], {}).get("method") not in (None, row["method"]):
            row["status"] = f"{row['status']}; method changed from {self.rows[row['product_id']]['method']}"
        self.rows[row["product_id"]] = {k: row.get(k, "") for k in MANIFEST_HEADER}

    def save(self):
        if not self.enabled:
            return
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=MANIFEST_HEADER, lineterminator="\n")
            writer.writeheader()
            writer.writerows(self.rows[k] for k in sorted(self.rows))


def last_success_time(paths, product_id):
    if not paths.collection_log_csv.exists():
        return ""
    with open(paths.collection_log_csv, newline="", encoding="utf-8") as f:
        times = [r["run_timestamp"] for r in csv.DictReader(f)
                 if r["source"] == SOURCE and r["product_id"] == product_id and r["status"] == "success"]
    return times[-1] if times else ""


class Collector:
    def __init__(self, paths, fetch, sleep=time.sleep, rng=None, manifest=None, out=print):
        self.paths = paths
        self.fetch = fetch
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.manifest = manifest or Manifest(paths)
        self.out = out

    def log(self, pid, records, status, message=""):
        log_collection(self.paths, SOURCE, pid, records, status, message, 0)

    def manifest_row(self, product, method, collected_at, facts, status):
        self.manifest.upsert(product_id=product["product_id"], query=product["trends_query"], method=method,
                             timeframe=timeframe(), geo=config.TRENDS_GEO_CODE, collected_at_utc=collected_at,
                             rows=facts.get("rows", ""), first_date=facts.get("first_date", ""),
                             last_date=facts.get("last_date", ""), partial_rows=facts.get("partial_rows", ""),
                             status=status)

    def cached(self, product):
        """Handle existing files. Returns a result dict, or None if the product must be fetched."""
        pid, term = product["product_id"], product["trends_query"]
        native, export = native_path(self.paths, pid), export_path(self.paths, pid)
        if native.exists():
            df = read_native(native)
            if not export.exists():  # crashed between the two writes: derive the export again
                write_once(export, to_export_text(df, term))
            facts, problems = validate(df, term)
            self.manifest_row(product, "pytrends", last_success_time(self.paths, pid), facts,
                              "ok" if not problems else "; ".join(problems))
            self.log(pid, 0, "skipped_cached", native.name)
            return {"status": "skipped_cached", "facts": facts, "problems": problems}
        if export.exists():
            rows, first, last = read_export_rows(export)
            self.manifest_row(product, "manual", "", {"rows": rows, "first_date": first, "last_date": last},
                              "manual export present; not fetched")
            self.log(pid, 0, "skipped_cached", f"{export.name} (manual export)")
            return {"status": "skipped_cached", "facts": {"rows": rows}, "problems": []}
        return None

    def fetch_with_backoff(self, product):
        pid, term = product["product_id"], product["trends_query"]
        waits = backoff_schedule()
        for attempt in range(1, config.PYTRENDS_MAX_ATTEMPTS + 1):
            try:
                return self.fetch(term)
            except Exception as raw_error:  # classified below; nothing is swallowed silently
                error = classify(raw_error)
                kind = type(error).__name__
                self.log(pid, 0, "error", f"attempt {attempt}/{config.PYTRENDS_MAX_ATTEMPTS} {kind}: {error}")
                self.out(f"    {pid} attempt {attempt} failed ({kind}): {error}")
                if isinstance(error, PermanentError) or attempt == config.PYTRENDS_MAX_ATTEMPTS:
                    raise error
                self.out(f"    waiting {waits[attempt - 1]} s before retrying")
                self.sleep(waits[attempt - 1])
        raise AssertionError("unreachable")

    def collect(self, product):
        pid, term = product["product_id"], product["trends_query"]
        df = self.fetch_with_backoff(product)
        collected_at = now_utc_iso()
        if df is None or df.empty:
            self.manifest_row(product, "pytrends", collected_at, {"rows": 0}, "no_data")
            self.log(pid, 0, "no_data", "Google returned no data for this term")
            return {"status": "no_data", "facts": {"rows": 0}, "problems": ["no data returned"]}
        buffer = io.StringIO()
        df.to_csv(buffer)
        write_once(native_path(self.paths, pid), buffer.getvalue())
        write_once(export_path(self.paths, pid), to_export_text(df, term))
        facts, problems = validate(df, term)
        self.manifest_row(product, "pytrends", collected_at, facts, "ok" if not problems else "; ".join(problems))
        self.log(pid, facts["rows"], "success", "; ".join(problems))
        return {"status": "success", "facts": facts, "problems": problems}

    def run(self, products, max_products=None):
        results, failures_in_row, fetched = {}, 0, 0
        try:
            for i, product in enumerate(products):
                pid = product["product_id"]
                cached = self.cached(product)
                if cached:
                    results[pid] = cached
                    self.out(f"  {pid}  skipped_cached")
                    continue
                if max_products is not None and fetched >= max_products:
                    results[pid] = {"status": "deferred", "facts": {}, "problems": ["--max-products reached"]}
                    continue
                if fetched:
                    pause = self.rng.uniform(*config.PYTRENDS_DELAY_RANGE_SECONDS)
                    self.out(f"  pausing {pause:.0f} s")
                    self.sleep(pause)
                fetched += 1
                try:
                    result = self.collect(product)
                    failures_in_row = 0
                except (RateLimited, TransientError, PermanentError) as error:
                    failures_in_row += 1
                    result = {"status": "failed", "facts": {}, "problems": [str(error)]}
                    self.manifest_row(product, "pytrends", "", {}, f"failed: {type(error).__name__}")
                    self.log(pid, 0, "failed", f"{type(error).__name__}: {error}")
                results[pid] = result
                self.out(f"  {pid}  {result['status']:<8} rows={result['facts'].get('rows', '')} "
                         f"{'; '.join(result['problems'])}")
                if failures_in_row >= config.PYTRENDS_STOP_AFTER_CONSECUTIVE_FAILURES:
                    remaining = [p["product_id"] for p in products[i + 1:] if p["product_id"] not in results]
                    message = (f"stop rule: {failures_in_row} products in a row failed; not attempted: "
                               f"{', '.join(remaining) or 'none'}")
                    self.log("", 0, "stopped", message)
                    self.out(f"STOPPED: {message}")
                    for pid_left in remaining:
                        results[pid_left] = {"status": "not_attempted", "facts": {}, "problems": ["stopped"]}
                    break
        finally:
            self.manifest.save()
        return results


def select_products(args, paths):
    if args.all:
        products = read_products(paths.active_registry_csv)
    else:
        ids = [r["product_id"] for r in read_products(paths.pilot_products_csv)]
        active = {p["product_id"]: p for p in read_products(paths.active_registry_csv)}
        products = [active[pid] for pid in ids]
    if args.only:
        wanted = [pid.strip().upper() for pid in args.only.split(",") if pid.strip()]
        known = {p["product_id"] for p in products}
        unknown = [pid for pid in wanted if pid not in known]
        if unknown:
            raise SystemExit(f"--only: {', '.join(unknown)} not in the selected product list")
        products = [p for p in products if p["product_id"] in wanted]
    if args.smoke_test:
        products = products[:1]
    return products


def dry_run(products, paths, max_products):
    print(f"DRY RUN: no requests. geo={config.TRENDS_GEO_CODE}, timeframe={timeframe()}, "
          f"category={config.TRENDS_CATEGORY}, property=Web Search, one term per request.\n")
    to_fetch = []
    for p in products:
        if native_path(paths, p["product_id"]).exists():
            state = "cached (pytrends)"
        elif export_path(paths, p["product_id"]).exists():
            state = "cached (manual export)"
        else:
            state = "fetch"
            to_fetch.append(p)
        print(f"  {p['product_id']}  {p['trends_query']!r:<28} {state}")
    if max_products is not None:
        to_fetch = to_fetch[:max_products]
    n = len(to_fetch)
    low, high = config.PYTRENDS_DELAY_RANGE_SECONDS
    typical = n * REQUEST_SECONDS + max(n - 1, 0) * (low + high) / 2
    worst = typical + n * sum(backoff_schedule())
    print(f"\nWould fetch {n} product(s). Estimated duration: about {typical / 60:.0f} min "
          f"(pauses {low}-{high} s between products); up to {worst / 60:.0f} min if every product needs all retries.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--pilot", action="store_true", help="pilot products (default)")
    target.add_argument("--all", action="store_true", help="every product in the active registry")
    parser.add_argument("--only", help="comma-separated product_ids, e.g. P003,P006")
    parser.add_argument("--max-products", type=int, help="fetch at most N products this run")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--smoke-test", action="store_true",
                      help="first product of the selection only; writes only its files and log rows")
    args = parser.parse_args(argv)
    if args.max_products is not None and args.max_products < 1:
        parser.error("--max-products must be >= 1")
    return args


def main(argv=None, paths=config.PATHS, fetch=None, sleep=time.sleep):
    args = parse_args(argv)
    products = select_products(args, paths)
    if not products:
        raise SystemExit("no products selected")
    if args.dry_run:
        dry_run(products, paths, args.max_products)
        return 0
    started = datetime.now(timezone.utc)
    print(f"Collecting {len(products)} product(s) from Google Trends via pytrends "
          f"({'smoke test' if args.smoke_test else 'all' if args.all else 'pilot'}), timeframe {timeframe()}:")
    collector = Collector(paths, fetch or PytrendsFetcher(), sleep=sleep,
                          manifest=Manifest(paths, enabled=not args.smoke_test))
    results = collector.run(products, args.max_products)
    counts = {}
    for r in results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    minutes = (datetime.now(timezone.utc) - started).total_seconds() / 60
    print(f"\nDone in {minutes:.1f} min: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0 if not ({"failed", "not_attempted"} & counts.keys()) else 1


if __name__ == "__main__":
    sys.exit(main())
