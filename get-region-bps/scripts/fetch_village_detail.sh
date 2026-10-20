#!/usr/bin/env bash
# Jalankan fetch_village_detail.py di background (nohup) dan kelola prosesnya.
#
# Pemakaian:
#   ./scripts/fetch_village_detail.sh start                          # pakai data/village_data.csv
#   ./scripts/fetch_village_detail.sh start data/lainnya.csv
#   ./scripts/fetch_village_detail.sh start data/village_data.csv --skip-db   # argumen tambahan diteruskan ke fetch_village_detail.py
#   CHUNK_SIZE=100 DELAY=1 ./scripts/fetch_village_detail.sh start   # opsional (default: CHUNK_SIZE=50, DELAY=0.5)
#   ./scripts/fetch_village_detail.sh status                         # cek masih jalan atau tidak + ringkasan log
#   ./scripts/fetch_village_detail.sh log                            # ikuti log terbaru (tail -f)
#   ./scripts/fetch_village_detail.sh stop                           # hentikan proses
#
# Di macOS proses dibungkus `caffeinate -i` supaya Mac tidak sleep selama fetch berjalan.

set -u

cd "$(dirname "$0")/.."

COMMAND="${1:-start}"
shift $(( $# > 0 ? 1 : 0 ))
CHUNK_SIZE="${CHUNK_SIZE:-50}"
DELAY="${DELAY:-0.5}"
PYTHON="${PYTHON:-.venv/bin/python}"

LOG_DIR="logs"
PID_FILE="$LOG_DIR/fetch_village_detail.pid"
LATEST_LOG="$LOG_DIR/fetch_village_detail.latest"
mkdir -p "$LOG_DIR"

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

latest_log() {
    [[ -f "$LATEST_LOG" ]] && cat "$LATEST_LOG"
}

case "$COMMAND" in
    start)
        CSV_FILE="${1:-data/village_data.csv}"
        shift $(( $# > 0 ? 1 : 0 ))
        EXTRA_ARGS=("$@")

        if is_running; then
            echo "Masih berjalan (PID $(cat "$PID_FILE")), log: $(latest_log)"
            exit 1
        fi
        if [[ ! -f "$CSV_FILE" ]]; then
            echo "File $CSV_FILE tidak ditemukan"
            exit 1
        fi
        if [[ ! -x "$PYTHON" ]]; then
            echo "Python $PYTHON tidak ditemukan; buat venv dan pip install -r requirements.txt dulu"
            exit 1
        fi

        LOG_FILE="$LOG_DIR/fetch_village_detail_$(date +%Y%m%d_%H%M%S).log"
        RUNNER=()
        command -v caffeinate >/dev/null && RUNNER=(caffeinate -i)

        nohup ${RUNNER[@]+"${RUNNER[@]}"} "$PYTHON" -u fetch_village_detail.py \
            --file "$CSV_FILE" --chunk-size "$CHUNK_SIZE" --delay "$DELAY" \
            ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        echo "$LOG_FILE" > "$LATEST_LOG"

        echo "Berjalan di background (PID $!)"
        echo "  file: $CSV_FILE, chunk: $CHUNK_SIZE, delay: ${DELAY}s"
        echo "  log : $LOG_FILE"

        # Cek env/koneksi DB gagal di awal supaya tidak baru ketahuan belakangan.
        sleep 3
        if ! is_running; then
            echo "Proses langsung berhenti, isi log:"
            cat "$LOG_FILE"
            exit 1
        fi
        echo "Pantau: ./scripts/fetch_village_detail.sh log | status | stop"
        ;;

    status)
        LOG_FILE="$(latest_log)"
        if is_running; then
            echo "Berjalan (PID $(cat "$PID_FILE"))"
        else
            echo "Tidak berjalan"
        fi
        if [[ -n "$LOG_FILE" && -f "$LOG_FILE" ]]; then
            echo "Log: $LOG_FILE"
            grep -E "^\[.*\] == \[" "$LOG_FILE" | tail -n 1
            grep -E "GAGAL|Chunk gagal" "$LOG_FILE" | tail -n 5
            grep -E "Selesai:|Database:" "$LOG_FILE"
        fi
        ;;

    log)
        LOG_FILE="$(latest_log)"
        if [[ -z "$LOG_FILE" || ! -f "$LOG_FILE" ]]; then
            echo "Belum ada log"
            exit 1
        fi
        tail -f "$LOG_FILE"
        ;;

    stop)
        if ! is_running; then
            echo "Tidak ada proses yang berjalan"
            rm -f "$PID_FILE"
            exit 0
        fi
        PID="$(cat "$PID_FILE")"
        # Hentikan child (python di bawah caffeinate) lalu prosesnya sendiri.
        pkill -TERM -P "$PID" 2>/dev/null
        kill -TERM "$PID" 2>/dev/null
        rm -f "$PID_FILE"
        echo "Dihentikan (PID $PID). Chunk yang sudah selesai tetap tersimpan, log: $(latest_log)"
        ;;

    *)
        echo "Perintah tidak dikenal: $COMMAND (pakai start | status | log | stop)"
        exit 1
        ;;
esac
