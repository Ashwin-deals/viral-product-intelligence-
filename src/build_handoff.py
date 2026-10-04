"""Build the teammate handoff zip: data/handoff/task1_handoff.zip.

Contents (exactly these files, nothing from raw/ and never .env):
  aligned_daily.csv, trends_clean.csv, youtube_videos_clean.csv, youtube_comments_clean.csv,
  full_report.csv (all from data/processed/), handoff_task1.md (docs/), and MANIFEST.csv
  (file, source path, size_bytes, sha256).

The zip is deterministic: fixed file order and timestamps, so rebuilding with unchanged inputs
gives a byte-identical file. A missing input stops the build with a clear message (no partial zip).

Usage: python src/build_handoff.py
"""

import csv
import hashlib
import io
import sys
import zipfile

import config

ZIP_NAME = "task1_handoff.zip"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # zip entries need a timestamp; a fixed one keeps the zip reproducible


def handoff_files(paths):
    p = paths.processed_dir
    return [
        p / "aligned_daily.csv",
        p / "trends_clean.csv",
        p / "youtube_videos_clean.csv",
        p / "youtube_comments_clean.csv",
        p / "full_report.csv",
        paths.root / "docs" / "handoff_task1.md",
    ]


def build(paths=config.PATHS):
    files = handoff_files(paths)
    missing = [str(f.relative_to(paths.root)) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"missing handoff input(s): {', '.join(missing)}")
    for f in files:  # defensive: the list above never includes these, keep it that way
        rel = f.relative_to(paths.root).as_posix()
        if f.name == ".env" or rel.startswith("raw/"):
            raise ValueError(f"refusing to package {rel}")

    manifest = io.StringIO()
    writer = csv.writer(manifest, lineterminator="\n")
    writer.writerow(["file", "source", "size_bytes", "sha256"])
    entries = []
    for f in files:
        data = f.read_bytes()
        entries.append((f.name, data))
        writer.writerow([f.name, f.relative_to(paths.root).as_posix(), len(data), hashlib.sha256(data).hexdigest()])
    entries.append(("MANIFEST.csv", manifest.getvalue().encode("utf-8")))

    out_dir = paths.data_dir / "handoff"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ZIP_NAME
    tmp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    tmp.replace(target)
    return target, entries


def main(paths=config.PATHS):
    try:
        target, entries = build(paths)
    except (FileNotFoundError, ValueError) as error:
        print(f"Handoff not built: {error}", file=sys.stderr)
        return 1
    total = sum(len(d) for _, d in entries)
    print(f"Wrote {target} ({target.stat().st_size / 1e6:.2f} MB, {len(entries)} files, "
          f"{total / 1e6:.1f} MB uncompressed): " + ", ".join(n for n, _ in entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
