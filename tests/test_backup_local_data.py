import csv
import hashlib
import io
import tarfile
from datetime import datetime, timezone

import pytest

import backup_local_data as bk

NOW = datetime(2026, 10, 3, 17, 0, 0, tzinfo=timezone.utc)


def make_files(paths):
    (paths.raw_dir / "youtube").mkdir(parents=True)
    (paths.raw_dir / "youtube" / "P001_2026-10-03_search.json").write_text('{"a": 1}')
    (paths.raw_dir / "trends").mkdir()
    (paths.raw_dir / "trends" / "P001_2026-10-03.csv").write_text("Day,x\n")
    (paths.root / ".env").write_text("YOUTUBE_API_KEY=secret")


def test_backup_archives_raw_with_manifest_and_hashes(paths):
    make_files(paths)

    archive, manifest_path, manifest = bk.create_backup(paths, now=NOW)

    assert archive.name == "local_data_20261003T170000Z.tar.gz"
    assert [m["path"] for m in manifest] == ["raw/trends/P001_2026-10-03.csv", "raw/youtube/P001_2026-10-03_search.json"]
    search = manifest[1]
    assert search["size_bytes"] == 8 and search["sha256"] == hashlib.sha256(b'{"a": 1}').hexdigest()
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        inner_manifest = tar.extractfile("MANIFEST.csv").read().decode()
        assert tar.extractfile("raw/youtube/P001_2026-10-03_search.json").read() == b'{"a": 1}'
    assert ".env" not in names and "MANIFEST.csv" in names
    assert inner_manifest == manifest_path.read_text()
    assert list(csv.DictReader(io.StringIO(inner_manifest)))[1]["sha256"] == search["sha256"]


def test_backup_never_overwrites(paths):
    make_files(paths)
    bk.create_backup(paths, now=NOW)
    with pytest.raises(FileExistsError):
        bk.create_backup(paths, now=NOW)
