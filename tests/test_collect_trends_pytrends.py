"""pytrends collector tests: fake fetchers and sleeps only, no network."""

import csv

import pandas as pd
import pytest

import collect_trends_pytrends as ct
import config
import load_trends
from conftest import PRODUCTS, read_csv


def trends_frame(term, days=None, freq="D", partial_last=False, zero_every=10):
    start = pd.Timestamp(config.TRENDS_BATCH_START)
    days = days if days is not None else ct.expected_days()
    index = pd.date_range(start, periods=days, freq=freq, name="date")
    values = [0 if i % zero_every == 0 else (i % 100) + 1 for i in range(days)]
    partial = [False] * days
    if partial_last:
        partial[-1] = True
    return pd.DataFrame({term: values, "isPartial": partial}, index=index)


class FakeFetch:
    """Returns frames or raises, from a per-term list of outcomes (default: a valid frame)."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.calls = []

    def __call__(self, term):
        self.calls.append(term)
        queue = self.outcomes.get(term)
        outcome = queue.pop(0) if queue else trends_frame(term)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TooManyRequestsError(Exception):  # same class name as pytrends.exceptions
    pass


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = FakeResponse(status)


def write_pilot(paths, products=PRODUCTS):
    with open(paths.pilot_products_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(products[0]))
        writer.writeheader()
        writer.writerows(products)


def run(paths, fetch, products=PRODUCTS, **kwargs):
    sleeps = []
    collector = ct.Collector(paths, fetch, sleep=sleeps.append, out=lambda *a: None, **kwargs)
    return collector.run([dict(p) for p in products]), sleeps


# --- export layout + loader ------------------------------------------------------------

def test_export_layout_loads_unchanged_with_load_trends(paths):
    run(paths, FakeFetch())
    text = ct.export_path(paths, "P001").read_text()
    assert text.startswith("Category: All categories\n\nDay,iphone 18 pro: (India)\n2026-01-08,0\n")

    assert load_trends.main([], paths=paths) == 0
    rows = read_csv(paths.trends_long_csv)
    p1 = [r for r in rows if r["product_id"] == "P001"]
    assert len(p1) == 269 and {r["granularity"] for r in p1} == {"daily"}
    assert (p1[0]["date"], p1[-1]["date"]) == ("2026-01-08", "2026-10-03")
    assert not any(r["is_below_threshold"] == "true" for r in rows)  # pytrends never yields "<1"


def test_native_file_keeps_pytrends_output_including_ispartial(paths):
    frame = trends_frame("iphone 18 pro", partial_last=True)
    run(paths, FakeFetch({"iphone 18 pro": [frame]}), products=PRODUCTS[:1])
    native = ct.read_native(ct.native_path(paths, "P001"))
    assert list(native.columns) == ["iphone 18 pro", "isPartial"]
    assert native["iphone 18 pro"].tolist() == frame["iphone 18 pro"].tolist()
    assert native["isPartial"].tolist()[-2:] == [False, True]


# --- validation --------------------------------------------------------------------------

def test_validation_accepts_269_daily_rows():
    facts, problems = ct.validate(trends_frame("t"), "t")
    assert problems == []
    assert (facts["rows"], facts["first_date"], facts["last_date"]) == (269, "2026-01-08", "2026-10-03")
    assert facts["zero_share"] == pytest.approx(27 / 269)


@pytest.mark.parametrize("frame, expected", [
    (trends_frame("t", days=268), "268 rows, expected 269"),
    (trends_frame("t", days=39, freq="7D"), "not daily (weekly spacing)"),
    (trends_frame("t", partial_last=True), "1 isPartial row(s)"),
])
def test_validation_reports_problems(frame, expected):
    _, problems = ct.validate(frame, "t")
    assert any(expected in p for p in problems)


# --- caching -----------------------------------------------------------------------------

def test_existing_files_are_skipped_not_refetched(paths):
    run(paths, FakeFetch())
    before = ct.native_path(paths, "P001").read_bytes()

    fetch = FakeFetch()
    results, sleeps = run(paths, fetch)

    assert fetch.calls == [] and sleeps == []
    assert {r["status"] for r in results.values()} == {"skipped_cached"}
    assert ct.native_path(paths, "P001").read_bytes() == before
    statuses = [r["status"] for r in read_csv(paths.collection_log_csv) if r["source"] == ct.SOURCE]
    assert statuses == ["success", "success", "skipped_cached", "skipped_cached"]


def test_manual_export_is_not_refetched_and_recorded_as_manual(paths):
    paths.raw_trends_dir.mkdir(parents=True)
    ct.export_path(paths, "P002").write_text("Category: All categories\n\nDay,pixel 11: (India)\n2026-01-08,5\n")
    fetch = FakeFetch()
    results, _ = run(paths, fetch)
    assert fetch.calls == ["iphone 18 pro"]
    manifest = {r["product_id"]: r for r in read_csv(paths.trends_manifest_csv)}
    assert manifest["P002"]["method"] == "manual" and manifest["P001"]["method"] == "pytrends"
    assert not ct.native_path(paths, "P002").exists()


# --- backoff and stop rule ---------------------------------------------------------------

def test_backoff_schedule():
    assert ct.backoff_schedule() == [60, 120, 240]


def test_rate_limit_retried_with_backoff_then_success(paths):
    fetch = FakeFetch({"iphone 18 pro": [TooManyRequestsError("429"), HttpError(503), trends_frame("iphone 18 pro")]})
    results, sleeps = run(paths, fetch, products=PRODUCTS[:1])
    assert results["P001"]["status"] == "success"
    assert sleeps == [60, 120]
    log = [r["status"] for r in read_csv(paths.collection_log_csv)]
    assert log == ["error", "error", "success"]


def test_permanent_error_not_retried(paths):
    fetch = FakeFetch({"iphone 18 pro": [HttpError(400)]})
    results, sleeps = run(paths, fetch, products=PRODUCTS[:1])
    assert results["P001"]["status"] == "failed" and sleeps == [] and len(fetch.calls) == 1


def test_stop_rule_after_three_consecutive_failures(paths):
    products = [dict(PRODUCTS[0], product_id=f"P00{i}", trends_query=f"term {i}") for i in range(1, 6)]
    fetch = FakeFetch({f"term {i}": [TooManyRequestsError("429")] * 4 for i in range(1, 6)})

    results, sleeps = run(paths, fetch, products=products)

    assert [results[f"P00{i}"]["status"] for i in range(1, 6)] == ["failed"] * 3 + ["not_attempted"] * 2
    assert len(fetch.calls) == 12  # 3 products x 4 attempts
    assert [s for s in sleeps if s in (60, 120, 240)] == [60, 120, 240] * 3
    log = read_csv(paths.collection_log_csv)
    assert log[-1]["status"] == "stopped" and "P004, P005" in log[-1]["error_message"]


def test_success_resets_failure_count(paths):
    products = [dict(PRODUCTS[0], product_id=f"P00{i}", trends_query=f"term {i}") for i in range(1, 6)]
    outcomes = {f"term {i}": [HttpError(400)] for i in (1, 2, 4, 5)}  # term 3 succeeds
    results, _ = run(paths, FakeFetch(outcomes), products=products)
    assert [results[f"P00{i}"]["status"] for i in range(1, 6)] == ["failed", "failed", "success", "failed", "failed"]


def test_pause_between_products_is_30_to_75_seconds(paths):
    products = [dict(PRODUCTS[0], product_id=f"P00{i}", trends_query=f"term {i}") for i in range(1, 5)]
    _, sleeps = run(paths, FakeFetch(), products=products)
    assert len(sleeps) == 3 and all(30 <= s <= 75 for s in sleeps)


def test_no_data_is_recorded_not_saved(paths):
    results, _ = run(paths, FakeFetch({"iphone 18 pro": [pd.DataFrame()]}), products=PRODUCTS[:1])
    assert results["P001"]["status"] == "no_data"
    assert not ct.export_path(paths, "P001").exists()


# --- CLI -----------------------------------------------------------------------------------

def test_smoke_test_writes_only_first_product_and_no_manifest(paths):
    write_pilot(paths)
    fetch = FakeFetch()
    assert ct.main(["--smoke-test"], paths=paths, fetch=fetch, sleep=lambda s: None) == 0
    assert fetch.calls == ["iphone 18 pro"]
    assert ct.export_path(paths, "P001").exists() and not ct.export_path(paths, "P002").exists()
    assert not paths.trends_manifest_csv.exists()


def test_max_products_defers_the_rest(paths):
    write_pilot(paths)
    fetch = FakeFetch()
    assert ct.main(["--max-products", "1"], paths=paths, fetch=fetch, sleep=lambda s: None) == 0
    assert fetch.calls == ["iphone 18 pro"]
    manifest = read_csv(paths.trends_manifest_csv)
    assert [r["product_id"] for r in manifest] == ["P001"]


def test_dry_run_makes_no_requests(paths, capsys):
    write_pilot(paths)
    fetch = FakeFetch()
    assert ct.main(["--dry-run", "--only", "P002"], paths=paths, fetch=fetch) == 0
    out = capsys.readouterr().out
    assert fetch.calls == [] and "Would fetch 1 product(s)" in out and "P001" not in out
