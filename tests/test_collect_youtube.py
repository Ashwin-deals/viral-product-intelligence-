"""Collector tests with a fake googleapiclient service: no network, no API key."""

import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

import collect_youtube as cy
from common import append_rows, append_rows_dedup, redact
from conftest import read_csv, write_products

SNAPSHOT = "2026-10-03"
QUOTA_DATE = "2026-10-02"
FAKE_KEY = "AIza" + "x" * 35


def http_error(status, reason):
    body = {"error": {"code": status, "message": f"boom key={FAKE_KEY}", "errors": [{"reason": reason}]}}
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode())


def search_ok(params):
    n = params["maxResults"]
    return {"items": [{"id": {"kind": "youtube#video", "videoId": f"v{i}"}} for i in range(1, n + 1)],
            "pageInfo": {"totalResults": 1000, "resultsPerPage": n}, "nextPageToken": "next"}


def videos_ok(params):
    ids = params["id"].split(",")
    return {"items": [
        {"id": vid, "snippet": {"title": f"Video {vid}", "channelId": "c1", "publishedAt": "2026-09-20T00:00:00Z"},
         "statistics": {"viewCount": str(100 * int(vid[1:])), "likeCount": "5", "commentCount": "2"}}
        for vid in ids
    ]}


def comments_ok(params):
    vid = params["videoId"]
    return {"items": [
        {"id": f"{vid}_c{i}", "snippet": {"videoId": vid, "topLevelComment": {"id": f"{vid}_c{i}", "snippet": {
            "textOriginal": f"comment {i}, with a comma\nand a newline", "likeCount": i,
            "publishedAt": "2026-09-21T00:00:00Z"}}}}
        for i in (1, 2)
    ]}


class FakeService:
    """Mimics service.<resource>().list(**params).execute(). Handlers return a dict, raise, or
    return a list of outcomes consumed one per call."""

    def __init__(self, search=search_ok, videos=videos_ok, comments=comments_ok):
        self.handlers = {"search": search, "videos": videos, "commentThreads": comments}
        self.calls = []

    def _resource(self, name):
        service = self

        class Resource:
            def list(self, **params):
                service.calls.append((name, params))

                class Request:
                    def execute(self, num_retries=0):
                        outcome = service.handlers[name]
                        if isinstance(outcome, list):
                            outcome = outcome.pop(0)
                        if isinstance(outcome, Exception):
                            raise outcome
                        return outcome(params)
                return Request()
        return Resource()

    def search(self):
        return self._resource("search")

    def videos(self):
        return self._resource("videos")

    def commentThreads(self):
        return self._resource("commentThreads")


def make_collector(paths, service, budget=9000, search_budget=90, max_videos=3, comment_videos=2, sleeps=None):
    tracker = cy.QuotaTracker(paths.quota_usage_csv, QUOTA_DATE, budget, search_budget, "run-1")
    sleep = sleeps.append if sleeps is not None else (lambda s: None)
    api = cy.YouTubeApi(service, tracker, sleep=sleep, pause=0, max_retries=2, backoff_base=1.0)
    return cy.YouTubeCollector(api, tracker, paths, SNAPSHOT, max_videos=max_videos,
                               comment_videos=comment_videos, comments_per_video=100)


def calls_by_method(service):
    return [name for name, _ in service.calls]


# --- quota accounting ---------------------------------------------------------

def test_quota_units_counted_and_persisted(paths, products):
    service = FakeService()
    collector = make_collector(paths, service)

    status, records, _ = collector.collect_product(products[0])

    assert status == "success"
    assert calls_by_method(service) == ["search", "videos", "commentThreads", "commentThreads"]
    assert collector.tracker.run_units == 4  # 1 unit each (verified costs)
    assert collector.tracker.run_search_calls == 1
    usage = read_csv(paths.quota_usage_csv)
    assert sum(int(r["units"]) for r in usage) == 4
    assert {r["date"] for r in usage} == {QUOTA_DATE}
    # a later run on the same quota day starts from the persisted total
    again = cy.QuotaTracker(paths.quota_usage_csv, QUOTA_DATE, 9000, 90, "run-2")
    assert (again.used_units, again.used_search_calls) == (4, 1)


def test_estimate_matches_actual_upper_bound():
    assert cy.estimate_product_cost(50, 5, 100) == (1 + 1 + 5, 1)
    assert cy.estimate_product_cost(120, 5, 250) == (3 + 3 + 15, 3)
    assert cy.estimate_product_cost(50, 5, 100, cached={"search", "videos"}) == (5, 0)


def test_budget_aborts_before_exceeding(paths, products):
    service = FakeService()
    collector = make_collector(paths, service, budget=3)  # product needs 4 units

    counts = cy.run_collection(collector, products, paths)

    assert service.calls == []  # stopped before any request
    assert counts == {"aborted_budget": 1}  # second product not attempted
    log = read_csv(paths.collection_log_csv)
    assert [(r["product_id"], r["status"]) for r in log] == [("P001", "aborted_budget")]


def test_search_call_budget_respected(paths, products):
    service = FakeService()
    collector = make_collector(paths, service, search_budget=1)

    counts = cy.run_collection(collector, products, paths)

    assert counts == {"success": 1, "aborted_budget": 1}
    assert calls_by_method(service).count("search") == 1


def test_quota_exceeded_stops_run_and_redacts_key(paths, products):
    service = FakeService(search=http_error(403, "quotaExceeded"))
    collector = make_collector(paths, service)

    counts = cy.run_collection(collector, products, paths)

    assert counts == {"quota_exceeded": 1}
    log = read_csv(paths.collection_log_csv)
    assert log[0]["status"] == "quota_exceeded"
    assert log[0]["quota_units"] == "1"  # failed requests still cost quota
    assert FAKE_KEY not in log[0]["error_message"] and "[REDACTED]" in log[0]["error_message"]


def test_retries_5xx_with_exponential_backoff(paths, products):
    sleeps = []
    service = FakeService(search=[http_error(503, "backendError"), http_error(429, "rateLimitExceeded"), search_ok])
    collector = make_collector(paths, service, sleeps=sleeps)

    status, _, _ = collector.collect_product(products[0])

    assert status == "success"
    assert sleeps == [1.0, 2.0]
    assert collector.tracker.run_search_calls == 3  # every attempt is charged


def test_non_retryable_error_fails_product_and_run_continues(paths, products):
    service = FakeService(search=[http_error(400, "invalidParameter"), search_ok])
    collector = make_collector(paths, service)

    counts = cy.run_collection(collector, products, paths)

    assert counts == {"failed": 1, "success": 1}


# --- caching ------------------------------------------------------------------

def test_cached_raw_files_are_reused_not_refetched(paths, products):
    make_collector(paths, FakeService()).collect_product(products[0])
    raw_files = sorted(paths.raw_youtube_dir.iterdir())
    before = {p: p.read_bytes() for p in raw_files}

    service = FakeService()
    counts = cy.run_collection(make_collector(paths, service), products[:1], paths)

    assert service.calls == []
    assert counts == {"skipped_cached": 1}
    assert {p: p.read_bytes() for p in raw_files} == before  # originals untouched
    log = read_csv(paths.collection_log_csv)
    assert (log[-1]["status"], log[-1]["quota_units"], log[-1]["records_collected"]) == ("skipped_cached", "0", "0")


def test_partial_cache_only_fetches_missing_steps(paths, products):
    make_collector(paths, FakeService()).collect_product(products[0])
    (paths.raw_youtube_dir / f"P001_{SNAPSHOT}_comments.json").unlink()

    service = FakeService()
    status, _, _ = make_collector(paths, service).collect_product(products[0])

    assert status == "success"
    assert calls_by_method(service) == ["commentThreads", "commentThreads"]


def test_raw_files_are_never_overwritten(tmp_path):
    path = tmp_path / "x.json"
    cy.write_raw(path, {"a": 1})
    with pytest.raises(FileExistsError):
        cy.write_raw(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 1}


# --- flat extracts --------------------------------------------------------------

def test_extracts_written_and_deduplicated(paths, products):
    collector = make_collector(paths, FakeService())
    _, records, _ = collector.collect_product(products[0])
    assert records == 3 + 4  # 3 videos + 2 comments on each of the top 2 videos

    # writing the same data again adds nothing
    videos_doc = cy.read_raw(paths.raw_youtube_dir / f"P001_{SNAPSHOT}_videos.json")
    comments_doc = cy.read_raw(paths.raw_youtube_dir / f"P001_{SNAPSHOT}_comments.json")
    assert collector._write_extracts("P001", videos_doc["responses"][0]["items"], comments_doc) == 0

    videos = read_csv(paths.videos_snapshot_csv)
    comments = read_csv(paths.comments_snapshot_csv)
    daily = read_csv(paths.product_daily_csv)
    assert len(videos) == 3 and len(comments) == 4 and len(daily) == 1
    assert {c["video_id"] for c in comments} == {"v3", "v2"}  # top 2 by view count
    assert comments[0]["text"] == "comment 1, with a comma\nand a newline"
    assert daily[0] == {"snapshot_date": SNAPSHOT, "product_id": "P001", "query_version": "v1",
                        "video_result_count": "3", "video_views_total": "600", "video_comments_total": "6",
                        "search_total_results_approx": "1000", "result_cap_hit": "true"}


def test_same_video_kept_for_each_product(paths, products):
    collector = make_collector(paths, FakeService())
    collector.collect_product(products[0])
    collector.collect_product(products[1])

    videos = read_csv(paths.videos_snapshot_csv)
    assert len(videos) == 6
    assert {(v["product_id"], v["video_id"]) for v in videos} == {
        (p, f"v{i}") for p in ("P001", "P002") for i in (1, 2, 3)}


def test_append_rows_dedup_skips_existing_and_repeated_keys(tmp_path):
    path = tmp_path / "t.csv"
    header, key = ["d", "id", "x"], ("d", "id")
    assert append_rows_dedup(path, header, [{"d": "1", "id": "a", "x": "1"}], key) == 1
    rows = [{"d": "1", "id": "a", "x": "2"}, {"d": "1", "id": "b", "x": "3"}, {"d": "1", "id": "b", "x": "4"},
            {"d": "2", "id": "a", "x": "5"}]
    assert append_rows_dedup(path, header, rows, key) == 2
    assert [r["x"] for r in read_csv(path)] == ["1", "3", "5"]


# --- comments disabled ------------------------------------------------------------

def test_comments_disabled_is_not_a_failure(paths, products):
    def comments(params):
        if params["videoId"] == "v3":
            raise http_error(403, "commentsDisabled")
        return comments_ok(params)

    service = FakeService(comments=comments)
    counts = cy.run_collection(make_collector(paths, service), products[:1], paths)

    assert counts == {"comments_disabled": 1}
    log = read_csv(paths.collection_log_csv)
    assert "v3" in log[0]["error_message"]
    doc = cy.read_raw(paths.raw_youtube_dir / f"P001_{SNAPSHOT}_comments.json")
    assert [v["status"] for v in doc["videos"]] == ["comments_disabled", "ok"]
    assert len(read_csv(paths.comments_snapshot_csv)) == 2


# --- secrets ------------------------------------------------------------------------

def test_redact_removes_key(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "secret-value-123")
    text = f"GET https://youtube.googleapis.com/youtube/v3/search?q=x&key={FAKE_KEY}&alt=json secret-value-123"
    cleaned = redact(text)
    assert FAKE_KEY not in cleaned and "secret-value-123" not in cleaned
    assert "q=x" in cleaned


# --- full runs (--all) ----------------------------------------------------------------

def test_resume_order_puts_uncollected_and_stalest_first(paths, products):
    p3 = dict(products[0], product_id="P003", youtube_query="p3 review")
    append_rows(paths.product_daily_csv, cy.PRODUCT_DAILY_HEADER, [
        {"snapshot_date": "2026-09-20", "product_id": "P001", "video_result_count": 1,
         "video_views_total": 1, "video_comments_total": 1},
        {"snapshot_date": "2026-09-27", "product_id": "P003", "video_result_count": 1,
         "video_views_total": 1, "video_comments_total": 1},
    ])
    ordered = cy.resume_order([products[0], products[1], p3], paths, SNAPSHOT)
    assert [p["product_id"] for p in ordered] == ["P002", "P001", "P003"]


def test_all_run_stops_at_search_cap_and_resumes_next_run(paths, products):
    first = make_collector(paths, FakeService(), search_budget=1)
    counts = cy.run_collection(first, cy.resume_order(products, paths, SNAPSHOT), paths)
    assert counts == {"success": 1, "aborted_budget": 1}

    # next run (budget raised, or the next quota day): the uncollected product goes first,
    # the finished one is served from cache at no cost
    service = FakeService()
    second = make_collector(paths, service, search_budget=2)
    order = cy.resume_order(products, paths, SNAPSHOT)
    assert [p["product_id"] for p in order] == ["P002", "P001"]
    counts = cy.run_collection(second, order, paths)
    assert counts == {"success": 1, "skipped_cached": 1}
    assert calls_by_method(service).count("search") == 1
    log = read_csv(paths.collection_log_csv)
    assert [(r["product_id"], r["status"]) for r in log] == [
        ("P001", "success"), ("P002", "aborted_budget"), ("P002", "success"), ("P001", "skipped_cached")]


def test_cli_all_dry_run_uses_full_registry_without_api(paths, capsys, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert cy.main(["--all", "--dry-run"], paths=paths) == 0
    out = capsys.readouterr().out
    assert "P001" in out and "P002" in out and "Products that fit in today's remaining budget: 2 of 2" in out


def test_cli_rejects_budgets_above_api_limits(paths):
    with pytest.raises(SystemExit):
        cy.parse_args(["--search-call-budget", "101"])
    with pytest.raises(SystemExit):
        cy.parse_args(["--daily-budget", "10001"])
    with pytest.raises(SystemExit):
        cy.parse_args(["--all", "--products", "x.csv"])


# --- query versions -------------------------------------------------------------------

def use_v2_query(paths, products, pid="P002", query="pixel 11 review -pro"):
    v2 = [dict(p, youtube_query=query) if p["product_id"] == pid else p for p in products]
    write_products(paths.registry_csv("v2"), v2)


def test_resolve_products_takes_active_registry_fields_and_query_version(paths, products, monkeypatch):
    monkeypatch.setattr(cy.config, "ACTIVE_REGISTRY", "v2")
    use_v2_query(paths, products)
    resolved = cy.resolve_products([{"product_id": "P001"}, {"product_id": "P002"}], paths)
    assert [(p["product_id"], p["query_version"], p["youtube_query"]) for p in resolved] == [
        ("P001", "v1", "iphone 18 pro review"), ("P002", "v2", "pixel 11 review -pro")]
    with pytest.raises(SystemExit):
        cy.resolve_products([{"product_id": "P999"}], paths)


def test_new_query_version_gets_own_raw_files_and_rows(paths, products, monkeypatch):
    monkeypatch.setattr(cy.config, "ACTIVE_REGISTRY", "v2")
    v1_product = cy.resolve_products([{"product_id": "P002"}], paths)[0]
    make_collector(paths, FakeService()).collect_product(v1_product)
    use_v2_query(paths, products)
    v2_product = cy.resolve_products([{"product_id": "P002"}], paths)[0]

    service = FakeService()
    status, _, _ = make_collector(paths, service).collect_product(v2_product)

    assert status == "success" and service.calls[0][1]["q"] == "pixel 11 review -pro"  # not served from v1 cache
    names = sorted(p.name for p in paths.raw_youtube_dir.iterdir())
    assert f"P002_{SNAPSHOT}_search.json" in names and f"P002_{SNAPSHOT}_qv2_search.json" in names
    daily = read_csv(paths.product_daily_csv)
    assert [(r["product_id"], r["query_version"]) for r in daily] == [("P002", "v1"), ("P002", "v2")]
    videos = read_csv(paths.videos_snapshot_csv)
    assert sum(r["query_version"] == "v2" for r in videos) == 3  # same video ids kept per version


def test_only_flag_filters_products(paths, capsys):
    cy.main(["--all", "--only", "p002", "--dry-run"], paths=paths)
    out = capsys.readouterr().out
    assert "P002" in out and "P001" not in out
    with pytest.raises(SystemExit):
        cy.main(["--all", "--only", "P999", "--dry-run"], paths=paths)


# --- recent-window pass ------------------------------------------------------------------

def test_recent_pass_counts_uploads_and_caches(paths, products):
    service = FakeService()
    collector = make_collector(paths, service)

    status, count, message = collector.collect_recent(products[0])

    assert (status, count) == ("success", 50) and "cap hit" in message
    name, params = service.calls[0]
    assert name == "search" and params["order"] == "date" and params["maxResults"] == 50
    assert params["publishedAfter"].endswith("Z")
    assert collector.tracker.run_units == 1 and collector.tracker.run_search_calls == 1
    assert (paths.raw_youtube_dir / f"P001_{SNAPSHOT}_recent7d.json").exists()
    row = read_csv(paths.recent_window_csv)[0]
    assert (row["videos_published_7d"], row["cap_hit"], row["window_days"]) == ("50", "true", "7")
    assert row["published_after"] == params["publishedAfter"]

    service2 = FakeService()
    assert make_collector(paths, service2).collect_recent(products[0])[0] == "skipped_cached"
    assert service2.calls == [] and len(read_csv(paths.recent_window_csv)) == 1


def test_recent_pass_under_cap_is_not_flagged(paths, products):
    service = FakeService(search=lambda params: {"items": [{"id": {"videoId": "a"}}, {"id": {"videoId": "b"}}],
                                                 "pageInfo": {"totalResults": 2}})
    status, count, message = make_collector(paths, service).collect_recent(products[0])
    assert (status, count, message) == ("success", 2, "")
    assert read_csv(paths.recent_window_csv)[0]["cap_hit"] == "false"


def test_both_passes_logged_separately(paths, products):
    counts = cy.run_collection(make_collector(paths, FakeService()), products[:1], paths, passes=("main", "recent"))
    assert counts == {"main:success": 1, "recent:success": 1}
    log = read_csv(paths.collection_log_csv)
    assert [(r["source"], r["status"], r["quota_units"]) for r in log] == [
        ("youtube", "success", "4"), ("youtube_recent", "success", "1")]


def test_recent_pass_respects_search_cap(paths, products):
    counts = cy.run_collection(make_collector(paths, FakeService(), search_budget=1), products,
                               paths, passes=("recent",))
    assert counts == {"success": 1, "aborted_budget": 1}


def test_plan_scenarios_for_sixty_products():
    text = "\n".join(cy.plan_scenarios(60, (7, 1), (1, 1), 9000, 90))
    assert "A. Main weekly only: 420 units, 60 search calls on the run day (fits)" in text
    assert "B. Recent daily only: 60 units, 60 search calls/day (fits)" in text
    assert "On the day both run: 480 units, 120 search calls (EXCEEDS the daily budget)" in text
    assert "spread the main pass over 2 days (at most 30 products/day" in text
    assert "at most 45 products/day can get both passes" in text


def test_cli_plan_prints_without_api(paths, capsys, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert cy.main(["--plan"], paths=paths) == 0
    assert "Quota plan for 2 products" in capsys.readouterr().out


# --- schema migration ---------------------------------------------------------------------

def test_migrate_extracts_adds_query_version_and_search_fields(paths, products):
    make_collector(paths, FakeService()).collect_product(products[0])
    # rewrite the extracts in the pre-query-version schema
    old_headers = {
        paths.videos_snapshot_csv: [h for h in cy.VIDEOS_HEADER if h != "query_version"],
        paths.comments_snapshot_csv: [h for h in cy.COMMENTS_HEADER if h != "query_version"],
        paths.product_daily_csv: cy.PRODUCT_DAILY_HEADER[:2] + cy.PRODUCT_DAILY_HEADER[3:6],
    }
    expected = {path: read_csv(path) for path in old_headers}
    for path, header in old_headers.items():
        rows = read_csv(path)
        path.unlink()
        append_rows(path, header, [{k: r[k] for k in header} for r in rows])

    assert sorted(cy.migrate_extracts(paths)) == ["comments_snapshot.csv", "product_daily.csv",
                                                   "videos_snapshot.csv"]
    for path, rows in expected.items():
        assert read_csv(path) == rows  # identical to what a current-schema run writes
    assert cy.migrate_extracts(paths) == []  # idempotent


def test_recent_on_target_count_and_migration(paths, products):
    titles = ["iPhone 18 Pro review", "iPhone 18 Pro Max camera test", "Best phones of 2026", "iphone 18 pro &amp; more"]
    service = FakeService(search=lambda params: {"items": [
        {"id": {"videoId": f"r{i}"}, "snippet": {"title": t}} for i, t in enumerate(titles)]})
    make_collector(paths, service).collect_recent(products[0])
    row = read_csv(paths.recent_window_csv)[0]
    assert (row["videos_published_7d"], row["videos_7d_on_target"]) == ("4", "2")

    # a file written before the column existed is filled from the raw response
    old_header = [h for h in cy.RECENT_HEADER if h != "videos_7d_on_target"]
    paths.recent_window_csv.unlink()
    append_rows(paths.recent_window_csv, old_header, [{k: row[k] for k in old_header}])
    assert cy.main(["--migrate"], paths=paths) == 0
    assert read_csv(paths.recent_window_csv) == [row]
