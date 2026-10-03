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
        return self.data_dir / "products.csv"

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
    def raw_trends_dir(self):
        return self.raw_dir / "trends"

    @property
    def trends_long_csv(self):
        return self.data_dir / "trends_long.csv"


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

# --- YouTube settings --------------------------------------------------------
YOUTUBE_REGION_CODE = "IN"
YOUTUBE_MAX_RESULTS_PER_SEARCH = 50  # API maximum for search.list maxResults
YOUTUBE_SEARCH_ORDER = "relevance"  # search.list `order`: date, rating, relevance, title, viewCount
YOUTUBE_MAX_VIDEOS = 50  # videos per product; >50 pages search.list (each page is another call)
YOUTUBE_VIDEOS_BATCH_SIZE = 50  # API maximum ids per videos.list call
YOUTUBE_COMMENT_VIDEOS = 5  # top-N videos (by view count) to fetch comments for
YOUTUBE_COMMENTS_PER_VIDEO = 100  # comment threads per video; 100 per page is the API maximum
YOUTUBE_COMMENTS_PAGE_SIZE = 100

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
