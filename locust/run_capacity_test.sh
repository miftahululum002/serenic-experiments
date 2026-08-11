#!/usr/bin/env bash
# Jalankan test kapasitas worker data parsing (khusus update encounter):
#   1. monitor queue di background (dengan auto-stop saat jenuh)
#   2. locust ramp-up bertahap; dihentikan otomatis begitu jenuh terdeteksi
#   3. analisis throughput + deteksi jenuh + RPS/latency
#
# Config lewat env (override opsional):
#   USERS=200 RUN_TIME=20m SPAWN_RATE=10 INTERVAL=5 OUTPUT_DIR=results
#   STOP_ON_SATURATED=1 (default) 0 utk selalu jalan sampai RUN_TIME habis
#   WINDOW=3 MIN_SATURATED_SAMPLES=4
set -euo pipefail

cd "$(dirname "$0")"

USERS="${USERS:-200}"
RUN_TIME="${RUN_TIME:-20m}"
SPAWN_RATE="${SPAWN_RATE:-10}"
INTERVAL="${INTERVAL:-5}"
OUTPUT_DIR="${OUTPUT_DIR:-results}"
STOP_ON_SATURATED="${STOP_ON_SATURATED:-1}"
WINDOW="${WINDOW:-3}"
MIN_SATURATED_SAMPLES="${MIN_SATURATED_SAMPLES:-4}"
STAMP="$(date +%Y%m%d_%H%M%S)"
MONITOR_CSV="$OUTPUT_DIR/queue_monitor_${STAMP}.csv"
LOCUST_PREFIX="$OUTPUT_DIR/locust_${STAMP}"

mkdir -p "$OUTPUT_DIR"

source .venv/bin/activate

echo "==> [1/3] Mulai monitor queue -> $MONITOR_CSV"
STOP_ARGS=""
if [ "$STOP_ON_SATURATED" = "1" ]; then
    STOP_ARGS="--stop-on-saturated"
    echo "    auto-stop aktif: test berhenti saat jenuh terdeteksi"
fi
python monitor_queue_capacity.py \
  --interval "$INTERVAL" --output "$MONITOR_CSV" \
  --window "$WINDOW" --min-saturated-samples "$MIN_SATURATED_SAMPLES" \
  $STOP_ARGS &
MONITOR_PID=$!

cleanup() {
    kill $MONITOR_PID 2>/dev/null || true
    wait $MONITOR_PID 2>/dev/null || true
}
trap cleanup EXIT

sleep 3

echo "==> [2/3] Jalankan locust (users=$USERS, spawn=$SPAWN_RATE, run=$RUN_TIME)"
locust -f locustfile_update.py \
  -u "$USERS" --spawn-rate "$SPAWN_RATE" \
  --run-time "$RUN_TIME" \
  --csv "$LOCUST_PREFIX" --csv-full-history \
  --headless &
LOCUST_PID=$!

# Tunggu salah satu selesai: locust (RUN_TIME habis) atau monitor (jenuh)
while kill -0 "$LOCUST_PID" 2>/dev/null && kill -0 "$MONITOR_PID" 2>/dev/null; do
    sleep 1
done

SATURATED=0
if kill -0 "$LOCUST_PID" 2>/dev/null; then
    # Monitor keluar duluan -> jenuh (exit code 3)
    set +e
    wait "$MONITOR_PID"
    MONITOR_EXIT=$?
    set -e
    if [ "$MONITOR_EXIT" -eq 3 ]; then
        echo "==> [AUTO-STOP] Jenuh terdeteksi, menghentikan locust..."
        SATURATED=1
        kill "$LOCUST_PID" 2>/dev/null || true
        wait "$LOCUST_PID" 2>/dev/null || true
    else
        echo "==> [WARN] Monitor berhenti abnormal (exit $MONITOR_EXIT), menghentikan locust"
        kill "$LOCUST_PID" 2>/dev/null || true
        wait "$LOCUST_PID" 2>/dev/null || true
    fi
else
    echo "==> Locust selesai sendiri ($RUN_TIME habis)"
    kill "$MONITOR_PID" 2>/dev/null || true
    wait "$MONITOR_PID" 2>/dev/null || true
fi

trap - EXIT

echo "==> [3/3] Analisis kapasitas (backlog + RPS + latency)"
python analyze_capacity_full.py --monitor "$MONITOR_CSV" \
  --locust "${LOCUST_PREFIX}_stats_history.csv" \
  --output "$OUTPUT_DIR/kapasitas_${STAMP}.txt"

echo
echo "Hasil:"
echo "  monitor : $MONITOR_CSV"
echo "  locust  : ${LOCUST_PREFIX}_stats_history.csv"
echo "  analisis: $OUTPUT_DIR/kapasitas_${STAMP}.txt"
[ "$SATURATED" = "1" ] && echo "  status  : AUTO-STOP (server jenuh terdeteksi)"
