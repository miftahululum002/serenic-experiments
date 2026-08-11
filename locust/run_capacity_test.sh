#!/usr/bin/env bash
# Jalankan test kapasitas worker data parsing (khusus update encounter):
#   1. monitor queue di background
#   2. locust ramp-up bertahap
#   3. analisis throughput + deteksi jenuh
#
# Config lewat env (override opsional):
#   USERS=200 RUN_TIME=20m SPAWN_RATE=10 INTERVAL=5 OUTPUT_DIR=results
set -euo pipefail

cd "$(dirname "$0")"

USERS="${USERS:-200}"
RUN_TIME="${RUN_TIME:-20m}"
SPAWN_RATE="${SPAWN_RATE:-10}"
INTERVAL="${INTERVAL:-5}"
OUTPUT_DIR="${OUTPUT_DIR:-results}"
STAMP="$(date +%Y%m%d_%H%M%S)"
MONITOR_CSV="$OUTPUT_DIR/queue_monitor_${STAMP}.csv"
LOCUST_PREFIX="$OUTPUT_DIR/locust_${STAMP}"

mkdir -p "$OUTPUT_DIR"

echo "==> [1/3] Mulai monitor queue -> $MONITOR_CSV"
source .venv/bin/activate
python monitor_queue.py --interval "$INTERVAL" --output "$MONITOR_CSV" &
MONITOR_PID=$!
trap 'kill $MONITOR_PID 2>/dev/null || true' EXIT

sleep 3

echo "==> [2/3] Jalankan locust (users=$USERS, spawn=$SPAWN_RATE, run=$RUN_TIME)"
locust -f locustfile_update.py \
  -u "$USERS" --spawn-rate "$SPAWN_RATE" \
  --run-time "$RUN_TIME" \
  --csv "$LOCUST_PREFIX" --csv-full-history \
  --headless

kill $MONITOR_PID 2>/dev/null || true
wait $MONITOR_PID 2>/dev/null || true

echo "==> [3/3] Analisis kapasitas (backlog + RPS + latency)"
python analyze_capacity_full.py --monitor "$MONITOR_CSV" \
  --locust "${LOCUST_PREFIX}_stats_history.csv" \
  --output "$OUTPUT_DIR/kapasitas_${STAMP}.txt"

echo
echo "Hasil:"
echo "  monitor : $MONITOR_CSV"
echo "  locust  : ${LOCUST_PREFIX}_stats_history.csv"
echo "  analisis: $OUTPUT_DIR/kapasitas_${STAMP}.txt"
