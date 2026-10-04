"""Shared settings for the collection pipeline: paths, YouTube settings, quota constants."""

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
API_KEY_ENV_VAR = "YOUTUBE_API_KEY"


@dataclass(frozen=True)
class Paths:
    """All file locations, derived from one root so tests can point at a temp dir.

    raw/  holds untouched originals (API JSON, Google Trends export CSVs), append-only.
    data/ holds every CSV table the project generates (registry, logs, flat extracts).
    """

    root: Path

    @classmethod
    def from_root(cls, root):
        return cls(Path(root))

    @property
    def data_dir(self):
        return self.root / "data"

    @property
    def raw_dir(self):
        return self.root / "raw"

    @property
    def products_csv(self):
        """Registry v1 working copy (also read by the validator, pilot selection and checklist)."""
        return self.data_dir / "products.csv"

    def registry_csv(self, version):
        return self.data_dir / REGISTRY_FILES[version]

    @property
    def active_registry_csv(self):
        return self.registry_csv(ACTIVE_REGISTRY)

    @property
    def pilot_products_csv(self):
        return self.data_dir / "pilot_products.csv"

    @property
    def collection_log_csv(self):
        return self.data_dir / "collection_log.csv"

    @property
    def raw_youtube_dir(self):
        return self.raw_dir / "youtube"

    @property
    def youtube_data_dir(self):
        return self.data_dir / "youtube"

    @property
    def videos_snapshot_csv(self):
        return self.youtube_data_dir / "videos_snapshot.csv"

    @property
    def comments_snapshot_csv(self):
        return self.youtube_data_dir / "comments_snapshot.csv"

    @property
    def product_daily_csv(self):
        return self.youtube_data_dir / "product_daily.csv"

    @property
    def quota_usage_csv(self):
        return self.youtube_data_dir / "quota_usage.csv"

    @property
    def recent_window_csv(self):
        return self.youtube_data_dir / "recent_window_daily.csv"

    @property
    def processed_dir(self):
        return self.data_dir / "processed"

    @property
    def backups_dir(self):
        return self.root / "backups"

    @property
    def raw_trends_dir(self):
        return self.raw_dir / "trends"

    @property
    def trends_long_csv(self):
        return self.data_dir / "trends_long.csv"

    @property
    def trends_checklist_csv(self):
        return self.data_dir / "trends_export_checklist.csv"

    @property
    def raw_trends_native_dir(self):
        return self.raw_trends_dir / "pytrends_native"

    @property
    def trends_manifest_csv(self):
        return self.data_dir / "trends_collection_manifest.csv"


# --- Product registry versions -------------------------------------------------
# Every registry version keeps the same columns and product_ids; versions differ only in
# documented changes (see docs/registry_changelog.md). The collectors read ACTIVE_REGISTRY.
# trends_query is identical across versions; only youtube_query may change.
REGISTRY_FILES = {"v1": "products.csv", "v2": "products_v2.csv", "v3": "products_v3.csv"}  # oldest first
ACTIVE_REGISTRY = "v3"

PATHS = Paths.from_root(ROOT)

COLLECTION_LOG_HEADER = [
    "run_timestamp",
    "source",
    "product_id",
    "records_collected",
    "status",
    "error_message",
    "quota_units",
]

# Snapshot dates use India time (the market studied). Quota days follow the API's
# reset at midnight Pacific Time, so quota usage is bucketed by the Pacific date.
SNAPSHOT_TIMEZONE = "Asia/Kolkata"
QUOTA_TIMEZONE = "America/Los_Angeles"

# --- Pilot selection ---------------------------------------------------------
PILOT_SEED = 42
PILOT_COUNTS = {"RISING": 4, "STABLE": 4, "DECLINING": 2}

# --- Google Trends -------------------------------------------------------------
# Team decision (2026-10-03): India, past ~9 months, daily data. Google Trends only returns
# daily points for ranges of 269 days or less (longer ranges switch to weekly); this limit
# comes from secondary sources (Glimpse, pytrends-based packages), not official Google docs.
# Exports use a custom range of exactly TRENDS_WINDOW_DAYS days, identical for every product.
TRENDS_GEO = "India"
TRENDS_WINDOW_DAYS = 269
TRENDS_EXPECTED_GRANULARITY = "daily"
TRENDS_ALLOW_WEEKLY = False  # override per run with: python src/load_trends.py --allow-weekly

# The 2026-10-03 batch: the exact custom range of docs/trends_export_guide.md (269 days, inclusive).
# Files are named with TRENDS_FILE_DATE so they match data/trends_export_checklist.csv; the
# actual collection time is recorded in data/trends_collection_manifest.csv.
TRENDS_BATCH_START = "2026-01-08"
TRENDS_BATCH_END = "2026-10-03"
TRENDS_FILE_DATE = "2026-10-03"
TRENDS_GEO_CODE = "IN"
TRENDS_CATEGORY = 0  # All categories
TRENDS_GPROP = ""  # "" = Web Search

# --- Google Trends via pytrends (unofficial; src/collect_trends_pytrends.py) --------
# pytrends calls Google's undocumented web endpoints; there is no official Trends API for this.
# Be gentle: one term per request, long random pauses, own exponential backoff, stop early.
PYTRENDS_HL = "en-US"
PYTRENDS_TZ = -330  # minutes west of UTC as pytrends expects; India is UTC+5:30
PYTRENDS_TIMEOUT = (10, 30)  # connect, read seconds
PYTRENDS_DELAY_RANGE_SECONDS = (30, 75)  # random pause between products
PYTRENDS_BACKOFF_START_SECONDS = 60  # then 120, 240
PYTRENDS_MAX_ATTEMPTS = 4  # per product
PYTRENDS_STOP_AFTER_CONSECUTIVE_FAILURES = 3

# --- YouTube settings --------------------------------------------------------
YOUTUBE_REGION_CODE = "IN"
YOUTUBE_MAX_RESULTS_PER_SEARCH = 50  # API maximum for search.list maxResults
YOUTUBE_SEARCH_ORDER = "relevance"  # search.list `order`: date, rating, relevance, title, viewCount
YOUTUBE_MAX_VIDEOS = 50  # videos per product; >50 pages search.list (each page is another call)
YOUTUBE_VIDEOS_BATCH_SIZE = 50  # API maximum ids per videos.list call
YOUTUBE_COMMENT_VIDEOS = 5  # top-N videos (by view count) to fetch comments for
YOUTUBE_COMMENTS_PER_VIDEO = 100  # comment threads per video; 100 per page is the API maximum
YOUTUBE_COMMENTS_PAGE_SIZE = 100

# Optional second pass (--pass recent|both): newest videos uploaded in the last N days.
# search.list with order=date and publishedAfter=<run time - N days>, one page of up to 50.
# Title-based exact-model filter (src/title_match.py): products whose share of on-target video
# titles is below this are flagged (low_on_target_share) in the cleaned daily table.
ON_TARGET_MIN_SHARE = 0.60
# A product whose Google Trends series is more than this share of zero days (among days with a
# value) is flagged low_signal_product in the aligned table and the full report.
LOW_SIGNAL_ZERO_SHARE = 0.50
YOUTUBE_RECENT_WINDOW_DAYS = 7
YOUTUBE_RECENT_MAX_RESULTS = 50

# Request pacing and retries (429 and 5xx are retried with exponential backoff).
REQUEST_PAUSE_SECONDS = 0.5
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 2.0

# --- Quota constants (YouTube Data API v3) -----------------------------------
# Verified 2026-10-03 against the official docs (page "Last updated 2026-09-15 UTC"):
#   https://developers.google.com/youtube/v3/determine_quota_cost
#   https://developers.google.com/youtube/v3/getting-started#quota
# "Projects that enable the YouTube Data API have a default quota allocation of
#  100 search.list calls, 100 videos.insert calls, and 10,000 units per day combined
#  for all other endpoints." Daily quotas reset at midnight Pacific Time.
# Every request, including invalid ones and retries, costs at least one unit.
# search.list: "100 quota per day. Each call costs 1 quota."  (separate 100-call/day cap)
QUOTA_COST_SEARCH_LIST = 1
QUOTA_COST_VIDEOS_LIST = 1
QUOTA_COST_COMMENT_THREADS_LIST = 1
DEFAULT_DAILY_QUOTA_UNITS = 10_000
SEARCH_LIST_DAILY_CALL_LIMIT = 100
DEFAULT_DAILY_BUDGET_UNITS = 9_000  # stay below the 10,000 default with headroom
DEFAULT_DAILY_SEARCH_CALL_BUDGET = 90  # stay below the 100 search.list calls/day cap
