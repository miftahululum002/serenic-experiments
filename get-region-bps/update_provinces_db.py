"""Update latitude, longitude di tabel ref_provinces dari wilayah.cahyadsn.com.

CSV wajib punya kolom id (id di ref_provinces) dan code_dagri (contoh 95). Tiap code_dagri
di-GET ke https://wilayah.cahyadsn.com/api/?kode={code_dagri}&detail=1, response disimpan ke
province_detail/YYYYMMDD/{kode}.json dan ringkasannya ke province_payload/province_payload.json:
    [{"id", "code_dagri", "name", "latitude", "longitude"}, ...]
Setelah semua di-fetch, latitude/longitude di-update per batch (default 100 data) dengan
UPDATE ... FROM (VALUES ...) WHERE ref_provinces.id = id, commit per batch. Provinsi yang
gagal di-fetch atau tanpa lat/lng dilewati.

Koneksi database dibaca dari .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.

Contoh:
    python update_provinces_db.py --file data/province.csv
    python update_provinces_db.py --file data/province.csv --delay 2 --dry-run
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import config  # noqa: F401  memuat .env saat di-import
from utils.query import PROVINCE_TABLE, connect, update_region_coordinates
from utils.utility import BASE_DIR, fetch_region_detail, log, to_float, write_json

PROVINCE_DETAIL_DIR = BASE_DIR / "province_detail"
PROVINCE_PAYLOAD_FILE = BASE_DIR / "province_payload" / "province_payload.json"


def read_provinces(path: Path) -> list[dict]:
    """Baca baris {id, code_dagri} dari CSV; baris tanpa id atau code_dagri dilewati."""
    with path.open(newline="") as f:
        rows = [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(f)
        ]
    return [r for r in rows if r.get("id") and r.get("code_dagri")]


def fetch_coordinates(provinces: list[dict], delay: float) -> tuple[list[dict], list[str]]:
    """Fetch lat/lng tiap provinsi. Return (item payload, code_dagri yang gagal)."""
    items, failed = [], []
    for i, province in enumerate(provinces):
        if i and delay:
            time.sleep(delay)
        code_dagri = province["code_dagri"]
        log(f"== [{i + 1}/{len(provinces)}] id={province['id']} code_dagri={code_dagri}")
        detail = fetch_region_detail(code_dagri, PROVINCE_DETAIL_DIR)
        latitude = to_float(detail.get("lat")) if detail else None
        longitude = to_float(detail.get("lng")) if detail else None
        if latitude is None or longitude is None:
            log(f"[FAIL] {code_dagri}: lat/lng tidak ditemukan")
            failed.append(code_dagri)
            continue
        items.append(
            {
                "id": province["id"],
                "code_dagri": code_dagri,
                "name": detail.get("nama"),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    return items, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="File CSV dengan kolom id,code_dagri",
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
        default=1,
        help="Jeda antar request ke wilayah.cahyadsn.com dalam detik (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch dan simpan payload saja, tanpa update DB",
    )
    args = parser.parse_args()

    provinces = read_provinces(args.file)
    if not provinces:
        log(f"Tidak ada baris id,code_dagri di {args.file}")
        return 1
    if not args.dry_run:
        # Cek env & koneksi di awal supaya tidak baru gagal setelah semua di-fetch.
        connect().close()
    log(f"{len(provinces)} provinsi akan di-fetch")

    items, failed = fetch_coordinates(provinces, args.delay)
    path = write_json(PROVINCE_PAYLOAD_FILE, items)
    log(f"{len(items)} provinsi tersimpan di {path}")

    failed_batches = []
    if args.dry_run:
        for item in items[:5]:
            log(f"  {item}")
        log("Dry run, database tidak diubah")
    elif items:
        rows = [(item["id"], item["latitude"], item["longitude"]) for item in items]
        conn = connect()
        try:
            updated, failed_batches = update_region_coordinates(conn, PROVINCE_TABLE, rows, args.batch_size)
        finally:
            conn.close()
        log(f"Selesai: {updated}/{len(rows)} provinsi terupdate di {PROVINCE_TABLE}")

    if failed:
        log(f"Gagal fetch ({len(failed)}): {', '.join(failed)}")
    if failed_batches:
        log(f"Batch gagal: {failed_batches}")
    return 1 if failed or failed_batches else 0


if __name__ == "__main__":
    sys.exit(main())
