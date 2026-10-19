"""Bulk update postal_code, latitude, longitude di tabel ref_villages dari file village_payload.

Sumber: village_payload/village_payload_{n}.json (hasil fetch_village_detail.py), isinya:
    [{"village_id", "code_dagri", "postal_code", "latitude", "longitude"}, ...]

Update dikirim per batch (default 100 data) dalam satu query UPDATE ... FROM (VALUES ...)
WHERE ref_villages.id = village_id, dan di-commit per batch.

Koneksi database dibaca dari .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.

Contoh:
    python update_villages_db.py
    python update_villages_db.py --dir village_payload --batch-size 100
    python update_villages_db.py --file village_payload/village_payload_1.json
    python update_villages_db.py --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path

import config  # noqa: F401  memuat .env saat di-import
from utils.query import TABLE, VILLAGE_PAYLOAD_DIR, connect, item_to_row, update_batches
from utils.utility import log


def payload_files(directory: Path) -> list[Path]:
    """File village_payload_*.json diurutkan berdasarkan nomor, bukan urutan string."""

    def number(path: Path) -> int:
        match = re.search(r"(\d+)$", path.stem)
        return int(match.group(1)) if match else 0

    return sorted(directory.glob("village_payload_*.json"), key=number)


def read_rows(files: list[Path]) -> list[tuple]:
    """Gabungkan semua payload jadi tuple (id, postal_code, latitude, longitude); id duplikat diambil yang terakhir."""
    rows = {}
    for path in files:
        for item in json.loads(path.read_text()):
            row = item_to_row(item)
            if row:
                rows[row[0]] = row
    return list(rows.values())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--dir",
        type=Path,
        default=VILLAGE_PAYLOAD_DIR,
        help="Folder village_payload (default: village_payload)",
    )
    source.add_argument(
        "--file",
        type=Path,
        nargs="+",
        help="File payload tertentu, bisa lebih dari satu",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Jumlah data per query UPDATE (default: 100)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0,
        help="Jeda antar batch dalam detik (default: 0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Hanya baca payload dan tampilkan ringkasan, tanpa update DB",
    )
    args = parser.parse_args()

    files = args.file or payload_files(args.dir)
    if not files:
        log("Tidak ada file payload ditemukan")
        return 1

    rows = read_rows(files)
    log(
        f"{len(rows)} village dari {len(files)} file, batch {args.batch_size} -> {(len(rows) + args.batch_size - 1) // args.batch_size} query"
    )
    if args.dry_run:
        for row in rows[:5]:
            log(f"  {row}")
        log("Dry run, database tidak diubah")
        return 0

    conn = connect()
    try:
        updated, not_found, failed = update_batches(
            conn, rows, args.batch_size, args.delay
        )
    finally:
        conn.close()

    log(
        f"Selesai: {updated} terupdate, {not_found} village_id tidak ditemukan di {TABLE}"
    )
    if failed:
        log(f"Batch gagal: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
