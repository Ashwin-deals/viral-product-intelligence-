"""Tests for SCRUM-30 Consolidated Pipeline Orchestrator."""

import json
import pytest
import pandas as pd

import config
import run_full_pipeline as rfp


def test_dry_run_all_stages():
    res = rfp.run_full_pipeline(dry_run=True, quiet=True)
    assert res.success is True
    assert len(res.stages) == len(rfp.ALL_STAGES)
    for name, s_res in res.stages.items():
        assert s_res.status == "DRY_RUN"
        assert name in rfp.ALL_STAGES


def test_units_filtering_unit1():
    res = rfp.run_full_pipeline(units=["unit1"], dry_run=True, quiet=True)
    assert res.success is True
    expected_stages = rfp.UNIT_STAGE_MAP["unit1"]
    assert list(res.stages.keys()) == expected_stages
    for s_res in res.stages.values():
        assert s_res.unit == "unit1"


def test_units_filtering_unit2():
    res = rfp.run_full_pipeline(units=["unit2"], dry_run=True, quiet=True)
    assert res.success is True
    expected_stages = rfp.UNIT_STAGE_MAP["unit2"]
    assert list(res.stages.keys()) == expected_stages
    for s_res in res.stages.values():
        assert s_res.unit == "unit2"


def test_units_filtering_unit3():
    res = rfp.run_full_pipeline(units=["unit3"], dry_run=True, quiet=True)
    assert res.success is True
    expected_stages = rfp.UNIT_STAGE_MAP["unit3"]
    assert list(res.stages.keys()) == expected_stages
    for s_res in res.stages.values():
        assert s_res.unit == "unit3"


def test_unknown_unit_raises():
    with pytest.raises(ValueError, match="Unknown unit"):
        rfp.run_full_pipeline(units=["non_existent_unit"], quiet=True)


def test_unknown_stage_raises():
    with pytest.raises(ValueError, match="Unknown stage"):
        rfp.run_full_pipeline(stages=["non_existent_stage"], quiet=True)


def test_stage_selection_preserves_dag_order():
    # Provide stages in reverse order
    requested = [rfp.STAGE_UNIT3_FORECAST, rfp.STAGE_UNIT1_MASTER]
    res = rfp.run_full_pipeline(stages=requested, dry_run=True, quiet=True)
    assert res.success is True
    # Should execute in canonical DAG order: unit1_master before unit3_forecast
    assert list(res.stages.keys()) == [rfp.STAGE_UNIT1_MASTER, rfp.STAGE_UNIT3_FORECAST]


def test_missing_input_marks_stage_blocked(paths):
    # In a blank test fixture where inputs don't exist yet:
    res = rfp.run_full_pipeline(
        paths=paths,
        stages=[rfp.STAGE_UNIT1_MASTER],
        dry_run=False,
        quiet=True,
    )
    assert res.success is False
    assert rfp.STAGE_UNIT1_MASTER in res.stages
    assert res.stages[rfp.STAGE_UNIT1_MASTER].status == "BLOCKED"
    assert "Missing required input" in (res.stages[rfp.STAGE_UNIT1_MASTER].error_message or "")


def test_cross_unit_consistency_verification(paths):
    checks = rfp.verify_cross_unit_consistency(paths)
    assert isinstance(checks, list)
    # Since master_dataset is missing in fresh paths fixture, first check should report FAIL
    assert len(checks) > 0
    assert checks[0].check_name == "master_dataset_exists"
    assert checks[0].status == "FAIL"


def test_execution_summary_persistence(paths, tmp_path):
    # Test saving summary report
    res = rfp.run_full_pipeline(paths=paths, stages=[], dry_run=True, quiet=True)
    rfp.save_execution_summary(res, paths)
    assert paths.pipeline_execution_summary_csv.exists()
    assert paths.pipeline_execution_summary_json.exists()

    with open(paths.pipeline_execution_summary_json, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert "summary" in data
        assert "stages" in data
        assert "cross_unit_checks" in data


def test_cli_main_dry_run():
    rc = rfp.main(["--dry-run", "--quiet"])
    assert rc == 0


def test_cli_main_unit_selection():
    rc = rfp.main(["--units", "unit1", "--dry-run", "--quiet"])
    assert rc == 0
