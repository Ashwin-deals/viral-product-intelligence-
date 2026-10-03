"""YouTube collector (YouTube Data API v3).

Main pass, per product: search.list -> videos.list -> commentThreads.list (top-N videos by views).
Recent pass (optional, --pass recent|both): one search.list with order=date and
publishedAfter=<run time - 7 days>, counting videos uploaded in the last 7 days (cap-aware).

Product fields come from the active registry (config.ACTIVE_REGISTRY); a products CSV passed
with --products only selects product_ids. Each product carries a query_version: the oldest
registry version with the same youtube_query. Raw files for query versions other than v1 get
a "_q<version>" tag, so re-collections with a new query never collide with earlier originals.

Original API responses are saved untouched under raw/youtube/ (one file per product, date,
query version and step, never overwritten; an existing file is reused as a cache). Flat
extracts are appended, without duplicates, to data/youtube/. Every attempt is logged to
data/collection_log.csv.

Usage (from the repo root):
    python src/collect_youtube.py --dry-run       # plan + quota estimate, no API calls
    python src/collect_youtube.py --smoke-test    # one search to check the key, writes only a log row
    python src/collect_youtube.py                 # collect data/pilot_products.csv
    python src/collect_youtube.py --all           # every product in the active registry, resumable
    python src/collect_youtube.py --pass recent   # only the 7-day recent-window pass
    python src/collect_youtube.py --only P044,P057
    python src/collect_youtube.py --plan          # quota scenarios for all products, no API calls
"""

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from common import (
    append_rows,
    append_rows_dedup,
    ensure_schema,
    log_collection,
    now_utc_iso,
    read_products,
    redact,
    today_in,
)
from title_match import title_classifier

SOURCE = "youtube"
SOURCE_RECENT = "youtube_recent"
PASSES = {"main": ("main",), "recent": ("recent",), "both": ("main", "recent")}
RECENT_STEP = f"recent{config.YOUTUBE_RECENT_WINDOW_DAYS}d"

VIDEOS_HEADER = [
    "snapshot_date", "product_id", "query_version", "video_id", "title", "channel_id",
    "published_at", "view_count", "like_count", "comment_count",
]
COMMENTS_HEADER = [
    "snapshot_date", "product_id", "query_version", "video_id", "comment_id", "text", "like_count",
    "published_at",
]
PRODUCT_DAILY_HEADER = [
    "snapshot_date", "product_id", "query_version", "video_result_count", "video_views_total",
    "video_comments_total", "search_total_results_approx", "result_cap_hit",
]
RECENT_HEADER = [
    "snapshot_date", "product_id", "query_version", "window_days", "published_after",
    "videos_published_7d", "videos_7d_on_target", "cap_hit", "search_total_results_approx",
]
QUOTA_HEADER = ["date", "units", "search_calls", "method", "run_timestamp"]

# A video id appears once per product, query version and day; the same video can belong to
# several products.
VIDEOS_KEY = ("snapshot_date", "product_id", "query_version", "video_id")
COMMENTS_KEY = ("snapshot_date", "product_id", "query_version", "comment_id")
PRODUCT_DAILY_KEY = ("snapshot_date", "product_id", "query_version")
RECENT_KEY = ("snapshot_date", "product_id", "query_version")

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "RATE_LIMIT_EXCEEDED"}
FATAL_REASONS = {"keyInvalid", "API_KEY_INVALID", "accessNotConfigured", "SERVICE_DISABLED", "keyExpired"}
COMMENTS_DISABLED_REASONS = {"commentsDisabled"}
VIDEO_UNAVAILABLE_REASONS = {"videoNotFound", "forbidden"}


class BudgetExhausted(Exception):
    """The next request would exceed --daily-budget (or the search.list call budget)."""


class QuotaExceededError(Exception):
    """The API reported that the project's quota is used up."""


class FatalApiError(Exception):
    """An error that will repeat for every product (bad key, API not enabled)."""


class ApiRequestError(Exception):
    def __init__(self, status, reasons, message):
        super().__init__(message)
        self.status = status
        self.reasons = reasons


class CommentsDisabledError(ApiRequestError):
    pass


def http_error_details(error):
    """Return (status, reasons, message) for a googleapiclient HttpError, without the API key."""
    status = int(getattr(getattr(error, "resp", None), "status", 0) or 0)
    reasons = set()
    message = ""
    try:
        body = json.loads(error.content.decode("utf-8"))["error"]
        message = body.get("message", "")
        reasons.update(e.get("reason", "") for e in body.get("errors", []))
        reasons.update(d.get("reason", "") for d in body.get("details", []) if isinstance(d, dict))
        if body.get("status"):
            reasons.add(body["status"])
    except (AttributeError, ValueError, KeyError, TypeError):
        message = str(error)
    reasons.discard("")
    return status, reasons, redact(f"HTTP {status} {sorted(reasons)}: {message}")


class QuotaTracker:
    """Counts quota units and search.list calls per quota day, persisted in quota_usage.csv.

    Each request is recorded as it is made (including failed requests and retries, which the
    API also charges), so the count survives crashes and is shared by runs on the same day.
    """

    def __init__(self, path, quota_date, unit_budget, search_call_budget, run_timestamp, persist=True):
        self.path = Path(path)
        self.quota_date = quota_date
        self.unit_budget = unit_budget
        self.search_call_budget = search_call_budget
        self.run_timestamp = run_timestamp
        self.persist = persist
        self.used_units, self.used_search_calls = self._load()
        self.run_units = 0
        self.run_search_calls = 0

    def _load(self):
        units = calls = 0
        if self.path.exists() and self.path.stat().st_size > 0:
            with open(self.path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row["date"] == self.quota_date:
                        units += int(row["units"])
                        calls += int(row.get("search_calls") or 0)
        return units, calls

    def can_afford(self, units, search_calls=0):
        return (
            self.used_units + units <= self.unit_budget
            and self.used_search_calls + search_calls <= self.search_call_budget
        )

    def require(self, units, search_calls=0, what="request"):
        if not self.can_afford(units, search_calls):
            raise BudgetExhausted(
                f"{what} needs {units} units / {search_calls} search calls; used today "
                f"{self.used_units}/{self.unit_budget} units, "
                f"{self.used_search_calls}/{self.search_call_budget} search calls"
            )

    def charge(self, method, units, search_calls=0):
        self.used_units += units
        self.used_search_calls += search_calls
        self.run_units += units
        self.run_search_calls += search_calls
        if self.persist:
            append_rows(
                self.path,
                QUOTA_HEADER,
                [{
                    "date": self.quota_date,
                    "units": units,
                    "search_calls": search_calls,
                    "method": method,
                    "run_timestamp": self.run_timestamp,
                }],
            )


class YouTubeApi:
    """Wraps a googleapiclient `youtube` service: budget checks, pacing, retries, error mapping."""

    def __init__(self, service, tracker, sleep=time.sleep, pause=config.REQUEST_PAUSE_SECONDS,
                 max_retries=config.MAX_RETRIES, backoff_base=config.BACKOFF_BASE_SECONDS):
        self.service = service
        self.tracker = tracker
        self.sleep = sleep
        self.pause = pause
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def search(self, **params):
        return self._call("search.list", lambda: self.service.search().list(**params),
                          config.QUOTA_COST_SEARCH_LIST, search_calls=1)

    def videos(self, **params):
        return self._call("videos.list", lambda: self.service.videos().list(**params),
                          config.QUOTA_COST_VIDEOS_LIST)

    def comment_threads(self, **params):
        return self._call("commentThreads.list", lambda: self.service.commentThreads().list(**params),
                          config.QUOTA_COST_COMMENT_THREADS_LIST)

    def _call(self, method, make_request, units, search_calls=0):
        from googleapiclient.errors import HttpError

        for attempt in range(self.max_retries + 1):
            self.tracker.require(units, search_calls, what=method)
            if self.pause:
                self.sleep(self.pause)
            try:
                response = make_request().execute(num_retries=0)
            except HttpError as error:
                self.tracker.charge(method, units, search_calls)
                status, reasons, message = http_error_details(error)
                if reasons & QUOTA_REASONS:
                    raise QuotaExceededError(message) from None
                if reasons & FATAL_REASONS:
                    raise FatalApiError(message) from None
                if reasons & COMMENTS_DISABLED_REASONS:
                    raise CommentsDisabledError(status, reasons, message) from None
                retryable = status == 429 or status >= 500
                if retryable and attempt < self.max_retries:
                    self.sleep(self.backoff_base * 2 ** attempt)
                    continue
                raise ApiRequestError(status, reasons, message) from None
            except OSError as error:  # connection resets, timeouts
                self.tracker.charge(method, units, search_calls)
                if attempt < self.max_retries:
                    self.sleep(self.backoff_base * 2 ** attempt)
                    continue
                raise ApiRequestError(0, set(), redact(f"network error: {error}")) from None
            self.tracker.charge(method, units, search_calls)
            return response
        raise AssertionError("unreachable")


def estimate_product_cost(max_videos, comment_videos, comments_per_video, cached=()):
    """Upper-bound (units, search_calls) for one product, skipping steps whose raw file is cached."""
    search_pages = math.ceil(max_videos / config.YOUTUBE_MAX_RESULTS_PER_SEARCH)
    video_batches = math.ceil(max_videos / config.YOUTUBE_VIDEOS_BATCH_SIZE)
    comment_pages = comment_videos * math.ceil(comments_per_video / config.YOUTUBE_COMMENTS_PAGE_SIZE)
    units = search_calls = 0
    if "search" not in cached:
        units += search_pages * config.QUOTA_COST_SEARCH_LIST
        search_calls += search_pages
    if "videos" not in cached:
        units += video_batches * config.QUOTA_COST_VIDEOS_LIST
    if "comments" not in cached:
        units += comment_pages * config.QUOTA_COST_COMMENT_THREADS_LIST
    return units, search_calls


def count_recent(response, trends_query):
    """(videos returned, videos whose title names the exact model) for a recent-pass response.
    order=date returns loose matches, so the title-matched count is the cleaner signal."""
    classify = title_classifier(trends_query)
    videos = [item for item in response.get("items", []) if item.get("id", {}).get("videoId")]
    return len(videos), sum(classify(item.get("snippet", {}).get("title", "")) == "on_target" for item in videos)


def recent_cost(cached=False):
    """(units, search_calls) for the recent-window pass: one search.list page."""
    return (0, 0) if cached else (config.QUOTA_COST_SEARCH_LIST, 1)


def raw_path(paths, product_id, snapshot_date, step, query_version="v1"):
    tag = "" if query_version == "v1" else f"_q{query_version}"
    return paths.raw_youtube_dir / f"{product_id}_{snapshot_date}{tag}_{step}.json"


def cached_steps(paths, product_id, snapshot_date, query_version="v1"):
    return {s for s in ("search", "videos", "comments")
            if raw_path(paths, product_id, snapshot_date, s, query_version).exists()}


def load_registries(paths):
    """{version: {product_id: row}} for every registry version file that exists, oldest first."""
    return {
        version: {p["product_id"]: p for p in read_products(paths.registry_csv(version))}
        for version in config.REGISTRY_FILES
        if paths.registry_csv(version).exists()
    }


def query_version(product_id, youtube_query, registries):
    """The oldest registry version whose youtube_query for this product equals youtube_query."""
    for version, products in registries.items():
        if product_id in products and products[product_id]["youtube_query"] == youtube_query:
            return version
    raise ValueError(f"{product_id}: youtube_query {youtube_query!r} is not in any registry version")


def resolve_products(rows, paths):
    """Take product_ids from rows, fields from the active registry, and attach query_version."""
    registries = load_registries(paths)
    if config.ACTIVE_REGISTRY not in registries:
        raise SystemExit(f"active registry {paths.active_registry_csv} is missing")
    active = registries[config.ACTIVE_REGISTRY]
    resolved = []
    for row in rows:
        pid = row["product_id"]
        if pid not in active:
            raise SystemExit(f"{pid} is not in the active registry {paths.active_registry_csv.name}")
        product = dict(active[pid])
        product["query_version"] = query_version(pid, product["youtube_query"], registries)
        resolved.append(product)
    return resolved


def write_raw(path, document):
    """Write a raw file once. Mode 'x' fails rather than overwrite an existing original."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        json.dump(document, f, ensure_ascii=False, indent=2)


def read_raw(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class YouTubeCollector:
    def __init__(self, api, tracker, paths, snapshot_date, max_videos=config.YOUTUBE_MAX_VIDEOS,
                 comment_videos=config.YOUTUBE_COMMENT_VIDEOS,
                 comments_per_video=config.YOUTUBE_COMMENTS_PER_VIDEO):
        self.api = api
        self.tracker = tracker
        self.paths = paths
        self.snapshot_date = snapshot_date
        self.max_videos = max_videos
        self.comment_videos = comment_videos
        self.comments_per_video = comments_per_video

    def _envelope(self, product, method, params):
        return {
            "source": "YouTube Data API v3",
            "method": method,
            "product_id": product["product_id"],
            "query_version": product.get("query_version", "v1"),
            "snapshot_date": self.snapshot_date,
            "retrieved_at": now_utc_iso(),
            "request_params": params,
        }

    def _load_or_fetch(self, product, step, fetch):
        path = raw_path(self.paths, product["product_id"], self.snapshot_date, step,
                        product.get("query_version", "v1"))
        if path.exists():
            return read_raw(path), True
        document = fetch(product)
        write_raw(path, document)
        return document, False

    def _fetch_search(self, product):
        params = {
            "part": "snippet",
            "q": product["youtube_query"],
            "type": "video",
            "regionCode": config.YOUTUBE_REGION_CODE,
            "order": config.YOUTUBE_SEARCH_ORDER,
        }
        responses = []
        page_token = None
        remaining = self.max_videos
        while remaining > 0:
            page_params = dict(params, maxResults=min(remaining, config.YOUTUBE_MAX_RESULTS_PER_SEARCH))
            if page_token:
                page_params["pageToken"] = page_token
            response = self.api.search(**page_params)
            responses.append(response)
            remaining -= len(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token or not response.get("items"):
                break
        return dict(self._envelope(product, "search.list", params), responses=responses)

    def _fetch_videos(self, video_ids):
        def fetch(product):
            params = {"part": "snippet,statistics"}
            responses = []
            for i in range(0, len(video_ids), config.YOUTUBE_VIDEOS_BATCH_SIZE):
                batch = video_ids[i:i + config.YOUTUBE_VIDEOS_BATCH_SIZE]
                responses.append(self.api.videos(id=",".join(batch), maxResults=len(batch), **params))
            return dict(self._envelope(product, "videos.list", params), responses=responses)
        return fetch

    def _fetch_comments(self, video_ids):
        def fetch(product):
            params = {"part": "snippet", "textFormat": "plainText", "order": "relevance"}
            videos = []
            for video_id in video_ids:
                entry = {"video_id": video_id, "status": "ok", "error": "", "responses": []}
                page_token = None
                remaining = self.comments_per_video
                try:
                    while remaining > 0:
                        page_params = dict(params, videoId=video_id,
                                           maxResults=min(remaining, config.YOUTUBE_COMMENTS_PAGE_SIZE))
                        if page_token:
                            page_params["pageToken"] = page_token
                        response = self.api.comment_threads(**page_params)
                        entry["responses"].append(response)
                        remaining -= len(response.get("items", []))
                        page_token = response.get("nextPageToken")
                        if not page_token or not response.get("items"):
                            break
                except CommentsDisabledError as error:
                    entry.update(status="comments_disabled", error=str(error))
                except ApiRequestError as error:
                    if not error.reasons & VIDEO_UNAVAILABLE_REASONS:
                        raise
                    entry.update(status="unavailable", error=str(error))
                videos.append(entry)
            return dict(self._envelope(product, "commentThreads.list", params), videos=videos)
        return fetch

    def _fetch_recent(self, product):
        published_after = (datetime.now(timezone.utc) - timedelta(days=config.YOUTUBE_RECENT_WINDOW_DAYS))
        params = {
            "part": "snippet",
            "q": product["youtube_query"],
            "type": "video",
            "regionCode": config.YOUTUBE_REGION_CODE,
            "order": "date",
            "publishedAfter": published_after.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "maxResults": config.YOUTUBE_RECENT_MAX_RESULTS,
        }
        response = self.api.search(**params)
        return dict(self._envelope(product, "search.list", params), responses=[response])

    def collect_recent(self, product):
        """Recent-window pass for one product. Returns (status, videos_counted, message)."""
        pid, qv = product["product_id"], product.get("query_version", "v1")
        cached = raw_path(self.paths, pid, self.snapshot_date, RECENT_STEP, qv).exists()
        units, search_calls = recent_cost(cached)
        self.tracker.require(units, search_calls, what=f"{pid} recent pass")
        doc, cached = self._load_or_fetch(product, RECENT_STEP, self._fetch_recent)
        response = doc["responses"][0]
        count, on_target = count_recent(response, product["trends_query"])
        cap_hit = count >= doc["request_params"]["maxResults"] and bool(response.get("nextPageToken"))
        append_rows_dedup(self.paths.recent_window_csv, RECENT_HEADER, [{
            "snapshot_date": self.snapshot_date,
            "product_id": pid,
            "query_version": qv,
            "window_days": config.YOUTUBE_RECENT_WINDOW_DAYS,
            "published_after": doc["request_params"]["publishedAfter"],
            "videos_published_7d": count,
            "videos_7d_on_target": on_target,
            "cap_hit": "true" if cap_hit else "false",
            "search_total_results_approx": response.get("pageInfo", {}).get("totalResults", ""),
        }], RECENT_KEY)
        message = f"{config.YOUTUBE_RECENT_MAX_RESULTS}-result cap hit; true count is higher" if cap_hit else ""
        return ("skipped_cached" if cached else "success"), count, message

    def collect_product(self, product):
        """Collect one product. Returns (status, records_added, message). Raises on failure."""
        pid = product["product_id"]
        cached = cached_steps(self.paths, pid, self.snapshot_date, product.get("query_version", "v1"))
        units, search_calls = estimate_product_cost(
            self.max_videos, self.comment_videos, self.comments_per_video, cached)
        self.tracker.require(units, search_calls, what=f"{pid} (estimated)")

        search_doc, _ = self._load_or_fetch(product, "search", self._fetch_search)
        video_ids = list(dict.fromkeys(
            item["id"]["videoId"]
            for response in search_doc["responses"]
            for item in response.get("items", [])
            if item.get("id", {}).get("videoId")
        ))
        videos_doc, _ = self._load_or_fetch(product, "videos", self._fetch_videos(video_ids))
        video_items = [item for response in videos_doc["responses"] for item in response.get("items", [])]
        top_ids = [
            item["id"] for item in sorted(
                video_items, key=lambda v: _int(v.get("statistics", {}).get("viewCount")), reverse=True
            )[: self.comment_videos]
        ]
        comments_doc, _ = self._load_or_fetch(product, "comments", self._fetch_comments(top_ids))

        last_search = search_doc["responses"][-1] if search_doc["responses"] else {}
        search_info = {
            "search_total_results_approx":
                (search_doc["responses"][0].get("pageInfo", {}).get("totalResults", "")
                 if search_doc["responses"] else ""),
            # more results existed beyond the ones fetched (the search stopped at --max-videos)
            "result_cap_hit": "true" if last_search.get("nextPageToken") else "false",
        }
        added = self._write_extracts(pid, video_items, comments_doc,
                                     product.get("query_version", "v1"), search_info)
        disabled = [v["video_id"] for v in comments_doc["videos"] if v["status"] == "comments_disabled"]
        unavailable = [v["video_id"] for v in comments_doc["videos"] if v["status"] == "unavailable"]
        notes = []
        if disabled:
            notes.append(f"comments disabled on {len(disabled)} video(s): {' '.join(disabled)}")
        if unavailable:
            notes.append(f"comments unavailable on {len(unavailable)} video(s): {' '.join(unavailable)}")
        if cached == {"search", "videos", "comments"}:
            status = "skipped_cached"
        elif disabled:
            status = "comments_disabled"
        else:
            status = "success"
        return status, added, "; ".join(notes)

    def _write_extracts(self, product_id, video_items, comments_doc, query_version="v1", search_info=None):
        video_rows = []
        for item in video_items:
            snippet, stats = item.get("snippet", {}), item.get("statistics", {})
            video_rows.append({
                "snapshot_date": self.snapshot_date,
                "product_id": product_id,
                "query_version": query_version,
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "channel_id": snippet.get("channelId", ""),
                "published_at": snippet.get("publishedAt", ""),
                "view_count": stats.get("viewCount", ""),
                "like_count": stats.get("likeCount", ""),
                "comment_count": stats.get("commentCount", ""),
            })
        comment_rows = []
        for video in comments_doc["videos"]:
            for response in video["responses"]:
                for thread in response.get("items", []):
                    top = thread["snippet"]["topLevelComment"]
                    snippet = top["snippet"]
                    comment_rows.append({
                        "snapshot_date": self.snapshot_date,
                        "product_id": product_id,
                        "query_version": query_version,
                        "video_id": thread["snippet"].get("videoId", video["video_id"]),
                        "comment_id": top["id"],
                        "text": snippet.get("textOriginal", snippet.get("textDisplay", "")),
                        "like_count": snippet.get("likeCount", ""),
                        "published_at": snippet.get("publishedAt", ""),
                    })
        daily_row = {
            "snapshot_date": self.snapshot_date,
            "product_id": product_id,
            "query_version": query_version,
            "video_result_count": len(video_rows),
            "video_views_total": sum(_int(r["view_count"]) for r in video_rows),
            "video_comments_total": sum(_int(r["comment_count"]) for r in video_rows),
            **(search_info or {"search_total_results_approx": "", "result_cap_hit": ""}),
        }
        added = append_rows_dedup(self.paths.videos_snapshot_csv, VIDEOS_HEADER, video_rows, VIDEOS_KEY)
        added += append_rows_dedup(self.paths.comments_snapshot_csv, COMMENTS_HEADER, comment_rows, COMMENTS_KEY)
        append_rows_dedup(self.paths.product_daily_csv, PRODUCT_DAILY_HEADER, [daily_row], PRODUCT_DAILY_KEY)
        return added


def run_collection(collector, products, paths, passes=("main",)):
    """Run the selected passes for every product, logging each attempt (one row per product and
    pass). Stops cleanly on budget or quota exhaustion and on fatal API errors."""
    tracker = collector.tracker
    steps = {"main": (SOURCE, collector.collect_product), "recent": (SOURCE_RECENT, collector.collect_recent)}
    counts = {}
    for product in products:
        pid = product["product_id"]
        stop = False
        for name in passes:
            source, collect = steps[name]
            units_before = tracker.run_units
            try:
                status, records, message = collect(product)
            except BudgetExhausted as error:
                status, records, message, stop = "aborted_budget", 0, str(error), True
            except QuotaExceededError as error:
                status, records, message, stop = "quota_exceeded", 0, str(error), True
            except FatalApiError as error:
                status, records, message, stop = "failed", 0, str(error), True
            except Exception as error:  # log and move on
                status, records, message = "failed", 0, f"{type(error).__name__}: {error}"
            qv = product.get("query_version", "v1")
            if qv != "v1":
                message = "; ".join(m for m in (f"query {qv}: {product['youtube_query']}", message) if m)
            units = tracker.run_units - units_before
            log_collection(paths, source, pid, records, status, message, units)
            key = status if len(passes) == 1 else f"{name}:{status}"
            counts[key] = counts.get(key, 0) + 1
            print(f"  {pid}  {name:<6} {status:<17} records={records:<5} units={units:<4} {redact(message)}")
            if stop:
                break
        if stop:
            print("Stopping run early.")
            break
    return counts


def migrate_extracts(paths):
    """Bring local extracts written before query versions to the current schema. Old rows are
    query version v1; product_daily's search fields are filled from the raw search files."""
    def fill_daily(row):
        search = raw_path(paths, row["product_id"], row["snapshot_date"], "search")
        info = {"query_version": "v1", "search_total_results_approx": "", "result_cap_hit": ""}
        if search.exists():
            responses = read_raw(search)["responses"]
            if responses:
                info["search_total_results_approx"] = responses[0].get("pageInfo", {}).get("totalResults", "")
                info["result_cap_hit"] = "true" if responses[-1].get("nextPageToken") else "false"
        return info

    trends_queries = {pid: p["trends_query"] for products in load_registries(paths).values()
                      for pid, p in products.items()}

    def fill_recent(row):
        raw = raw_path(paths, row["product_id"], row["snapshot_date"], RECENT_STEP, row["query_version"])
        query = trends_queries.get(row["product_id"])
        if raw.exists() and query:
            return {"videos_7d_on_target": count_recent(read_raw(raw)["responses"][0], query)[1]}
        return {"videos_7d_on_target": ""}

    changed = []
    for path, header, fill in [
        (paths.recent_window_csv, RECENT_HEADER, fill_recent),
        (paths.videos_snapshot_csv, VIDEOS_HEADER, lambda row: {"query_version": "v1"}),
        (paths.comments_snapshot_csv, COMMENTS_HEADER, lambda row: {"query_version": "v1"}),
        (paths.product_daily_csv, PRODUCT_DAILY_HEADER, fill_daily),
    ]:
        if ensure_schema(path, header, fill):
            changed.append(path.name)
    return changed


def load_api_key():
    from dotenv import load_dotenv

    load_dotenv(config.ENV_FILE)
    key = os.environ.get(config.API_KEY_ENV_VAR, "").strip()
    if not key:
        raise SystemExit(f"{config.API_KEY_ENV_VAR} is not set. Copy .env.example to .env and add your key.")
    return key


def build_service(api_key):
    from googleapiclient.discovery import build

    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def print_quota_summary(tracker):
    print(
        f"\nQuota this run: {tracker.run_units} units, {tracker.run_search_calls} search.list calls."
        f"\nQuota used on {tracker.quota_date} (Pacific): {tracker.used_units}/{tracker.unit_budget} units budget, "
        f"{tracker.used_search_calls}/{tracker.search_call_budget} search.list calls budget."
    )


def product_cost(paths, product, snapshot_date, args, passes):
    """Upper-bound (units, search_calls, cache note) for the selected passes of one product."""
    pid, qv = product["product_id"], product.get("query_version", "v1")
    units = search_calls = 0
    notes = []
    if "main" in passes:
        cached = cached_steps(paths, pid, snapshot_date, qv)
        u, c = estimate_product_cost(args.max_videos, args.comment_videos, args.comments_per_video, cached)
        units, search_calls = units + u, search_calls + c
        if cached:
            notes.append(f"main cached: {','.join(sorted(cached))}")
    if "recent" in passes:
        cached = raw_path(paths, pid, snapshot_date, RECENT_STEP, qv).exists()
        u, c = recent_cost(cached)
        units, search_calls = units + u, search_calls + c
        if cached:
            notes.append("recent cached")
    return units, search_calls, "; ".join(notes)


def dry_run(products, paths, snapshot_date, tracker, args, passes):
    print(f"DRY RUN for snapshot {snapshot_date}, passes: {', '.join(passes)}. No API calls, nothing written.\n")
    total_units = total_search = fitting = 0
    for p in products:
        units, search_calls, note = product_cost(paths, p, snapshot_date, args, passes)
        total_units += units
        total_search += search_calls
        if tracker.can_afford(total_units, total_search):
            fitting += 1
        print(f"  {p['product_id']} {p['query_version']}  q={p['youtube_query']!r:<46} units<={units:<4} "
              f"search_calls<={search_calls} {note}")
    described = []
    if "main" in passes:
        described.append(
            f"main: search.list up to {args.max_videos} videos, videos.list for those ids, commentThreads.list "
            f"for the top {args.comment_videos} videos by views (up to {args.comments_per_video} comments each)")
    if "recent" in passes:
        described.append(f"recent: one search.list, order=date, published in the last "
                         f"{config.YOUTUBE_RECENT_WINDOW_DAYS} days, up to {config.YOUTUBE_RECENT_MAX_RESULTS}")
    print(
        "\nPer product: " + "; ".join(described) + "."
        f"\nEstimated quota (upper bound): {total_units} units, {total_search} search.list calls."
        f"\nAlready used today (Pacific {tracker.quota_date}): {tracker.used_units} units, "
        f"{tracker.used_search_calls} search.list calls."
        f"\nBudget: {tracker.unit_budget} units, {tracker.search_call_budget} search.list calls -> "
        f"{'fits' if tracker.can_afford(total_units, total_search) else 'DOES NOT FIT; run would stop early'}."
        f"\nProducts that fit in today's remaining budget: {fitting} of {len(products)}."
    )


def smoke_test(product, paths, tracker, api_key):
    service = build_service(api_key)
    api = YouTubeApi(service, tracker, max_retries=1)
    try:
        response = api.search(part="snippet", q=product["youtube_query"], type="video",
                              regionCode=config.YOUTUBE_REGION_CODE, maxResults=5)
    except Exception as error:
        message = redact(f"{type(error).__name__}: {error}")
        log_collection(paths, SOURCE, product["product_id"], 0, "smoke_test_failed", message, tracker.run_units)
        print(f"SMOKE TEST FAILED: {message}")
        return 1
    items = response.get("items", [])
    log_collection(paths, SOURCE, product["product_id"], len(items), "smoke_test_ok", "", tracker.run_units)
    print(f"SMOKE TEST OK: search for {product['youtube_query']!r} returned {len(items)} videos.")
    for item in items:
        print(f"  - {item['snippet']['title']}")
    return 0


def resume_order(products, paths, snapshot_date):
    """Order for --all runs: products never collected first, then the stalest snapshot first
    (ties by product_id). Products already collected for snapshot_date go last; their raw
    files are cached, so they cost no quota. A run that stops on a budget cap therefore
    continues with the uncollected products next time."""
    last = {}
    if paths.product_daily_csv.exists() and paths.product_daily_csv.stat().st_size > 0:
        with open(paths.product_daily_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                last[row["product_id"]] = max(last.get(row["product_id"], ""), row["snapshot_date"])

    def key(product):
        pid = product["product_id"]
        steps = cached_steps(paths, pid, snapshot_date, product.get("query_version", "v1"))
        done_today = steps == {"search", "videos", "comments"}
        return (done_today, last.get(pid, ""), pid)

    return sorted(products, key=key)


def plan_scenarios(n_products, main_cost, recent_cost_, unit_budget, search_budget):
    """Quota needed for common schedules. Costs are (units, search_calls) per product."""
    (mu, ms), (ru, rs) = main_cost, recent_cost_
    lines = [f"Quota plan for {n_products} products (budget {unit_budget} units, {search_budget} search.list "
             f"calls per day; main pass {mu} units + {ms} search call(s), recent pass {ru} unit(s) + {rs} "
             f"search call(s) per product)", ""]

    def fits(units, calls):
        return "fits" if units <= unit_budget and calls <= search_budget else "EXCEEDS the daily budget"

    main_day = (n_products * mu, n_products * ms)
    recent_day = (n_products * ru, n_products * rs)
    both_day = (main_day[0] + recent_day[0], main_day[1] + recent_day[1])
    lines.append(f"  A. Main weekly only: {main_day[0]} units, {main_day[1]} search calls on the run day "
                 f"({fits(*main_day)}); {main_day[0]} units/week.")
    lines.append(f"  B. Recent daily only: {recent_day[0]} units, {recent_day[1]} search calls/day "
                 f"({fits(*recent_day)}); {recent_day[0] * 7} units/week.")
    week_units = main_day[0] + recent_day[0] * 7
    week_calls = main_day[1] + recent_day[1] * 7
    lines.append(f"  C. Main weekly + recent daily: {week_units} units and {week_calls} search calls per week. "
                 f"On the day both run: {both_day[0]} units, {both_day[1]} search calls ({fits(*both_day)}).")
    spare = search_budget - recent_day[1]
    if both_day[1] > search_budget and spare > 0 and ms:
        per_day = spare // ms
        days = -(-n_products // per_day)
        lines.append(f"     -> spread the main pass over {days} days (at most {per_day} products/day next to the "
                     f"daily recent pass): run `--all --pass recent` first, then `--all --pass main` on {days} "
                     f"consecutive days; the main run stops at the cap and the next run resumes stalest-first.")
    elif both_day[1] > search_budget:
        lines.append("     -> the recent pass alone uses the whole search budget; the main pass cannot run.")
    max_both = min(unit_budget // (mu + ru), search_budget // (ms + rs)) if (ms + rs) else n_products
    lines.append(f"  D. Both passes daily: {both_day[0]} units, {both_day[1]} search calls/day "
                 f"({fits(*both_day)}); at most {max_both} products/day can get both passes.")
    return lines


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--products", type=Path, help="products CSV (default: data/pilot_products.csv)")
    target.add_argument("--all", action="store_true",
                        help="every product in data/products.csv, stalest first; stops cleanly at a budget "
                             "cap and continues where it stopped on the next run")
    parser.add_argument("--max-videos", type=int, default=config.YOUTUBE_MAX_VIDEOS)
    parser.add_argument("--comment-videos", type=int, default=config.YOUTUBE_COMMENT_VIDEOS)
    parser.add_argument("--comments-per-video", type=int, default=config.YOUTUBE_COMMENTS_PER_VIDEO)
    parser.add_argument("--daily-budget", type=int, default=config.DEFAULT_DAILY_BUDGET_UNITS,
                        help="max quota units to use per (Pacific) day, across runs")
    parser.add_argument("--search-call-budget", type=int, default=config.DEFAULT_DAILY_SEARCH_CALL_BUDGET,
                        help="max search.list calls per (Pacific) day, across runs")
    parser.add_argument("--pass", dest="pass_", choices=sorted(PASSES), default="main",
                        help="main (default): search+videos+comments; recent: 7-day upload count; both")
    parser.add_argument("--only", help="comma-separated product_ids to restrict the run to, e.g. P044,P057")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--smoke-test", action="store_true")
    mode.add_argument("--plan", action="store_true", help="print quota scenarios for all products and exit")
    mode.add_argument("--migrate", action="store_true",
                      help="bring local extracts to the current schema (from raw files) and exit; no API calls")
    args = parser.parse_args(argv)
    for name in ("max_videos", "comment_videos", "comments_per_video", "daily_budget", "search_call_budget"):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be >= 0")
    if args.daily_budget > config.DEFAULT_DAILY_QUOTA_UNITS:
        parser.error(f"--daily-budget cannot exceed the API's {config.DEFAULT_DAILY_QUOTA_UNITS} units/day")
    if args.search_call_budget > config.SEARCH_LIST_DAILY_CALL_LIMIT:
        parser.error(f"--search-call-budget cannot exceed the API's "
                     f"{config.SEARCH_LIST_DAILY_CALL_LIMIT} search.list calls/day")
    return args


def main(argv=None, paths=config.PATHS):
    args = parse_args(argv)
    passes = PASSES[args.pass_]
    if args.plan:
        n = len(read_products(paths.active_registry_csv))
        main_cost = estimate_product_cost(args.max_videos, args.comment_videos, args.comments_per_video)
        print("\n".join(plan_scenarios(n, main_cost, recent_cost(), args.daily_budget, args.search_call_budget)))
        return 0
    if args.migrate:
        migrated = migrate_extracts(paths)
        print(f"Migrated: {', '.join(migrated)}" if migrated else "All extracts already use the current schema.")
        return 0
    products_csv = paths.active_registry_csv if args.all else (args.products or paths.pilot_products_csv)
    products = resolve_products(read_products(products_csv), paths)
    if args.only:
        wanted = [pid.strip().upper() for pid in args.only.split(",") if pid.strip()]
        unknown = set(wanted) - {p["product_id"] for p in products}
        if unknown:
            raise SystemExit(f"--only: {', '.join(sorted(unknown))} not in {products_csv.name}")
        products = [p for p in products if p["product_id"] in wanted]
    if not products:
        raise SystemExit(f"no products in {products_csv}")
    snapshot_date = today_in(config.SNAPSHOT_TIMEZONE)
    if args.all:
        products = resume_order(products, paths, snapshot_date)
    tracker = QuotaTracker(
        paths.quota_usage_csv, today_in(config.QUOTA_TIMEZONE), args.daily_budget,
        args.search_call_budget, now_utc_iso(),
        persist=not (args.dry_run or args.smoke_test),
    )

    if args.dry_run:
        dry_run(products, paths, snapshot_date, tracker, args, passes)
        return 0

    api_key = load_api_key()
    if args.smoke_test:
        code = smoke_test(products[0], paths, tracker, api_key)
        print_quota_summary(tracker)
        return code

    try:
        service = build_service(api_key)
    except Exception as error:
        raise SystemExit(redact(f"could not build YouTube client: {error}")) from None
    migrated = migrate_extracts(paths)
    if migrated:
        print(f"Migrated to the current schema: {', '.join(migrated)}")
    collector = YouTubeCollector(
        YouTubeApi(service, tracker), tracker, paths, snapshot_date,
        args.max_videos, args.comment_videos, args.comments_per_video,
    )
    print(f"Collecting {len(products)} products for snapshot {snapshot_date} "
          f"(registry {config.ACTIVE_REGISTRY}, passes: {', '.join(passes)}):")
    counts = run_collection(collector, products, paths, passes)
    print("\nStatus counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print_quota_summary(tracker)
    failures = {"failed", "quota_exceeded", "aborted_budget"}
    return 1 if any(k.split(":")[-1] in failures for k in counts) else 0


if __name__ == "__main__":
    sys.exit(main())
