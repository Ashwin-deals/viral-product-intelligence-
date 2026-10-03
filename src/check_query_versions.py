"""Check that every YouTube raw file and table row is tied to the query version that produced it.

Raw files (read only, never modified):
- the version comes from the file name tag "_q<version>"; untagged files are v1 by convention
  (they were written before tags existed);
- files written after the change also record query_version in their JSON envelope, which must
  match the tag;
- search and recent files record the query sent (request_params.q), which must equal that
  version's youtube_query in the registry;
- videos and comments files have no query; they must sit next to a search file with the same
  product, date and version (they were fetched for that search's video ids).
Writes data/youtube/raw_file_index.csv (file names, versions and queries; no personal data).

Tables (data/youtube/*.csv): first brought to the current schema (collect_youtube.migrate_extracts:
adds query_version = v1 to rows written before versions existed, the only version that existed
then). Then every row must have a known query_version, and every (product, date, version) in
product_daily / recent_window_daily must have its raw search file.

Usage: python src/check_query_versions.py      (exit 1 if any problem)
"""

import csv
import re
import sys

import config
from collect_youtube import RECENT_STEP, load_registries, migrate_extracts, read_raw

RAW_NAME = re.compile(rf"^(P\d{{3}})_(\d{{4}}-\d{{2}}-\d{{2}})(?:_q(v\d+))?_(search|videos|comments|{RECENT_STEP})\.json$")
INDEX_HEADER = ["file", "product_id", "snapshot_date", "step", "query_version", "version_source",
                "envelope_query_version", "query", "registry_query", "status"]


def index_raw_files(paths, registries):
    rows, problems = [], []
    files = sorted(paths.raw_youtube_dir.glob("*.json")) if paths.raw_youtube_dir.exists() else []
    names = {f.name for f in files}
    for path in files:
        match = RAW_NAME.match(path.name)
        if not match:
            problems.append(f"{path.name}: unexpected raw file name")
            continue
        pid, date, tag, step = match.groups()
        version = tag or "v1"
        doc = read_raw(path)
        envelope = doc.get("query_version", "")
        query = doc.get("request_params", {}).get("q", "")
        registry_query = registries.get(version, {}).get(pid, {}).get("youtube_query", "")
        status = []
        if version not in registries:
            status.append(f"unknown version {version}")
        if envelope and envelope != version:
            status.append(f"envelope says {envelope}")
        if step in ("search", RECENT_STEP) and query != registry_query:
            status.append(f"query {query!r} != registry {version} {registry_query!r}")
        if step in ("videos", "comments"):
            search = f"{pid}_{date}{'_q' + tag if tag else ''}_search.json"
            if search not in names:
                status.append(f"no matching {search}")
        problems += [f"{path.name}: {s}" for s in status]
        rows.append({
            "file": path.name, "product_id": pid, "snapshot_date": date, "step": step,
            "query_version": version, "version_source": "filename_tag" if tag else "untagged_v1_convention",
            "envelope_query_version": envelope, "query": query,
            "registry_query": registry_query if step in ("search", RECENT_STEP) else "",
            "status": "ok" if not status else "; ".join(status),
        })
    return rows, problems


def check_tables(paths, registries, raw_names):
    problems, summary = [], []
    tables = [
        (paths.videos_snapshot_csv, None),
        (paths.comments_snapshot_csv, None),
        (paths.product_daily_csv, "search"),
        (paths.recent_window_csv, RECENT_STEP),
    ]
    for path, raw_step in tables:
        if not path.exists():
            summary.append(f"{path.name}: missing (nothing to check)")
            continue
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if "query_version" not in reader.fieldnames:
                problems.append(f"{path.name}: no query_version column")
                continue
            rows = list(reader)
        bad = [r for r in rows if r["query_version"] not in registries]
        problems += [f"{path.name}: {len(bad)} row(s) with unknown query_version"] if bad else []
        if raw_step:
            for r in rows:
                tag = "" if r["query_version"] == "v1" else f"_q{r['query_version']}"
                name = f"{r['product_id']}_{r['snapshot_date']}{tag}_{raw_step}.json"
                if name not in raw_names:
                    problems.append(f"{path.name}: {r['product_id']} {r['snapshot_date']} {r['query_version']} "
                                    f"has no raw {name}")
        counts = {}
        for r in rows:
            counts[r["query_version"]] = counts.get(r["query_version"], 0) + 1
        summary.append(f"{path.name}: {len(rows)} rows, by version " +
                       ", ".join(f"{v}={n}" for v, n in sorted(counts.items())))
    return problems, summary


def main(paths=config.PATHS):
    registries = load_registries(paths)
    migrated = migrate_extracts(paths)
    if migrated:
        print(f"Backfilled query_version (and newer columns) in: {', '.join(migrated)}")
    rows, problems = index_raw_files(paths, registries)
    index_path = paths.youtube_data_dir / "raw_file_index.csv"
    if rows:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(index_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=INDEX_HEADER, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    tagged = sum(r["version_source"] == "filename_tag" for r in rows)
    print(f"Raw files: {len(rows)} indexed ({tagged} tagged, {len(rows) - tagged} untagged = v1 by convention) "
          f"-> {index_path.name}")
    table_problems, summary = check_tables(paths, registries, {r["file"] for r in rows})
    for line in summary:
        print(f"  {line}")
    problems += table_problems
    for p in problems:
        print(f"PROBLEM {p}", file=sys.stderr)
    print(f"\nQuery-version integrity: {'FAIL' if problems else 'PASS'} ({len(problems)} problem(s))")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
