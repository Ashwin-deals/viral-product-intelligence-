"""SCRUM-30: Unit 1, 2, 3 Execution Consolidation Orchestrator.

Integrates the completed implementations of Units 1, 2, and 3 into a single,
deterministic, and reproducible execution workflow for the Viral Product Intelligence project.

Architecture & Dependency DAG:
  Stage 0: [task1_prep]          Raw data validation, cleaning, and daily alignment (Task 1 & 2)
  Stage 1: [unit2_features]      Feature encoding, standard scaling, and surge label engineering (SCRUM-20)
  Stage 2: [unit1_master]        Canonical master dataset consolidation & 16,140-row validation (SCRUM-21)
  Stage 3: [unit3_text_corpus]   Comment text cleaning, tokenization, and corpus extraction (SCRUM-23)
  Stage 4: [unit3_ts_prep]       Chronological 80/20 split, ADF/KPSS stationarity & lag diagnostics (SCRUM-22)
  Stage 5: [unit3_forecast]      Method 1 time-series forecasting & model benchmarking (SCRUM-26/27)
  Stage 6: [unit2_method2]       Method 2 multimodal daily signals & strict leakage audit (SCRUM-24)
  Stage 7: [unit3_text_mining]   Descriptive VADER sentiment, LDA/NMF topics & aspect mining (SCRUM-28)
  Stage 8: [unit3_text_spotcheck] AI spot-check agreement evaluation (SCRUM-28)
  Stage 9: [unit3_text_eval]     Sentiment benchmark & lead-lag correlation vs search interest (SCRUM-29)
  Final:   [cross_unit_check]    Verification of consistency, schemas, and artifact completeness

Usage:
  # Run the complete end-to-end pipeline
  python src/run_full_pipeline.py

  # Fast smoke-test mode (skips plots, minimal topic iterations, ~15-20s)
  python src/run_full_pipeline.py --smoke-test

  # Run without generating visual plots
  python src/run_full_pipeline.py --no-plots

  # Run specific units
  python src/run_full_pipeline.py --units unit1
  python src/run_full_pipeline.py --units unit2
  python src/run_full_pipeline.py --units unit3
  python src/run_full_pipeline.py --units unit1 unit2

  # Run specific stages
  python src/run_full_pipeline.py --stages unit1_master unit3_ts_prep unit3_forecast

  # Dry-run validation (validates inputs, schemas, and contracts without execution)
  python src/run_full_pipeline.py --dry-run
"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Ensure UTF-8 output encoding on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        getattr(sys.stdout, "reconfigure")(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        getattr(sys.stderr, "reconfigure")(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure src directory is in sys.path for direct script execution
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import numpy as np
import pandas as pd

import config

# Stage Names
STAGE_TASK1_PREP = "task1_prep"
STAGE_UNIT2_FEATURES = "unit2_features"
STAGE_UNIT1_MASTER = "unit1_master"
STAGE_UNIT3_TEXT_CORPUS = "unit3_text_corpus"
STAGE_UNIT3_TS_PREP = "unit3_ts_prep"
STAGE_UNIT3_FORECAST = "unit3_forecast"
STAGE_UNIT2_METHOD2 = "unit2_method2"
STAGE_UNIT3_TEXT_MINING = "unit3_text_mining"
STAGE_UNIT3_TEXT_SPOTCHECK = "unit3_text_spotcheck"
STAGE_UNIT3_TEXT_EVAL = "unit3_text_eval"

ALL_STAGES = [
    STAGE_TASK1_PREP,
    STAGE_UNIT2_FEATURES,
    STAGE_UNIT1_MASTER,
    STAGE_UNIT3_TEXT_CORPUS,
    STAGE_UNIT3_TS_PREP,
    STAGE_UNIT3_FORECAST,
    STAGE_UNIT2_METHOD2,
    STAGE_UNIT3_TEXT_MINING,
    STAGE_UNIT3_TEXT_SPOTCHECK,
    STAGE_UNIT3_TEXT_EVAL,
]

UNIT_STAGE_MAP = {
    "unit1": [STAGE_TASK1_PREP, STAGE_UNIT1_MASTER],
    "unit2": [STAGE_UNIT2_FEATURES, STAGE_UNIT2_METHOD2],
    "unit3": [
        STAGE_UNIT3_TEXT_CORPUS,
        STAGE_UNIT3_TS_PREP,
        STAGE_UNIT3_FORECAST,
        STAGE_UNIT3_TEXT_MINING,
        STAGE_UNIT3_TEXT_SPOTCHECK,
        STAGE_UNIT3_TEXT_EVAL,
    ],
}


@dataclass
class StageResult:
    name: str
    unit: str
    status: str  # "SUCCESS", "FAILED", "BLOCKED", "SKIPPED", "DRY_RUN"
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    details: str = ""
    inputs_checked: List[str] = field(default_factory=list)
    outputs_produced: List[str] = field(default_factory=list)


@dataclass
class CrossUnitCheckResult:
    check_name: str
    unit_boundary: str
    status: str  # "PASS", "FAIL", "WARN"
    detail: str


@dataclass
class FullPipelineResult:
    success: bool
    stages: Dict[str, StageResult]
    total_duration_seconds: float
    summary: Dict[str, Any] = field(default_factory=dict)
    cross_unit_checks: List[CrossUnitCheckResult] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Stage Runners
# -----------------------------------------------------------------------------

def _run_task1_prep(paths: config.Paths, quiet: bool, **kwargs) -> Tuple[int, str]:
    import consolidate_pipeline
    # Check if raw files exist
    res = consolidate_pipeline.run_pipeline(paths=paths, quiet=quiet)
    if not res.success:
        failed_stage = next((name for name, r in res.stages.items() if r.status == "FAILED"), "unknown")
        err = res.stages[failed_stage].error_message if failed_stage in res.stages else "Stage failed"
        return 1, f"Task 1 prep failed at sub-stage '{failed_stage}': {err}"
    return 0, f"Task 1 prep completed ({len(res.stages)} sub-stages succeeded)"


def _run_unit2_features(paths: config.Paths, quiet: bool, **kwargs) -> Tuple[int, str]:
    import feature_encoding_scaling
    rc = feature_encoding_scaling.main(paths=paths)
    if rc != 0:
        return rc, "feature_encoding_scaling.main returned non-zero exit code"
    out_file = paths.features_encoded_scaled_csv
    if not out_file.exists():
        return 1, f"Output file missing: {out_file}"
    df = pd.read_csv(out_file)
    surges = int(float(df["surge_label"].sum())) if "surge_label" in df.columns else 0
    return 0, f"Engineered features & surge labels ({len(df):,} rows, {surges} surges, {len(df.columns)} cols)"


def _run_unit1_master(paths: config.Paths, quiet: bool, **kwargs) -> Tuple[int, str]:
    import build_master
    rc = build_master.main(paths=paths)
    if rc != 0:
        return rc, "build_master.main returned non-zero exit code"
    master_path = paths.master_dataset_csv
    if not master_path.exists():
        return 1, f"Master dataset missing: {master_path}"
    df = pd.read_csv(master_path)
    return 0, f"Master dataset validated ({len(df):,} rows x {len(df.columns)} cols, 60 products)"


def _run_unit3_text_corpus(paths: config.Paths, quiet: bool, no_plots: bool, **kwargs) -> Tuple[int, str]:
    import build_text_corpus
    argv = ["--no-plots"] if no_plots else []
    rc = build_text_corpus.main(argv=argv, paths=paths)
    if rc != 0:
        return rc, "build_text_corpus.main returned non-zero exit code"
    corpus_path = paths.text_corpus_csv
    df = pd.read_csv(corpus_path)
    return 0, f"Text corpus extracted ({len(df):,} comments across {df['product_id'].nunique()} products)"


def _run_unit3_ts_prep(paths: config.Paths, quiet: bool, no_plots: bool, **kwargs) -> Tuple[int, str]:
    import prep_time_series
    rc = prep_time_series.main(paths=paths, generate_plots=not no_plots, quiet=quiet)
    if rc != 0:
        return rc, "prep_time_series.main returned non-zero exit code"
    train_df = pd.read_csv(paths.time_series_train_csv)
    test_df = pd.read_csv(paths.time_series_test_csv)
    return 0, f"Chronological split & stationarity diagnostics ({len(train_df):,} train / {len(test_df):,} test)"


def _run_unit3_forecast(paths: config.Paths, quiet: bool, no_plots: bool, **kwargs) -> Tuple[int, str]:
    import forecast_method1
    rc = forecast_method1.main(paths=paths, generate_plots=not no_plots, quiet=quiet)
    if rc != 0:
        return rc, "forecast_method1.main returned non-zero exit code"
    comp_df = pd.read_csv(paths.method1_model_comparison_csv)
    model_col = "model_name" if "model_name" in comp_df.columns else "model"
    best_model = comp_df.loc[comp_df["test_mae"].idxmin()][model_col]
    best_mae = comp_df["test_mae"].min()
    return 0, f"Trained {len(comp_df)} forecasting models. Top model: {best_model} (MAE: {best_mae:.3f})"


def _run_unit2_method2(paths: config.Paths, quiet: bool, no_plots: bool, **kwargs) -> Tuple[int, str]:
    import isolate_method2_data
    rc = isolate_method2_data.main(paths=paths, generate_plots=not no_plots, quiet=quiet)
    if rc != 0:
        return rc, "isolate_method2_data.main returned non-zero exit code"
    train_df = pd.read_csv(paths.method2_train_csv)
    test_df = pd.read_csv(paths.method2_test_csv)
    return 0, f"Method 2 multimodal dataset isolated & audited ({len(train_df):,} train / {len(test_df):,} test)"


def _run_unit3_text_mining(paths: config.Paths, quiet: bool, no_plots: bool, smoke_test: bool, **kwargs) -> Tuple[int, str]:
    import text_mining
    argv = []
    if no_plots:
        argv.append("--no-plots")
    if smoke_test:
        argv.extend(["--ks", "3"])
    rc = text_mining.main(argv=argv, paths=paths)
    if rc != 0:
        return rc, "text_mining.main returned non-zero exit code"
    comments_df = pd.read_csv(paths.text_sentiment_comments_csv)
    scored = comments_df["sentiment_compound"].notna().sum()
    return 0, f"Sentiment & topic mining completed ({scored:,} scored comments)"


def _run_unit3_text_spotcheck(paths: config.Paths, quiet: bool, **kwargs) -> Tuple[int, str]:
    import evaluate_spotcheck
    rc = evaluate_spotcheck.main(argv=[], paths=paths)
    if rc != 0:
        return rc, "evaluate_spotcheck.main returned non-zero exit code"
    eval_df = pd.read_csv(paths.text_sentiment_spotcheck_eval_csv)
    return 0, f"Spot-check agreement evaluated ({len(eval_df):,} metric records)"


def _run_unit3_text_eval(paths: config.Paths, quiet: bool, **kwargs) -> Tuple[int, str]:
    import evaluate_text_mining
    rc = evaluate_text_mining.main(paths=paths)
    if rc != 0:
        return rc, "evaluate_text_mining.main returned non-zero exit code"
    comp_df = pd.read_csv(paths.text_eval_method_comparison_csv)
    lags_df = pd.read_csv(paths.text_signal_lead_lag_csv)
    return 0, f"Evaluated {len(comp_df)} sentiment methods and {len(lags_df)} signal lead-lag lags"


# -----------------------------------------------------------------------------
# Stage Specification Mapping
# -----------------------------------------------------------------------------

def get_stage_definitions(paths: config.Paths) -> Dict[str, Dict[str, Any]]:
    """Return formal specifications for each pipeline stage."""
    proc = paths.processed_dir
    root_proc = paths.root / "processed"

    return {
        STAGE_TASK1_PREP: {
            "unit": "unit1",
            "description": "Task 1 & 2 raw data validation, cleaning, and daily alignment",
            "inputs": [paths.active_registry_csv],
            "outputs": [
                proc / "aligned_daily.csv",
                proc / "youtube_videos_clean.csv",
                proc / "youtube_comments_clean.csv",
                proc / "full_report.csv",
            ],
            "runner": _run_task1_prep,
        },
        STAGE_UNIT2_FEATURES: {
            "unit": "unit2",
            "description": "Encode categoricals, scale numerical features, and engineer surge_label (SCRUM-20)",
            "inputs": [proc / "aligned_daily.csv"],
            "outputs": [paths.features_encoded_scaled_csv],
            "runner": _run_unit2_features,
        },
        STAGE_UNIT1_MASTER: {
            "unit": "unit1",
            "description": "Consolidate & validate canonical 60-product master dataset (SCRUM-21)",
            "inputs": [
                proc / "aligned_daily.csv",
                proc / "youtube_videos_clean.csv",
                proc / "youtube_comments_clean.csv",
                proc / "full_report.csv",
                paths.active_registry_csv,
            ],
            "outputs": [
                paths.master_dataset_csv,
                root_proc / "master_dataset_sample.csv",
                paths.master_validation_report_csv,
            ],
            "runner": _run_unit1_master,
        },
        STAGE_UNIT3_TEXT_CORPUS: {
            "unit": "unit3",
            "description": "Clean, tokenize, and extract YouTube comment corpus (SCRUM-23)",
            "inputs": [proc / "youtube_comments_clean.csv", paths.active_registry_csv],
            "outputs": [
                paths.text_corpus_csv,
                paths.text_corpus_by_product_csv,
                proc / "text_cleaning_report.csv",
            ],
            "runner": _run_unit3_text_corpus,
        },
        STAGE_UNIT3_TS_PREP: {
            "unit": "unit3",
            "description": "Chronological 80/20 train/test split, ADF/KPSS stationarity & lags (SCRUM-22)",
            "inputs": [paths.master_dataset_csv],
            "outputs": [
                paths.time_series_train_csv,
                paths.time_series_test_csv,
                paths.time_series_split_metadata_json,
                paths.stationarity_report_csv,
                paths.lag_diagnostics_csv,
            ],
            "runner": _run_unit3_ts_prep,
        },
        STAGE_UNIT3_FORECAST: {
            "unit": "unit3",
            "description": "Method 1 time-series forecasting & model benchmarking (SCRUM-26/27)",
            "inputs": [paths.time_series_train_csv, paths.time_series_test_csv],
            "outputs": [
                paths.method1_model_comparison_csv,
                paths.method1_forecast_predictions_csv,
                paths.method1_cohort_performance_csv,
                paths.method1_feature_importance_csv,
            ],
            "runner": _run_unit3_forecast,
        },
        STAGE_UNIT2_METHOD2: {
            "unit": "unit2",
            "description": "Method 2 multimodal dataset isolation & leakage audit (SCRUM-24)",
            "inputs": [paths.master_dataset_csv, paths.text_corpus_csv],
            "outputs": [
                paths.method2_daily_signals_csv,
                paths.method2_train_csv,
                paths.method2_test_csv,
                paths.method2_metadata_json,
                paths.method2_leakage_audit_report_csv,
                paths.method2_lag_diagnostics_csv,
            ],
            "runner": _run_unit2_method2,
        },
        STAGE_UNIT3_TEXT_MINING: {
            "unit": "unit3",
            "description": "Descriptive VADER sentiment, LDA/NMF topics & aspect mining (SCRUM-28)",
            "inputs": [paths.text_corpus_csv, paths.active_registry_csv],
            "outputs": [
                paths.text_sentiment_comments_csv,
                paths.text_sentiment_by_product_csv,
                paths.text_topics_terms_csv,
                paths.text_topics_by_product_csv,
                paths.text_sentiment_spotcheck_csv,
            ],
            "runner": _run_unit3_text_mining,
        },
        STAGE_UNIT3_TEXT_SPOTCHECK: {
            "unit": "unit3",
            "description": "Evaluate VADER sentiment against AI-reviewed spot-check sample (SCRUM-28)",
            "inputs": [paths.text_sentiment_spotcheck_csv, paths.text_sentiment_comments_csv],
            "outputs": [paths.text_sentiment_spotcheck_eval_csv],
            "runner": _run_unit3_text_spotcheck,
        },
        STAGE_UNIT3_TEXT_EVAL: {
            "unit": "unit3",
            "description": "Sentiment benchmarking & lead-lag correlation vs search interest (SCRUM-29)",
            "inputs": [
                proc / "text_sentiment_reference_labels.csv",
                paths.text_sentiment_spotcheck_csv,
                paths.text_corpus_csv,
                paths.text_sentiment_comments_csv,
                paths.master_dataset_csv,
            ],
            "outputs": [
                paths.text_eval_method_comparison_csv,
                paths.text_eval_confusion_csv,
                paths.text_signal_lead_lag_csv,
            ],
            "runner": _run_unit3_text_eval,
        },
    }


# -----------------------------------------------------------------------------
# Cross-Unit Consistency Verification
# -----------------------------------------------------------------------------

def verify_cross_unit_consistency(paths: config.Paths) -> List[CrossUnitCheckResult]:
    """Execute rigorous integrity, schema, and alignment checks across Units 1, 2, and 3."""
    checks: List[CrossUnitCheckResult] = []

    # 1. Master Dataset Canonical Contract (Unit 1)
    master_path = paths.master_dataset_csv
    if not master_path.exists():
        checks.append(CrossUnitCheckResult(
            check_name="master_dataset_exists",
            unit_boundary="Unit 1",
            status="FAIL",
            detail=f"Master dataset missing at {master_path}",
        ))
        return checks

    df_master = pd.read_csv(master_path)
    if len(df_master) == 16140 and len(df_master.columns) == 43:
        checks.append(CrossUnitCheckResult(
            check_name="master_dataset_dimensions",
            unit_boundary="Unit 1",
            status="PASS",
            detail=f"Exact dimensions verified: {len(df_master):,} rows x {len(df_master.columns)} columns",
        ))
    else:
        checks.append(CrossUnitCheckResult(
            check_name="master_dataset_dimensions",
            unit_boundary="Unit 1",
            status="FAIL",
            detail=f"Dimension mismatch: expected (16140, 43), found ({len(df_master)}, {len(df_master.columns)})",
        ))

    pids = df_master["product_id"].nunique()
    if pids == 60:
        checks.append(CrossUnitCheckResult(
            check_name="master_product_continuity",
            unit_boundary="Unit 1",
            status="PASS",
            detail=f"All {pids} products represented across continuous 269-day window",
        ))
    else:
        checks.append(CrossUnitCheckResult(
            check_name="master_product_continuity",
            unit_boundary="Unit 1",
            status="FAIL",
            detail=f"Product count mismatch: expected 60, found {pids}",
        ))

    # 2. Unit 1 <-> Unit 2 Surge Label Consistency
    feat_path = paths.features_encoded_scaled_csv
    if feat_path.exists():
        df_feat = pd.read_csv(feat_path, usecols=["product_id", "date", "surge_label"])
        merged = df_master[["product_id", "date", "surge_label"]].merge(
            df_feat, on=["product_id", "date"], suffixes=("_master", "_feat")
        )
        match_share = (merged["surge_label_master"] == merged["surge_label_feat"]).mean()
        if match_share == 1.0:
            surges = int(float(merged["surge_label_master"].sum()))
            checks.append(CrossUnitCheckResult(
                check_name="unit1_unit2_surge_label_parity",
                unit_boundary="Unit 1 <-> Unit 2",
                status="PASS",
                detail=f"100% agreement on surge labels ({surges} positive surge days across both tables)",
            ))
        else:
            checks.append(CrossUnitCheckResult(
                check_name="unit1_unit2_surge_label_parity",
                unit_boundary="Unit 1 <-> Unit 2",
                status="FAIL",
                detail=f"Surge label disagreement: {(1.0 - match_share)*100:.2f}% mismatch",
            ))
    else:
        checks.append(CrossUnitCheckResult(
            check_name="unit1_unit2_surge_label_parity",
            unit_boundary="Unit 1 <-> Unit 2",
            status="WARN",
            detail=f"features_encoded_scaled.csv absent; skipped parity check",
        ))

    # 3. Unit 1 <-> Unit 3 Method 1 Chronological Partition
    train_m1_path = paths.time_series_train_csv
    test_m1_path = paths.time_series_test_csv
    if train_m1_path.exists() and test_m1_path.exists():
        df_tr1 = pd.read_csv(train_m1_path)
        df_te1 = pd.read_csv(test_m1_path)
        total_m1 = len(df_tr1) + len(df_te1)
        expected_m1 = len(df_master) - (60 * 7)
        if total_m1 == expected_m1:
            checks.append(CrossUnitCheckResult(
                check_name="unit1_unit3_m1_partition_conservation",
                unit_boundary="Unit 1 <-> Unit 3 (Method 1)",
                status="PASS",
                detail=f"Method 1 row count conserved after 7d burn-in: {len(df_tr1):,} train + {len(df_te1):,} test = {total_m1:,}",
            ))
        else:
            checks.append(CrossUnitCheckResult(
                check_name="unit1_unit3_m1_partition_conservation",
                unit_boundary="Unit 1 <-> Unit 3 (Method 1)",
                status="FAIL",
                detail=f"Row count mismatch: train+test={total_m1}, expected={expected_m1}",
            ))
    else:
        checks.append(CrossUnitCheckResult(
            check_name="unit1_unit3_m1_partition_conservation",
            unit_boundary="Unit 1 <-> Unit 3 (Method 1)",
            status="WARN",
            detail="Method 1 train/test splits absent; skipped conservation check",
        ))

    # 4. Unit 1 & Unit 3 <-> Unit 2 Method 2 Multimodal Partition
    train_m2_path = paths.method2_train_csv
    test_m2_path = paths.method2_test_csv
    if train_m2_path.exists() and test_m2_path.exists():
        df_tr2 = pd.read_csv(train_m2_path)
        df_te2 = pd.read_csv(test_m2_path)
        # Expected rows: 16140 minus 420 burn-in rows = 15720
        total_m2 = len(df_tr2) + len(df_te2)
        expected_m2 = len(df_master) - (60 * config.METHOD2_BURN_IN_DAYS)
        if total_m2 == expected_m2:
            checks.append(CrossUnitCheckResult(
                check_name="unit2_m2_burn_in_conservation",
                unit_boundary="Unit 1 <-> Unit 2 (Method 2)",
                status="PASS",
                detail=f"Method 2 row count conserved after 7d burn-in: {len(df_tr2):,} train + {len(df_te2):,} test = {total_m2:,}",
            ))
        else:
            checks.append(CrossUnitCheckResult(
                check_name="unit2_m2_burn_in_conservation",
                unit_boundary="Unit 1 <-> Unit 2 (Method 2)",
                status="FAIL",
                detail=f"Method 2 row count mismatch: found {total_m2}, expected {expected_m2}",
            ))
    else:
        checks.append(CrossUnitCheckResult(
            check_name="unit2_m2_burn_in_conservation",
            unit_boundary="Unit 1 <-> Unit 2 (Method 2)",
            status="WARN",
            detail="Method 2 train/test splits absent; skipped check",
        ))

    # 5. Unit 3 Text Mining Signals Lead-Lag
    lead_lag_path = paths.text_signal_lead_lag_csv
    if lead_lag_path.exists():
        df_ll = pd.read_csv(lead_lag_path)
        if len(df_ll) > 0 and "spearman_rho" in df_ll.columns:
            checks.append(CrossUnitCheckResult(
                check_name="unit3_lead_lag_signal_coupling",
                unit_boundary="Unit 3 Text Mining",
                status="PASS",
                detail=f"Lead-lag correlation matrix computed across {len(df_ll)} lag configurations",
            ))
        else:
            checks.append(CrossUnitCheckResult(
                check_name="unit3_lead_lag_signal_coupling",
                unit_boundary="Unit 3 Text Mining",
                status="FAIL",
                detail="lead_lag table empty or missing columns",
            ))
    else:
        checks.append(CrossUnitCheckResult(
            check_name="unit3_lead_lag_signal_coupling",
            unit_boundary="Unit 3 Text Mining",
            status="WARN",
            detail="text_signal_lead_lag.csv absent; skipped check",
        ))

    return checks


# -----------------------------------------------------------------------------
# Main Orchestration Engine
# -----------------------------------------------------------------------------

def run_full_pipeline(
    paths: Optional[config.Paths] = None,
    units: Optional[List[str]] = None,
    stages: Optional[List[str]] = None,
    smoke_test: bool = False,
    no_plots: bool = False,
    dry_run: bool = False,
    skip_prep_if_exists: bool = False,
    continue_on_error: bool = False,
    quiet: bool = False,
) -> FullPipelineResult:
    """Execute the consolidated workflow for Units 1, 2, and 3 in verified dependency order."""
    t_start = time.time()
    if paths is None:
        paths = config.PATHS

    stage_defs = get_stage_definitions(paths)

    # Determine stages to run
    if stages:
        invalid = [s for s in stages if s not in ALL_STAGES]
        if invalid:
            raise ValueError(f"Unknown stage(s): {', '.join(invalid)}. Available stages: {ALL_STAGES}")
        selected_stages = [s for s in ALL_STAGES if s in stages]
    elif units:
        # Resolve units to stage list
        selected_set: Set[str] = set()
        for u in units:
            u_clean = u.lower().strip()
            if u_clean in UNIT_STAGE_MAP:
                selected_set.update(UNIT_STAGE_MAP[u_clean])
            elif u_clean in ("all", "full"):
                selected_set.update(ALL_STAGES)
            else:
                raise ValueError(f"Unknown unit: '{u}'. Valid units: unit1, unit2, unit3, all")
        # Keep execution in canonical DAG order
        selected_stages = [s for s in ALL_STAGES if s in selected_set]
    else:
        selected_stages = list(ALL_STAGES)

    if not quiet:
        print("=" * 80)
        print("  VIRAL PRODUCT INTELLIGENCE — UNIT 1, 2, 3 CONSOLIDATED PIPELINE (SCRUM-30)")
        print("=" * 80)
        mode_flags = []
        if dry_run:
            mode_flags.append("DRY-RUN")
        if smoke_test:
            mode_flags.append("SMOKE-TEST")
        if no_plots or smoke_test:
            mode_flags.append("NO-PLOTS")
        flags_str = f" [{', '.join(mode_flags)}]" if mode_flags else ""
        print(f"Execution Target: {len(selected_stages)} stages{flags_str}")
        print(f"Workspace Root:   {paths.root}")
        print("-" * 80)

    stage_results: Dict[str, StageResult] = {}
    pipeline_success = True

    # Check for fast skipping of prep if requested and outputs exist
    if skip_prep_if_exists and STAGE_TASK1_PREP in selected_stages:
        prep_outputs = stage_defs[STAGE_TASK1_PREP]["outputs"]
        if all(p.exists() and p.stat().st_size > 0 for p in prep_outputs):
            if not quiet:
                print(f"-> [Stage: {STAGE_TASK1_PREP:<23}] SKIPPED (outputs already present)")
            stage_results[STAGE_TASK1_PREP] = StageResult(
                name=STAGE_TASK1_PREP,
                unit="unit1",
                status="SKIPPED",
                details="Pre-existing clean extracts found",
                outputs_produced=[str(p) for p in prep_outputs],
            )
            selected_stages.remove(STAGE_TASK1_PREP)

    for stage_name in selected_stages:
        spec = stage_defs[stage_name]
        unit = spec["unit"]
        inputs = spec["inputs"]
        expected_outputs = spec["outputs"]

        if not quiet:
            print(f"-> [{unit.upper():<6}] [Stage: {stage_name:<23}] Starting...")

        # 1. Pre-execution Contract Check (Inputs)
        missing_inputs = [p for p in inputs if not p.exists() or p.stat().st_size == 0]
        if missing_inputs:
            err_msg = f"Missing required input(s): {', '.join(str(p.relative_to(paths.root) if str(p).startswith(str(paths.root)) else p.name) for p in missing_inputs)}"
            if not quiet:
                print(f"   [BLOCKED] {err_msg}")
            stage_results[stage_name] = StageResult(
                name=stage_name,
                unit=unit,
                status="BLOCKED",
                duration_seconds=0.0,
                error_message=err_msg,
                inputs_checked=[str(p) for p in inputs],
            )
            pipeline_success = False
            if not continue_on_error:
                if not quiet:
                    print(f"\nExecution halted due to blocked stage: {stage_name}")
                break
            continue

        # 2. Dry-run Mode
        if dry_run:
            stage_results[stage_name] = StageResult(
                name=stage_name,
                unit=unit,
                status="DRY_RUN",
                duration_seconds=0.0,
                details=f"Dry run validated: {len(inputs)} input contracts verified",
                inputs_checked=[str(p) for p in inputs],
                outputs_produced=[str(p) for p in expected_outputs],
            )
            if not quiet:
                print(f"   [DRY_RUN] Verified input contracts")
            continue

        # 3. Execution
        t0 = time.time()
        try:
            rc, details = spec["runner"](
                paths=paths,
                quiet=quiet,
                no_plots=no_plots or smoke_test,
                smoke_test=smoke_test,
            )
            elapsed = time.time() - t0

            if rc != 0:
                pipeline_success = False
                stage_results[stage_name] = StageResult(
                    name=stage_name,
                    unit=unit,
                    status="FAILED",
                    duration_seconds=elapsed,
                    error_message=details,
                    inputs_checked=[str(p) for p in inputs],
                )
                if not quiet:
                    print(f"   [FAILED] ({elapsed:.2f}s): {details}")
                if not continue_on_error:
                    break
                continue

            # 4. Post-execution Output Verification
            missing_outputs = [p for p in expected_outputs if not p.exists() or p.stat().st_size == 0]
            if missing_outputs:
                err_msg = f"Stage claimed success but missing output: {', '.join(str(p.name) for p in missing_outputs)}"
                pipeline_success = False
                stage_results[stage_name] = StageResult(
                    name=stage_name,
                    unit=unit,
                    status="FAILED",
                    duration_seconds=elapsed,
                    error_message=err_msg,
                    inputs_checked=[str(p) for p in inputs],
                )
                if not quiet:
                    print(f"   [FAILED] ({elapsed:.2f}s): {err_msg}")
                if not continue_on_error:
                    break
                continue

            # Success
            stage_results[stage_name] = StageResult(
                name=stage_name,
                unit=unit,
                status="SUCCESS",
                duration_seconds=elapsed,
                details=details,
                inputs_checked=[str(p) for p in inputs],
                outputs_produced=[str(p) for p in expected_outputs],
            )
            if not quiet:
                print(f"   [SUCCESS] ({elapsed:.2f}s): {details}")

        except Exception as exc:
            elapsed = time.time() - t0
            pipeline_success = False
            err_msg = f"Unexpected exception: {type(exc).__name__}: {str(exc)}"
            stage_results[stage_name] = StageResult(
                name=stage_name,
                unit=unit,
                status="FAILED",
                duration_seconds=elapsed,
                error_message=err_msg,
                inputs_checked=[str(p) for p in inputs],
            )
            if not quiet:
                print(f"   [EXCEPTION] ({elapsed:.2f}s): {err_msg}")
            if not continue_on_error:
                break

    total_duration = time.time() - t_start

    # Run Cross-Unit Integrity Checks
    cross_unit_results = verify_cross_unit_consistency(paths)
    if any(c.status == "FAIL" for c in cross_unit_results) and not dry_run:
        pipeline_success = False

    # Summary Statistics
    status_counts: Dict[str, int] = {}
    for r in stage_results.values():
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    summary_info = {
        "pipeline_success": pipeline_success,
        "total_duration_seconds": round(total_duration, 2),
        "stages_executed": len(stage_results),
        "stages_success": status_counts.get("SUCCESS", 0),
        "stages_failed": status_counts.get("FAILED", 0),
        "stages_blocked": status_counts.get("BLOCKED", 0),
        "stages_skipped": status_counts.get("SKIPPED", 0),
        "stages_dry_run": status_counts.get("DRY_RUN", 0),
        "cross_unit_passes": sum(1 for c in cross_unit_results if c.status == "PASS"),
        "cross_unit_fails": sum(1 for c in cross_unit_results if c.status == "FAIL"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }

    result = FullPipelineResult(
        success=pipeline_success,
        stages=stage_results,
        total_duration_seconds=total_duration,
        summary=summary_info,
        cross_unit_checks=cross_unit_results,
    )

    # Save summary report if not dry-run
    if not dry_run and len(stage_results) > 0:
        save_execution_summary(result, paths)

    if not quiet:
        print_summary_report(result)

    return result


# -----------------------------------------------------------------------------
# Summary Reporting and Persistence
# -----------------------------------------------------------------------------

def save_execution_summary(result: FullPipelineResult, paths: config.Paths) -> None:
    """Save execution summary to processed/pipeline_execution_summary.csv & .json."""
    out_dir = paths.root / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    # CSV summary table of stages
    records = []
    for s in result.stages.values():
        records.append({
            "stage_name": s.name,
            "unit": s.unit,
            "status": s.status,
            "duration_seconds": round(s.duration_seconds, 3),
            "details": s.details,
            "error_message": s.error_message or "",
            "outputs_count": len(s.outputs_produced),
        })
    df_summary = pd.DataFrame(records)
    csv_path = paths.pipeline_execution_summary_csv
    df_summary.to_csv(csv_path, index=False, lineterminator="\n")

    # JSON comprehensive report
    json_path = paths.pipeline_execution_summary_json
    payload = {
        "summary": result.summary,
        "stages": [asdict(s) for s in result.stages.values()],
        "cross_unit_checks": [asdict(c) for c in result.cross_unit_checks],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def print_summary_report(result: FullPipelineResult) -> None:
    """Print beautifully formatted console summary table."""
    print("\n" + "=" * 80)
    print("  CONSOLIDATED PIPELINE EXECUTION SUMMARY")
    print("=" * 80)
    print(f"Overall Result:      {'PASS (100% SUCCESS)' if result.success else 'FAILED'}")
    print(f"Total Wall Time:     {result.total_duration_seconds:.2f} seconds")
    print(f"Stages Executed:     {len(result.stages)}")
    print(f"  - SUCCESS:         {result.summary.get('stages_success', 0)}")
    print(f"  - FAILED:          {result.summary.get('stages_failed', 0)}")
    print(f"  - BLOCKED:         {result.summary.get('stages_blocked', 0)}")
    print(f"  - SKIPPED:         {result.summary.get('stages_skipped', 0)}")
    print(f"  - DRY_RUN:         {result.summary.get('stages_dry_run', 0)}")
    print("-" * 80)
    print(f"{'Unit':<7} | {'Stage':<25} | {'Status':<8} | {'Time (s)':<8} | {'Details'}")
    print("-" * 80)
    for s in result.stages.values():
        status_disp = f"[{s.status}]"
        print(f"{s.unit.upper():<7} | {s.name:<25} | {status_disp:<8} | {s.duration_seconds:>7.2f}s | {s.details or s.error_message or ''}")
    print("-" * 80)

    if result.cross_unit_checks:
        print("\nCross-Unit Integrity Verification:")
        for c in result.cross_unit_checks:
            print(f"  [{c.status}] {c.unit_boundary:<24} | {c.check_name:<30} : {c.detail}")
    print("=" * 80)


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SCRUM-30: Unit 1, 2, 3 Consolidated Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--units",
        nargs="+",
        help="Specify units to run (e.g. unit1, unit2, unit3, all).",
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        help="Specify exact stage names to run in dependency order.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Fast smoke-test validation (~15-20s, skips plots, minimal topic iterations).",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip generating all matplotlib/seaborn visualization figures.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check stage configuration and input contracts without running actual computation.",
    )
    parser.add_argument(
        "--skip-prep",
        action="store_true",
        help="Skip task1_prep stage if clean extracts already exist.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue subsequent non-dependent stages if a stage fails.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose progress messages.",
    )

    args = parser.parse_args(argv)

    res = run_full_pipeline(
        units=args.units,
        stages=args.stages,
        smoke_test=args.smoke_test,
        no_plots=args.no_plots,
        dry_run=args.dry_run,
        skip_prep_if_exists=args.skip_prep,
        continue_on_error=args.continue_on_error,
        quiet=args.quiet,
    )

    return 0 if res.success else 1


if __name__ == "__main__":
    sys.exit(main())
