import zipfile

import pytest

import build_handoff as bh


def make_inputs(paths):
    paths.processed_dir.mkdir(parents=True)
    for name in ("aligned_daily.csv", "trends_clean.csv", "youtube_videos_clean.csv",
                 "youtube_comments_clean.csv", "full_report.csv"):
        (paths.processed_dir / name).write_text(f"col\n{name}\n")
    (paths.root / "docs").mkdir()
    (paths.root / "docs" / "handoff_task1.md").write_text("# handoff\n")
    (paths.root / ".env").write_text("YOUTUBE_API_KEY=secret")
    (paths.raw_dir / "trends").mkdir(parents=True)
    (paths.raw_dir / "trends" / "P001_2026-10-03.csv").write_text("x")


def test_zip_has_exactly_the_handoff_files_and_is_reproducible(paths):
    make_inputs(paths)
    assert bh.main(paths) == 0
    target = paths.data_dir / "handoff" / bh.ZIP_NAME
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
        manifest = zf.read("MANIFEST.csv").decode()
    assert names == ["aligned_daily.csv", "trends_clean.csv", "youtube_videos_clean.csv",
                     "youtube_comments_clean.csv", "full_report.csv", "handoff_task1.md", "MANIFEST.csv"]
    assert ".env" not in manifest and "raw/" not in manifest
    first = target.read_bytes()
    bh.main(paths)
    assert target.read_bytes() == first


def test_missing_input_builds_nothing(paths, capsys):
    make_inputs(paths)
    (paths.processed_dir / "full_report.csv").unlink()
    assert bh.main(paths) == 1
    assert "full_report.csv" in capsys.readouterr().err
    assert not (paths.data_dir / "handoff" / bh.ZIP_NAME).exists()
