"""Back up the local data folders into backups/ (git-ignored). Nothing is uploaded anywhere.

This is a backup of the local disk, not of git: every file under raw/, data/ and docs/ is
included whether or not git tracks it (raw API JSON, per-video and per-comment tables, cleaned
outputs, logs, docs). Excluded: any file named .env and the backups/ folder itself. As a
safety check the run aborts, writing nothing, if any file to be archived contains something
that looks like a Google API key. Each run writes a new archive and never overwrites an
earlier one:
    backups/local_data_<YYYYMMDDTHHMMSSZ>.tar.gz
    backups/local_data_<YYYYMMDDTHHMMSSZ>_manifest.csv   (path, size_bytes, sha256)
The manifest is also stored inside the archive as MANIFEST.csv.

Usage: python src/backup_local_data.py
"""

import csv
import hashlib
import io
import re
import sys
import tarfile
from datetime import datetime, timezone

import config

MANIFEST_HEADER = ["path", "size_bytes", "sha256"]
BACKED_UP_DIRS = ("raw", "data", "docs")
NEVER_BACKED_UP = {".env"}
API_KEY_PATTERN = re.compile(rb"AIza[0-9A-Za-z_\-]{35}")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_to_back_up(paths):
    """Every file under raw/, data/ and docs/, relative to the root; never .env or backups/."""
    rel = set()
    for name in BACKED_UP_DIRS:
        folder = paths.root / name
        if folder.exists():
            rel.update(p.relative_to(paths.root).as_posix() for p in folder.rglob("*") if p.is_file())
    backups = paths.backups_dir.relative_to(paths.root).as_posix() + "/"
    return sorted(r for r in rel if r.split("/")[-1] not in NEVER_BACKED_UP and not r.startswith(backups))


def files_with_api_keys(paths, files):
    found = []
    for rel in files:
        with open(paths.root / rel, "rb") as f:
            if API_KEY_PATTERN.search(f.read()):
                found.append(rel)
    return found


def build_manifest(paths, files):
    return [
        {"path": rel, "size_bytes": (paths.root / rel).stat().st_size, "sha256": sha256_of(paths.root / rel)}
        for rel in files
    ]


def create_backup(paths=config.PATHS, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    files = files_to_back_up(paths)
    leaked = files_with_api_keys(paths, files)
    if leaked:
        raise ValueError(f"refusing to back up: possible API key in {', '.join(leaked)}")
    manifest = build_manifest(paths, files)

    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    archive = paths.backups_dir / f"local_data_{stamp}.tar.gz"
    manifest_path = paths.backups_dir / f"local_data_{stamp}_manifest.csv"

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=MANIFEST_HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(manifest)
    manifest_bytes = buffer.getvalue().encode("utf-8")

    with open(archive, "xb") as raw_file, tarfile.open(fileobj=raw_file, mode="w:gz") as tar:
        for rel in files:
            tar.add(paths.root / rel, arcname=rel, recursive=False)
        info = tarfile.TarInfo("MANIFEST.csv")
        info.size = len(manifest_bytes)
        info.mtime = int(now.timestamp())
        tar.addfile(info, io.BytesIO(manifest_bytes))
    with open(manifest_path, "xb") as f:
        f.write(manifest_bytes)
    return archive, manifest_path, manifest


def main(paths=config.PATHS):
    try:
        archive, manifest_path, manifest = create_backup(paths)
    except ValueError as error:
        print(f"Backup aborted, nothing written: {error}", file=sys.stderr)
        return 1
    total = sum(m["size_bytes"] for m in manifest)
    print(f"Backed up {len(manifest)} files ({total / 1e6:.1f} MB uncompressed) to {archive}")
    print(f"Archive size: {archive.stat().st_size / 1e6:.2f} MB. Manifest: {manifest_path}")
    print("Nothing was uploaded. Copy the archive somewhere safe (e.g. an encrypted drive) yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
