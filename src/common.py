"""Helpers shared by the collectors: the collection log, secret redaction, dates, CSV appends."""

import csv
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config

# Google API keys start with "AIza" and are 39 characters long. Also catch key=... in URLs,
# which googleapiclient includes in HttpError messages.
_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
_KEY_PARAM_PATTERN = re.compile(r"(key=)[^&\s\"']+")


def redact(text):
    """Remove API key values from text before it is printed or logged."""
    text = "" if text is None else str(text)
    secret = os.environ.get(config.API_KEY_ENV_VAR)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = _KEY_PATTERN.sub("[REDACTED]", text)
    return _KEY_PARAM_PATTERN.sub(r"\1[REDACTED]", text)


def now_utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_in(tz_name):
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def append_rows(path, header, rows):
    """Append dict rows to a CSV, writing the header first if the file is missing or empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def append_rows_dedup(path, header, rows, key_fields):
    """Append rows whose key is not already in the file (or earlier in `rows`). Returns rows added."""
    seen = set()
    if path.exists() and path.stat().st_size > 0:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(tuple(row[k] for k in key_fields))
    new_rows = []
    for row in rows:
        key = tuple(str(row[k]) for k in key_fields)
        if key not in seen:
            seen.add(key)
            new_rows.append(row)
    if new_rows:
        append_rows(path, header, new_rows)
    return len(new_rows)


def log_collection(paths, source, product_id, records_collected, status, error_message="", quota_units=0):
    """Append one row to data/collection_log.csv. Never rewrites existing rows."""
    append_rows(
        paths.collection_log_csv,
        config.COLLECTION_LOG_HEADER,
        [
            {
                "run_timestamp": now_utc_iso(),
                "source": source,
                "product_id": product_id,
                "records_collected": records_collected,
                "status": status,
                "error_message": redact(error_message),
                "quota_units": quota_units,
            }
        ],
    )


def read_products(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
