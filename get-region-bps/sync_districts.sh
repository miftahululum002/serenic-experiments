#!/usr/bin/env bash
# Loop setiap district_id di CSV dan jalankan sync_villages.py untuk masing-masing.
#
# Pemakaian:
#   ./sync_districts.sh                         # pakai data/district_kosong.csv
#   ./sync_districts.sh data/lainnya.csv
#   BATCH_SIZE=10 BATCH_DELAY=10 ./sync_districts.sh  # opsional: jeda 10 detik setiap 10 district (default: tanpa jeda antar district)
#   ./sync_districts.sh data/district_kosong.csv --dry-run   # argumen tambahan diteruskan ke sync_villages.py

set -u

cd "$(dirname "$0")"

CSV_FILE="${1:-data/district_kosong.csv}"
shift $(( $# > 0 ? 1 : 0 ))
EXTRA_ARGS=("$@")
BATCH_SIZE="${BATCH_SIZE:-10}"
BATCH_DELAY="${BATCH_DELAY:-0}"
PYTHON="${PYTHON:-.venv/bin/python}"

if [[ ! -f "$CSV_FILE" ]]; then
    echo "File $CSV_FILE tidak ditemukan"
    exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="logs"
LOG_FILE="$LOG_DIR/sync_${TIMESTAMP}.log"
FAILED_FILE="$LOG_DIR/failed_${TIMESTAMP}.txt"
mkdir -p "$LOG_DIR"

total=$(( $(tail -n +2 "$CSV_FILE" | grep -c .) ))
count=0
ok=0
failed=0

# `|| [[ -n $line ]]` supaya baris terakhir tanpa newline tetap terbaca
while IFS= read -r line || [[ -n "$line" ]]; do
    district_id="$(echo "$line" | cut -d, -f1 | tr -d '\r" ')"
    [[ -z "$district_id" ]] && continue

    count=$((count + 1))
    echo "===== [$count/$total] district_id=$district_id =====" | tee -a "$LOG_FILE"

    if "$PYTHON" -u sync_villages.py --parent "$district_id" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} 2>&1 | tee -a "$LOG_FILE"; [[ ${PIPESTATUS[0]} -eq 0 ]]; then
        ok=$((ok + 1))
    else
        failed=$((failed + 1))
        echo "$district_id" >> "$FAILED_FILE"
    fi
    echo | tee -a "$LOG_FILE"

    if (( BATCH_DELAY > 0 && count % BATCH_SIZE == 0 && count < total )); then
        echo "--- $count district diproses, jeda ${BATCH_DELAY} detik ---" | tee -a "$LOG_FILE"
        sleep "$BATCH_DELAY"
    fi
done < <(tail -n +2 "$CSV_FILE")

echo "Selesai: $count district, $ok berhasil, $failed gagal" | tee -a "$LOG_FILE"
echo "Log: $LOG_FILE"
[[ $failed -gt 0 ]] && echo "District gagal: $FAILED_FILE"

[[ $failed -eq 0 ]]
