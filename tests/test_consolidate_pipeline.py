import pytest
import consolidate_pipeline as cp
import config


def test_dry_run_all_stages():
    res = cp.run_pipeline(dry_run=True, quiet=True)
    assert res.success is True
    assert len(res.stages) == len(cp.ALL_STAGES)
    for stage_name, stage_res in res.stages.items():
        assert stage_res.status == "DRY_RUN"
        assert stage_name in cp.ALL_STAGES


def test_run_selected_stages():
    res = cp.run_pipeline(stages=["validation"], quiet=True)
    assert res.success is True
    assert list(res.stages.keys()) == ["validation"]
    assert res.stages["validation"].status == "SUCCESS"


def test_invalid_stage_name_raises():
    with pytest.raises(ValueError, match="Unknown stage 'non_existent_stage'"):
        cp.run_pipeline(stages=["non_existent_stage"], quiet=True)


def test_cli_main_dry_run():
    rc = cp.main(["--dry-run", "--quiet"])
    assert rc == 0


def test_cli_main_skip_flags():
    rc = cp.main(["--dry-run", "--skip-validation", "--skip-handoff", "--quiet"])
    assert rc == 0


def test_cli_main_stage_selection():
    rc = cp.main(["--dry-run", "--stages", "validation", "trends_clean", "--quiet"])
    assert rc == 0


def test_pipeline_summary_counts():
    res = cp.run_pipeline(dry_run=True, quiet=True)
    assert isinstance(res.summary, dict)
    if (config.PATHS.processed_dir / "aligned_daily.csv").exists():
        assert "aligned_daily_rows" in res.summary
        assert res.summary["aligned_daily_rows"] == 16140
