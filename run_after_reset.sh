#!/usr/bin/env bash
# run_after_reset.sh: refresh YouTube recent-pass data and rebuild every Task 1-3 output.
#
# Run AFTER the YouTube quota resets (midnight Pacific = 12:30 IST in October), from the repo root.
# On macOS keep the machine awake for the whole run:
#     caffeinate -i ./run_after_reset.sh
# (Linux: run it in tmux/screen so a closed terminal does not stop it.)
#
# Steps run in order and the script stops at the first failure. A summary is always printed.
#   b. YouTube recent pass for all products (plan first, no API calls; then about 60 search calls)
#   c. clean YouTube tables        d. rebuild the aligned table     e. full 60-product report
#   f. execute the EDA notebook    g. rebuild the handoff zip       h. local backup   i. tests
# It never commits or pushes, never touches raw/ except adding new collector files, and never
# prints the API key (it stays in .env).

set -uo pipefail
cd "$(dirname "$0")"

STEPS=()
STATUSES=()
TIMES=()
START_ALL=$(date +%s)

summary() {
  local total=$(( $(date +%s) - START_ALL ))
  echo
  echo "================ SUMMARY ================"
  for i in "${!STEPS[@]}"; do
    printf "  %-48s %-8s %5ss\n" "${STEPS[$i]}" "${STATUSES[$i]}" "${TIMES[$i]}"
  done
  echo "  total: ${total}s"
  if [[ -f data/processed/full_report.csv ]]; then
    python3 - <<'EOF'
import pandas as pd
r = pd.read_csv("data/processed/full_report.csv")
a = pd.read_csv("data/processed/aligned_daily.csv")
print(f"  aligned_daily.csv: {len(a):,} rows, {a['product_id'].nunique()} products, "
      f"YouTube observed on {int(a['youtube_observed'].sum())} product-days, "
      f"recent pass on {int(a['youtube_recent_observed'].sum())}")
print(f"  full_report.csv: {len(r)} products, usable {int(r['usable'].sum())}, "
      f"low-signal Trends {int((r['low_signal_product'] == True).sum())}, "
      f"low on-target {int((r['low_on_target_share'] == True).sum())}, "
      f"with recent-pass count {int(r['videos_published_7d'].notna().sum())}")
EOF
  fi
  [[ -f data/handoff/task1_handoff.zip ]] && echo "  handoff: data/handoff/task1_handoff.zip"
  latest_backup=$(ls -t backups/local_data_*.tar.gz 2>/dev/null | head -1)
  [[ -n "$latest_backup" ]] && echo "  latest backup: $latest_backup"
  echo "  Nothing was committed or pushed."
  echo "========================================="
}

step() {
  local name="$1"; shift
  echo
  echo "---- ${name} ----"
  local t0=$(date +%s)
  "$@"
  local rc=$?
  STEPS+=("$name")
  TIMES+=("$(( $(date +%s) - t0 ))")
  if [[ $rc -ne 0 ]]; then
    STATUSES+=("FAILED($rc)")
    echo "!! ${name} failed with exit code ${rc}; stopping."
    summary
    exit "$rc"
  fi
  STATUSES+=("ok")
}

plan_recent_pass() {
  # Dry run only: no API calls. Refuse to start if today's remaining quota cannot cover the pass,
  # e.g. because the Pacific quota day has not reset yet.
  local out
  out=$(python3 src/collect_youtube.py --pass recent --all --dry-run) || return $?
  echo "$out" | tail -5
  if grep -q "DOES NOT FIT" <<<"$out"; then
    echo "Quota for the current Pacific day cannot cover the recent pass yet."
    echo "Wait until after the reset (midnight Pacific = 12:30 IST) and run this script again."
    return 2
  fi
}

step "b0. YouTube recent pass: plan (no API calls)" plan_recent_pass
step "b. YouTube recent pass, all products"         python3 src/collect_youtube.py --pass recent --all
step "c. clean YouTube tables"                      python3 src/clean_youtube.py
step "d. rebuild aligned_daily.csv"                 python3 src/build_aligned.py
step "e. full 60-product report"                    python3 src/full_report.py
step "f. execute notebooks/04_eda_full.ipynb"       jupyter nbconvert --to notebook --execute --inplace \
                                                      --ExecutePreprocessor.timeout=900 notebooks/04_eda_full.ipynb
step "g. rebuild handoff zip"                       python3 src/build_handoff.py
step "h. local backup"                              python3 src/backup_local_data.py
step "i. tests"                                     python3 -m pytest -q

summary
