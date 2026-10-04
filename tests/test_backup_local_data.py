import hashlib
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
    paths.youtube_data_dir.mkdir(parents=True)
    paths.comments_snapshot_csv.write_text("comment_id,text\nk1,hello\n")
    paths.processed_dir.mkdir(parents=True)
    (paths.processed_dir / "youtube_comments_clean.csv").write_text("comment_id,text\nk1,hello\n")
    (paths.root / "docs").mkdir()
    (paths.root / "docs" / "handoff_task1.md").write_text("# handoff\n")
    (paths.root / ".env").write_text("YOUTUBE_API_KEY=secret")
    (paths.data_dir / ".env").write_text("YOUTUBE_API_KEY=secret")  # a stray .env anywhere is skipped
    (paths.root / "src").mkdir()
    (paths.root / "src" / "x.py").write_text("print(1)\n")  # code is not part of the data backup


def test_backup_includes_raw_data_and_docs_with_hashes(paths):
    make_files(paths)

    archive, manifest_path, manifest = bk.create_backup(paths, now=NOW)

    names = [m["path"] for m in manifest]
    assert archive.name == "local_data_20261003T170000Z.tar.gz"
    assert "raw/youtube/P001_2026-10-03_search.json" in names
    assert "data/youtube/comments_snapshot.csv" in names  # tracked or not, it is backed up
    assert "data/processed/youtube_comments_clean.csv" in names
    assert "data/products.csv" in names and "docs/handoff_task1.md" in names
    assert not any(n.endswith(".env") for n in names) and "src/x.py" not in names
    search = next(m for m in manifest if m["path"] == "raw/youtube/P001_2026-10-03_search.json")
    assert search["size_bytes"] == 8 and search["sha256"] == hashlib.sha256(b'{"a": 1}').hexdigest()
    with tarfile.open(archive) as tar:
        tar_names = tar.getnames()
        assert tar.extractfile("MANIFEST.csv").read().decode() == manifest_path.read_text()
    assert sorted(tar_names) == sorted(names + ["MANIFEST.csv"])


def test_backup_never_includes_backups_folder(paths):
    make_files(paths)
    bk.create_backup(paths, now=NOW)
    later = datetime(2026, 10, 3, 18, 0, 0, tzinfo=timezone.utc)
    _, _, manifest = bk.create_backup(paths, now=later)
    assert not any(m["path"].startswith("backups/") for m in manifest)


def test_backup_never_overwrites(paths):
    make_files(paths)
    bk.create_backup(paths, now=NOW)
    with pytest.raises(FileExistsError):
        bk.create_backup(paths, now=NOW)


def test_backup_aborts_if_a_file_contains_an_api_key(paths, capsys):
    make_files(paths)
    (paths.data_dir / "leak.csv").write_text("key\nAIza" + "x" * 35 + "\n")
    assert bk.main(paths) == 1
    assert "data/leak.csv" in capsys.readouterr().err
    assert not paths.backups_dir.exists() or not list(paths.backups_dir.iterdir())
