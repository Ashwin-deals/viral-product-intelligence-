"""Pilot YouTube collector (YouTube Data API v3).

For each product: search.list -> videos.list -> commentThreads.list (top-N videos by views).
Original API responses are saved untouched under raw/youtube/ (one file per product, date and
step, never overwritten; an existing file is reused as a cache). Flat extracts are appended,
without duplicates, to data/youtube/. Every product attempt is logged to data/collection_log.csv.

Usage (from the repo root):
    python src/collect_youtube.py --dry-run       # plan + quota estimate, no API calls
    python src/collect_youtube.py --smoke-test    # one search to check the key, writes only a log row
    python src/collect_youtube.py                 # collect data/pilot_products.csv
    python src/collect_youtube.py --all           # every product in data/products.csv, resumable
"""

import argparse
import csv
import json
import math
import os
import sys
import time
from pathlib import Path

import config
from common import (
    append_rows,
    append_rows_dedup,
    log_collection,
    now_utc_iso,
    read_products,
    redact,
    today_in,
)

SOURCE = "youtube"

VIDEOS_HEADER = [
    "snapshot_date", "product_id", "video_id", "title", "channel_id",
    "published_at", "view_count", "like_count", "comment_count",
]
COMMENTS_HEADER = [
    "snapshot_date", "product_id", "video_id", "comment_id", "text", "like_count", "published_at",
]
PRODUCT_DAILY_HEADER = [
    "snapshot_date", "product_id", "video_result_count", "video_views_total", "video_comments_total",
]
QUOTA_HEADER = ["date", "units", "search_calls", "method", "run_timestamp"]

# A video id appears once per product per day; the same video can belong to several products.
VIDEOS_KEY = ("snapshot_date", "product_id", "video_id")
COMMENTS_KEY = ("snapshot_date", "product_id", "comment_id")
PRODUCT_DAILY_KEY = ("snapshot_date", "product_id")

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


def raw_path(paths, product_id, snapshot_date, step):
    return paths.raw_youtube_dir / f"{product_id}_{snapshot_date}_{step}.json"


def cached_steps(paths, product_id, snapshot_date):
    return {s for s in ("search", "videos", "comments") if raw_path(paths, product_id, snapshot_date, s).exists()}


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
            "snapshot_date": self.snapshot_date,
            "retrieved_at": now_utc_iso(),
            "request_params": params,
        }

    def _load_or_fetch(self, product, step, fetch):
        path = raw_path(self.paths, product["product_id"], self.snapshot_date, step)
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

    def collect_product(self, product):
        """Collect one product. Returns (status, records_added, message). Raises on failure."""
        pid = product["product_id"]
        cached = cached_steps(self.paths, pid, self.snapshot_date)
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

        added = self._write_extracts(pid, video_items, comments_doc)
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

    def _write_extracts(self, product_id, video_items, comments_doc):
        video_rows = []
        for item in video_items:
            snippet, stats = item.get("snippet", {}), item.get("statistics", {})
            video_rows.append({
                "snapshot_date": self.snapshot_date,
                "product_id": product_id,
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
                        "video_id": thread["snippet"].get("videoId", video["video_id"]),
                        "comment_id": top["id"],
                        "text": snippet.get("textOriginal", snippet.get("textDisplay", "")),
                        "like_count": snippet.get("likeCount", ""),
                        "published_at": snippet.get("publishedAt", ""),
                    })
        daily_row = {
            "snapshot_date": self.snapshot_date,
            "product_id": product_id,
            "video_result_count": len(video_rows),
            "video_views_total": sum(_int(r["view_count"]) for r in video_rows),
            "video_comments_total": sum(_int(r["comment_count"]) for r in video_rows),
        }
        added = append_rows_dedup(self.paths.videos_snapshot_csv, VIDEOS_HEADER, video_rows, VIDEOS_KEY)
        added += append_rows_dedup(self.paths.comments_snapshot_csv, COMMENTS_HEADER, comment_rows, COMMENTS_KEY)
        append_rows_dedup(self.paths.product_daily_csv, PRODUCT_DAILY_HEADER, [daily_row], PRODUCT_DAILY_KEY)
        return added


def run_collection(collector, products, paths):
    """Collect every product, logging each attempt. Stops cleanly on budget or quota exhaustion."""
    tracker = collector.tracker
    counts = {}
    for product in products:
        pid = product["product_id"]
        units_before = tracker.run_units
        stop = False
        try:
            status, records, message = collector.collect_product(product)
        except BudgetExhausted as error:
            status, records, message, stop = "aborted_budget", 0, str(error), True
        except QuotaExceededError as error:
            status, records, message, stop = "quota_exceeded", 0, str(error), True
        except FatalApiError as error:
            status, records, message, stop = "failed", 0, str(error), True
        except Exception as error:  # log and move on to the next product
            status, records, message = "failed", 0, f"{type(error).__name__}: {error}"
        units = tracker.run_units - units_before
        log_collection(paths, SOURCE, pid, records, status, message, units)
        counts[status] = counts.get(status, 0) + 1
        print(f"  {pid}  {status:<17} records={records:<5} units={units:<4} {redact(message)}")
        if stop:
            print("Stopping run early.")
            break
    return counts


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


def dry_run(products, paths, snapshot_date, tracker, args):
    print(f"DRY RUN for snapshot {snapshot_date}: no API calls, nothing written.\n")
    total_units = total_search = fitting = 0
    for p in products:
        cached = cached_steps(paths, p["product_id"], snapshot_date)
        units, search_calls = estimate_product_cost(
            args.max_videos, args.comment_videos, args.comments_per_video, cached)
        total_units += units
        total_search += search_calls
        if tracker.can_afford(total_units, total_search):
            fitting += 1
        note = f"cached: {','.join(sorted(cached))}" if cached else ""
        print(f"  {p['product_id']}  q={p['youtube_query']!r:<36} units<={units:<4} search_calls<={search_calls} {note}")
    print(
        f"\nPer product: search.list up to {args.max_videos} videos, videos.list for those ids, "
        f"commentThreads.list for the top {args.comment_videos} videos by views "
        f"(up to {args.comments_per_video} comments each)."
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
        done_today = cached_steps(paths, pid, snapshot_date) == {"search", "videos", "comments"}
        return (done_today, last.get(pid, ""), pid)

    return sorted(products, key=key)


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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--smoke-test", action="store_true")
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
    products_csv = paths.products_csv if args.all else (args.products or paths.pilot_products_csv)
    products = read_products(products_csv)
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
        dry_run(products, paths, snapshot_date, tracker, args)
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
    collector = YouTubeCollector(
        YouTubeApi(service, tracker), tracker, paths, snapshot_date,
        args.max_videos, args.comment_videos, args.comments_per_video,
    )
    print(f"Collecting {len(products)} products for snapshot {snapshot_date}:")
    counts = run_collection(collector, products, paths)
    print("\nStatus counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print_quota_summary(tracker)
    return 0 if not ({"failed", "quota_exceeded", "aborted_budget"} & counts.keys()) else 1


if __name__ == "__main__":
    sys.exit(main())
