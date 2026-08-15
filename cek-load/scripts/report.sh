#!/usr/bin/env bash
# Jalankan report.py (satu queue) di latar belakang selama 3 jam.
set -euo pipefail

cd "$(dirname "$0")/.."

STAMP=$(date +%Y%m%d_%H%M%S)
REPORT="reports/LAPORAN_${STAMP}.md"
LOG="logs/report_${STAMP}.log"

mkdir -p reports logs

nohup ./.venv/bin/python -u report.py \
    --minutes 180 \
    --output "$REPORT" \
    "$@" > "$LOG" 2>&1 &

echo "PID     : $!"
echo "Log     : $LOG"
echo "Laporan : $REPORT (ditulis setelah sampling selesai)"
echo "Pantau  : tail -f $LOG"
