"""Archive the local-only data into backups/ (git-ignored). Nothing is uploaded anywhere.

Backed up: everything under raw/, plus every git-ignored file under data/ (the per-video and
per-comment YouTube extracts and cleaned outputs that are not committed). .env is never
included. Each run writes a new archive and never overwrites an earlier one:
    backups/local_data_<YYYYMMDDTHHMMSSZ>.tar.gz
    backups/local_data_<YYYYMMDDTHHMMSSZ>_manifest.csv   (path, size_bytes, sha256)
The manifest is also stored inside the archive as MANIFEST.csv.

Usage: python src/backup_local_data.py
"""

import csv
import hashlib
import io
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

import config

MANIFEST_HEADER = ["path", "size_bytes", "sha256"]
NEVER_BACKED_UP = {".env"}


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_ignored_files(paths, subdir):
    """Git-ignored files under subdir, relative to the repo root (empty if git is unavailable)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", subdir],
            cwd=paths.root, capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in out.splitlines() if line]


def files_to_back_up(paths):
    rel = {p.relative_to(paths.root).as_posix() for p in paths.raw_dir.rglob("*") if p.is_file()} \
        if paths.raw_dir.exists() else set()
    rel.update(git_ignored_files(paths, "data"))
    return sorted(r for r in rel if r.split("/")[-1] not in NEVER_BACKED_UP and not r.endswith(".tmp"))


def build_manifest(paths, files):
    return [
        {"path": rel, "size_bytes": (paths.root / rel).stat().st_size, "sha256": sha256_of(paths.root / rel)}
        for rel in files
    ]


def create_backup(paths=config.PATHS, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    files = files_to_back_up(paths)
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
    archive, manifest_path, manifest = create_backup(paths)
    total = sum(m["size_bytes"] for m in manifest)
    print(f"Backed up {len(manifest)} files ({total / 1e6:.1f} MB uncompressed) to {archive}")
    print(f"Archive size: {archive.stat().st_size / 1e6:.2f} MB. Manifest: {manifest_path}")
    print("Nothing was uploaded. Copy the archive somewhere safe (e.g. an encrypted drive) yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
