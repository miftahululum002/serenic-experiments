"""Ambil detail desa (kodepos, lat/lng) dari wilayah.cahyadsn.com untuk setiap baris CSV.

CSV wajib punya kolom village_id dan code_dagri (kode Kemendagri, contoh 35.10.08.2006).
Hasil disimpan per chunk ke village_payload/village_payload_{n}.json, isinya:
    [{"village_id", "code_dagri", "postal_code", "latitude", "longitude"}, ...]
Setiap chunk yang tersimpan langsung di-update ke tabel ref_villages (bulk per
--batch-size data, default 100). Jika update chunk gagal, file JSON-nya tetap ada
dan bisa diulang dengan update_villages_db.py --file <file>.

Contoh:
    python fetch_village_detail.py --file data/village_data.csv
    python fetch_village_detail.py --file data/village_data.csv --chunk-size 50 --delay 0.5
    python fetch_village_detail.py --file data/village_data.csv --skip-db   # hanya simpan JSON
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import config  # noqa: F401  memuat .env (koneksi DB) saat di-import
from utils.query import connect, update_items
from utils.utility import (
    BASE_DIR,
    build_update_payload,
    fetch_village_detail,
    log,
    write_json,
)

VILLAGE_PAYLOAD_DIR = BASE_DIR / "village_payload"


def read_villages(path: Path) -> list[dict]:
    """Baca baris {village_id, code_dagri} dari CSV; baris tanpa code_dagri dilewati."""
    with path.open(newline="") as f:
        rows = [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(f)
        ]
    return [r for r in rows if r.get("village_id") and r.get("code_dagri")]


def save_chunk(index: int, items: list[dict]) -> Path:
    path = write_json(VILLAGE_PAYLOAD_DIR / f"village_payload_{index}.json", items)
    log(f"  chunk {index}: {len(items)} data tersimpan di {path}")
    return path


def update_chunk(index: int, path: Path, items: list[dict], batch_size: int) -> tuple[int, int, bool]:
    """Update satu chunk ke ref_villages. Return (terupdate, tidak ditemukan, gagal)."""
    try:
        updated, not_found, failed = update_items(items, batch_size)
    except Exception as e:  # koneksi putus dll.; JSON tetap tersimpan untuk diulang
        log(f"  chunk {index}: update DB GAGAL ({e}); ulangi dengan update_villages_db.py --file {path}")
        return 0, 0, True
    log(f"  chunk {index}: {updated} terupdate, {not_found} village_id tidak ditemukan di ref_villages")
    if failed:
        log(f"  chunk {index}: batch gagal {failed}; ulangi dengan update_villages_db.py --file {path}")
    return updated, not_found, bool(failed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="File CSV dengan kolom village_id,code_dagri",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=200,
        help="Jumlah data per file JSON (default: 200)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1,
        help="Jeda antar request dalam detik (default: 1)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Jumlah data per query UPDATE ke database (default: 100)",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Hanya simpan JSON, tanpa update ke database",
    )
    args = parser.parse_args()

    villages = read_villages(args.file)
    if not villages:
        log(f"Tidak ada baris village_id,code_dagri di {args.file}")
        return 1

    if not args.skip_db:
        # Cek env & koneksi di awal supaya tidak baru gagal setelah fetch chunk pertama.
        connect().close()

    chunk: list[dict] = []
    chunk_index = 0
    failed = 0
    db_updated = db_not_found = 0
    db_failed_chunks: list[int] = []

    def flush(items: list[dict]) -> None:
        nonlocal chunk_index, db_updated, db_not_found
        chunk_index += 1
        path = save_chunk(chunk_index, items)
        if args.skip_db:
            return
        updated, not_found, chunk_failed = update_chunk(chunk_index, path, items, args.batch_size)
        db_updated += updated
        db_not_found += not_found
        if chunk_failed:
            db_failed_chunks.append(chunk_index)

    for i, village in enumerate(villages):
        if i and args.delay:
            time.sleep(args.delay)
        log(f"== [{i + 1}/{len(villages)}] village_id={village['village_id']}")
        detail = fetch_village_detail(village["code_dagri"])
        if not detail:
            failed += 1
            continue
        chunk.append(
            {
                "village_id": village["village_id"],
                "code_dagri": village["code_dagri"],
                **build_update_payload(detail),
            }
        )
        if len(chunk) == args.chunk_size:
            flush(chunk)
            chunk = []
    if chunk:
        flush(chunk)

    print()
    found = len(villages) - failed
    log(
        f"Selesai: {found} ditemukan, {failed} tidak ditemukan/gagal dari {len(villages)} baris, {chunk_index} file JSON"
    )
    if not args.skip_db:
        log(f"Database: {db_updated} terupdate, {db_not_found} village_id tidak ditemukan di ref_villages")
        if db_failed_chunks:
            log(f"Chunk gagal update DB: {db_failed_chunks}")
    return 0 if not failed and not db_failed_chunks else 1


if __name__ == "__main__":
    sys.exit(main())
