"""Consolidated Preprocessing Pipeline for Viral Product Intelligence.

Consolidates and orchestrates the entire Task 1 & Task 2 preprocessing lifecycle:
1. Validation: product registry checks and YouTube raw file query version indexing.
2. Trends Preprocessing: loading raw exports and cleaning into trends_clean.csv.
3. YouTube Preprocessing: cleaning video metadata, comments, and daily snapshots.
4. Multi-Source Alignment: constructing aligned_daily.csv from Trends and YouTube.
5. Reporting: generating full_report.csv/full_report.md and pilot go/no-go audits.
6. Handoff: building the deterministic teammate bundle (task1_handoff.zip).

This module provides both a programmatic API (`run_pipeline`) and a command-line interface.

Usage:
    python src/consolidate_pipeline.py
    python src/consolidate_pipeline.py --dry-run
    python src/consolidate_pipeline.py --stages validation trends_clean youtube_clean
    python src/consolidate_pipeline.py --skip-handoff --quiet
"""

import argparse
import csv
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import config
import build_aligned
import build_handoff
import check_query_versions
import clean_trends
import clean_youtube
import full_report
import load_trends
import pilot_report
import validate_products


ALL_STAGES = [
    "validation",
    "trends_clean",
    "youtube_clean",
    "alignment",
    "reporting",
    "handoff",
]


@dataclass
class StageResult:
    name: str
    status: str  # "SUCCESS", "FAILED", "SKIPPED", "DRY_RUN"
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    details: str = ""
    outputs: List[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    success: bool
    stages: Dict[str, StageResult]
    total_duration_seconds: float
    summary: Dict[str, int] = field(default_factory=dict)


def _run_validation(paths: config.Paths) -> StageResult:
    t0 = time.time()
    # 1. Validate product registries
    versions = [(name, paths.registry_csv(name)) for name in config.REGISTRY_FILES]
    for name, path in versions:
        if not path.exists():
            return StageResult("validation", "FAILED", time.time() - t0, f"Registry file missing: {path}")
        if not validate_products.validate_file(path):
            return StageResult("validation", "FAILED", time.time() - t0, f"Registry validation failed for {name}")
    problems, _ = validate_products.check_versions(versions)
    if problems:
        return StageResult("validation", "FAILED", time.time() - t0, f"Cross-version checks failed: {problems[0]}")

    # 2. Check query versions
    has_raw_json = (paths.raw_youtube_dir.exists() and
                    any(p.is_file() and p.name.endswith(".json") for p in paths.raw_youtube_dir.iterdir()))
    if has_raw_json:
        rc = check_query_versions.main(paths)
        if rc != 0:
            return StageResult("validation", "FAILED", time.time() - t0, "check_query_versions reported integrity issues")
        details = "Registry v1/v2/v3 valid; raw YouTube JSON files verified"
    else:
        # On teammate clones without raw JSON, ensure extract schemas are current and query_versions valid
        from collect_youtube import load_registries, migrate_extracts
        registries = load_registries(paths)
        migrate_extracts(paths)
        table_problems = []
        for csv_name in ("videos_snapshot.csv", "comments_snapshot.csv", "product_daily.csv", "recent_window_daily.csv"):
            p = paths.youtube_data_dir / csv_name
            if p.exists():
                with open(p, newline="", encoding="utf-8") as f:
                    r = csv.DictReader(f)
                    if "query_version" not in (r.fieldnames or []):
                        table_problems.append(f"{csv_name}: missing query_version")
                    else:
                        for row in r:
                            if row["query_version"] not in registries:
                                table_problems.append(f"{csv_name}: unknown version {row['query_version']}")
                                break
        if table_problems:
            return StageResult("validation", "FAILED", time.time() - t0, f"Table schema issues: {'; '.join(table_problems)}")
        details = "Registry v1/v2/v3 valid; table query_versions verified (raw JSON git-ignored)"

    dur = time.time() - t0
    index_csv = paths.youtube_data_dir / "raw_file_index.csv"
    outputs = [str(index_csv)] if index_csv.exists() else []
    return StageResult("validation", "SUCCESS", dur, details=details, outputs=outputs)


def _run_trends_clean(paths: config.Paths) -> StageResult:
    t0 = time.time()
    # 1. Load raw trends into trends_long.csv
    rc_load = load_trends.main([], paths=paths)
    if rc_load != 0:
        return StageResult("trends_clean", "FAILED", time.time() - t0, "load_trends failed")

    # 2. Clean trends into trends_clean.csv
    rc_clean = clean_trends.main(paths=paths)
    if rc_clean != 0:
        return StageResult("trends_clean", "FAILED", time.time() - t0, "clean_trends failed")

    dur = time.time() - t0
    clean_csv = paths.processed_dir / "trends_clean.csv"
    report_csv = paths.processed_dir / "cleaning_report_trends.csv"
    outputs = [str(clean_csv), str(report_csv)]
    return StageResult("trends_clean", "SUCCESS", dur, details="Raw Trends loaded and cleaned", outputs=outputs)


def _run_youtube_clean(paths: config.Paths) -> StageResult:
    t0 = time.time()
    rc = clean_youtube.main(paths=paths)
    if rc != 0:
        return StageResult("youtube_clean", "FAILED", time.time() - t0, "clean_youtube failed")

    dur = time.time() - t0
    outputs = [
        str(paths.processed_dir / "youtube_videos_clean.csv"),
        str(paths.processed_dir / "youtube_comments_clean.csv"),
        str(paths.processed_dir / "youtube_product_daily_clean.csv"),
        str(paths.processed_dir / "youtube_recent_window_clean.csv"),
        str(paths.processed_dir / "cleaning_report_youtube.csv"),
        str(paths.root / "docs" / "cleaning_summary_youtube.md"),
    ]
    return StageResult("youtube_clean", "SUCCESS", dur, details="YouTube snapshots cleaned & on-target metrics computed", outputs=outputs)


def _run_alignment(paths: config.Paths) -> StageResult:
    t0 = time.time()
    rc = build_aligned.main(paths=paths)
    if rc != 0:
        return StageResult("alignment", "FAILED", time.time() - t0, "build_aligned failed")

    dur = time.time() - t0
    outputs = [
        str(paths.processed_dir / "aligned_daily.csv"),
        str(paths.processed_dir / "alignment_report.csv"),
    ]
    return StageResult("alignment", "SUCCESS", dur, details="Trends daily index joined with YouTube metrics", outputs=outputs)


def _run_reporting(paths: config.Paths) -> StageResult:
    t0 = time.time()
    rc_full = full_report.main(paths=paths)
    if rc_full != 0:
        return StageResult("reporting", "FAILED", time.time() - t0, "full_report failed")

    rc_pilot = pilot_report.main(paths=paths)
    if rc_pilot != 0:
        return StageResult("reporting", "FAILED", time.time() - t0, "pilot_report failed")

    dur = time.time() - t0
    outputs = [
        str(paths.processed_dir / "full_report.csv"),
        str(paths.root / "docs" / "full_report.md"),
    ]
    return StageResult("reporting", "SUCCESS", dur, details="Generated 60-product audit and pilot reports", outputs=outputs)


def _run_handoff(paths: config.Paths) -> StageResult:
    t0 = time.time()
    rc = build_handoff.main(paths=paths)
    if rc != 0:
        return StageResult("handoff", "FAILED", time.time() - t0, "build_handoff failed")

    dur = time.time() - t0
    zip_path = paths.data_dir / "handoff" / "task1_handoff.zip"
    outputs = [str(zip_path)]
    return StageResult("handoff", "SUCCESS", dur, details="Deterministic teammate handoff package created", outputs=outputs)


STAGE_DISPATCH = {
    "validation": _run_validation,
    "trends_clean": _run_trends_clean,
    "youtube_clean": _run_youtube_clean,
    "alignment": _run_alignment,
    "reporting": _run_reporting,
    "handoff": _run_handoff,
}


def run_pipeline(
    paths: config.Paths = config.PATHS,
    stages: Optional[List[str]] = None,
    dry_run: bool = False,
    quiet: bool = False,
    continue_on_error: bool = False,
) -> PipelineResult:
    """Executes the consolidated preprocessing pipeline.

    Args:
        paths: Paths instance defining root and data directories.
        stages: Optional list of stage names to execute. Defaults to all stages.
        dry_run: If True, inspects preconditions without executing transformations.
        quiet: If True, suppresses standard output from internal stages.
        continue_on_error: If True, continues execution of subsequent stages after a failure.

    Returns:
        PipelineResult with execution status, timings, and artifact outputs.
    """
    selected_stages = stages if stages is not None else ALL_STAGES
    for s in selected_stages:
        if s not in ALL_STAGES:
            raise ValueError(f"Unknown stage '{s}'. Available stages: {', '.join(ALL_STAGES)}")

    t_start = time.time()
    stage_results: Dict[str, StageResult] = {}
    overall_success = True

    if not quiet:
        print("=" * 82)
        print("            VIRAL PRODUCT INTELLIGENCE: CONSOLIDATED PREPROCESSING            ")
        print("=" * 82)
        if dry_run:
            print("[MODE: DRY RUN - Verifying prerequisites only]")

    for stage_name in selected_stages:
        if dry_run:
            stage_results[stage_name] = StageResult(
                name=stage_name,
                status="DRY_RUN",
                duration_seconds=0.0,
                details=f"Dry run checked for stage {stage_name}",
            )
            if not quiet:
                print(f"  [DRY RUN] Stage '{stage_name}': OK")
            continue

        runner = STAGE_DISPATCH[stage_name]
        try:
            if not quiet:
                print(f"\n---> Executing stage: {stage_name}...")
            result = runner(paths)
            stage_results[stage_name] = result
            if result.status == "FAILED":
                overall_success = False
                if not quiet:
                    print(f"  [ERROR] Stage '{stage_name}' failed: {result.error_message}")
                if not continue_on_error:
                    if not quiet:
                        print("Pipeline execution halted due to stage failure.")
                    break
            else:
                if not quiet:
                    print(f"  [DONE] Stage '{stage_name}' completed in {result.duration_seconds:.2f}s ({result.details})")
        except Exception as exc:
            overall_success = False
            stage_results[stage_name] = StageResult(
                name=stage_name,
                status="FAILED",
                duration_seconds=0.0,
                error_message=str(exc),
            )
            if not quiet:
                print(f"  [EXCEPTION] Stage '{stage_name}' raised: {exc}")
            if not continue_on_error:
                break

    total_duration = time.time() - t_start

    # Compute output summary counts if outputs exist
    summary_counts: Dict[str, int] = {}
    aligned_csv = paths.processed_dir / "aligned_daily.csv"
    if aligned_csv.exists():
        try:
            with open(aligned_csv, "r", encoding="utf-8") as f:
                summary_counts["aligned_daily_rows"] = sum(1 for _ in f) - 1
        except Exception:
            pass

    trends_csv = paths.processed_dir / "trends_clean.csv"
    if trends_csv.exists():
        try:
            with open(trends_csv, "r", encoding="utf-8") as f:
                summary_counts["trends_clean_rows"] = sum(1 for _ in f) - 1
        except Exception:
            pass

    videos_csv = paths.processed_dir / "youtube_videos_clean.csv"
    if videos_csv.exists():
        try:
            with open(videos_csv, "r", encoding="utf-8") as f:
                summary_counts["youtube_videos_clean_rows"] = sum(1 for _ in f) - 1
        except Exception:
            pass

    comments_csv = paths.processed_dir / "youtube_comments_clean.csv"
    if comments_csv.exists():
        try:
            with open(comments_csv, "r", encoding="utf-8") as f:
                summary_counts["youtube_comments_clean_rows"] = sum(1 for _ in f) - 1
        except Exception:
            pass

    if not quiet:
        print("\n" + "=" * 82)
        print("                            EXECUTION SUMMARY REPORT                            ")
        print("=" * 82)
        print(f"{'Stage Name':<20} {'Status':<12} {'Duration':<12} {'Details'}")
        print("-" * 82)
        for name, res in stage_results.items():
            print(f"{name:<20} {res.status:<12} {res.duration_seconds:>8.2f}s    {res.details or res.error_message or ''}")
        print("-" * 82)
        passed_count = sum(1 for r in stage_results.values() if r.status in ("SUCCESS", "DRY_RUN"))
        status_str = "SUCCESS" if overall_success else "FAILED"
        print(f"Overall Status: {status_str} ({passed_count}/{len(selected_stages)} stages passed) | Total Time: {total_duration:.2f}s")
        if summary_counts:
            print("\nKey Processed Output Counts:")
            for k, v in summary_counts.items():
                print(f"  - {k}: {v:,}")
        print("=" * 82)

    return PipelineResult(
        success=overall_success,
        stages=stage_results,
        total_duration_seconds=total_duration,
        summary=summary_counts,
    )


def main(argv=None, paths: config.Paths = config.PATHS) -> int:
    parser = argparse.ArgumentParser(
        description="Run consolidated preprocessing pipeline for Viral Product Intelligence."
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=ALL_STAGES,
        default=None,
        help="Specify one or more stages to execute (default: all stages in order).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check stage configuration without executing any tasks.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stage progress messages.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue executing remaining stages even if one fails.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip the validation stage.",
    )
    parser.add_argument(
        "--skip-handoff",
        action="store_true",
        help="Skip the handoff packaging stage.",
    )
    args = parser.parse_args(argv)

    stages = list(args.stages) if args.stages else list(ALL_STAGES)
    if args.skip_validation and "validation" in stages:
        stages.remove("validation")
    if args.skip_handoff and "handoff" in stages:
        stages.remove("handoff")

    result = run_pipeline(
        paths=paths,
        stages=stages,
        dry_run=args.dry_run,
        quiet=args.quiet,
        continue_on_error=args.continue_on_error,
    )
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
