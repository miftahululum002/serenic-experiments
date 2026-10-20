"""Update latitude, longitude di tabel ref_districts dari wilayah.cahyadsn.com.

CSV wajib punya kolom id (id di ref_districts) dan code_dagri (contoh 96.05.01); kolom name dan
regency_id opsional. Tiap code_dagri di-GET ke
https://wilayah.cahyadsn.com/api/?kode={code_dagri}&detail=1.

code_dagri yang nol di belakangnya hilang (contoh 96.05.1) dinormalisasi jadi 96.05.10 sebelum
di-request. Kalau kode tetap tidak ditemukan dan CSV punya name + regency_id, kode baru dicari
dari daftar kecamatan di regency_id-nya (?kode={regency_id}&level=3) berdasarkan nama.

Response disimpan ke output/district/YYYYMMDD/{kode}.json. Setiap --batch-size (default 20)
data yang berhasil di-fetch disimpan ke district/district_payload_{n}.json, isinya:
    [{"id", "code_dagri", "name", "latitude", "longitude"}, ...]
lalu langsung di-update ke DB dengan satu query UPDATE ... FROM (VALUES ...)
WHERE ref_districts.id = id dan di-commit. Kecamatan yang gagal di-fetch atau tanpa
lat/lng dilewati.

Koneksi database dibaca dari .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.

Contoh:
    python update_districts_db.py --file data/district.csv
    python update_districts_db.py --file data/district.csv --delay 2 --dry-run
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import psycopg2

import config  # noqa: F401  memuat .env saat di-import
from utils.query import DISTRICT_TABLE, connect, update_region_coordinates
from utils.utility import (
    PAYLOAD_DIR,
    OUTPUT_DIR,
    fetch_region_children,
    fetch_region_detail,
    get_timestamp,
    log,
    to_float,
    write_json,
)

DISTRICT_DETAIL_DIR = OUTPUT_DIR / "district"
DISTRICT_PAYLOAD_DIR = PAYLOAD_DIR / "district"


def read_districts(path: Path) -> list[dict]:
    """Baca baris {id, code_dagri, name?, regency_id?} dari CSV; baris tanpa id atau code_dagri dilewati."""
    with path.open(newline="") as f:
        rows = [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(f)
        ]
    return [r for r in rows if r.get("id") and r.get("code_dagri")]


def normalize_code(code_dagri: str) -> str:
    """Kembalikan nol yang hilang di segmen kecamatan: 96.05.1 -> 96.05.10."""
    code = code_dagri.strip()
    return code + "0" if re.fullmatch(r"\d{2}\.\d{2}\.\d", code) else code


def normalize_name(name: str) -> str:
    """Samakan penulisan nama: "KEC. Sorong-Barat" ~ "Sorong Barat"."""
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return re.sub(r"^(kecamatan|kec|distrik) ", "", " ".join(name.split()))


def find_code_by_name(
    parent_id: str, name: str, children_cache: dict[str, dict[str, str]]
) -> str | None:
    """Cari kode kecamatan dari daftar anak parent_id berdasarkan nama; daftar di-cache per parent."""
    if parent_id not in children_cache:
        children = fetch_region_children(parent_id)
        children_cache[parent_id] = {
            normalize_name(c["nama"]): c["kode"] for c in children if c.get("nama")
        }
    return children_cache[parent_id].get(normalize_name(name))


def fetch_coordinates(
    district: dict, children_cache: dict[str, dict[str, str]]
) -> dict | None:
    """Fetch lat/lng satu kecamatan. Return item payload, atau None kalau gagal."""
    code_dagri = district["code_dagri"]
    name = district.get("name", "")
    parent_id = district.get("regency_id", "")
    kode = normalize_code(code_dagri)
    detail = fetch_region_detail(kode, DISTRICT_DETAIL_DIR)
    if not detail and parent_id and name:
        new_kode = find_code_by_name(parent_id, name, children_cache)
        if new_kode and new_kode != kode:
            log(f"  kode baru {new_kode} ditemukan dari nama di wilayah {parent_id}")
            kode = new_kode
            detail = fetch_region_detail(kode, DISTRICT_DETAIL_DIR)
    latitude = to_float(detail.get("lat")) if detail else None
    longitude = to_float(detail.get("lng")) if detail else None
    if latitude is None or longitude is None:
        log(
            f"[FAIL] {kode} {name}: lat/lng tidak ditemukan"
            if name
            else f"[FAIL] {kode}: lat/lng tidak ditemukan"
        )
        return None
    return {
        "id": district["id"],
        "code_dagri": kode,
        "name": name or detail.get("nama"),
        "latitude": latitude,
        "longitude": longitude,
    }


def update_batch(index: int, items: list[dict]) -> tuple[int, bool]:
    """Update satu batch ke ref_districts dengan koneksi sendiri. Return (terupdate, gagal)."""
    rows = [(item["id"], item["latitude"], item["longitude"]) for item in items]
    try:
        conn = connect()
        try:
            updated, failed = update_region_coordinates(
                conn, DISTRICT_TABLE, rows, len(rows)
            )
        finally:
            conn.close()
    except (
        psycopg2.Error
    ) as e:  # koneksi putus dll.; JSON batch tetap tersimpan untuk diulang
        log(f"  batch {index}: update DB GAGAL ({e})")
        return 0, True
    log(
        f"  batch {index}: {updated}/{len(rows)} kecamatan terupdate di {DISTRICT_TABLE}"
    )
    return updated, bool(failed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="File CSV dengan kolom id,code_dagri (opsional: name,regency_id)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Jumlah data per batch: disimpan ke JSON lalu di-UPDATE ke DB (default: 20)",
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

    districts = read_districts(args.file)
    if not districts:
        log(f"Tidak ada baris id,code_dagri di {args.file}")
        return 1
    if not args.dry_run:
        # Cek env & koneksi di awal supaya tidak baru gagal setelah batch pertama.
        connect().close()
    log(
        f"{len(districts)} kecamatan akan di-fetch, update DB setiap {args.batch_size} data"
    )

    children_cache: dict[str, dict[str, str]] = {}
    batch: list[dict] = []
    batch_index = 0
    failed: list[str] = []
    updated = 0
    failed_batches: list[int] = []

    def flush(items: list[dict]) -> None:
        nonlocal batch_index, updated
        batch_index += 1
        timestamp = get_timestamp()
        filename_extracted = (
            DISTRICT_PAYLOAD_DIR / f"district_payload_{batch_index}_{timestamp}.json"
        )
        path = write_json(filename_extracted, items)
        log(f"  batch {batch_index}: {len(items)} data tersimpan di {path}")
        if args.dry_run:
            return
        batch_updated, batch_failed = update_batch(batch_index, items)
        updated += batch_updated
        if batch_failed:
            failed_batches.append(batch_index)

    for i, district in enumerate(districts):
        if i and args.delay:
            time.sleep(args.delay)
        code_dagri = district["code_dagri"]
        name = district.get("name", "")
        kode = normalize_code(code_dagri)
        note = f" (code_dagri CSV {code_dagri})" if kode != code_dagri else ""
        label = f"{kode} {name}" if name else kode
        log(
            f"== [{i + 1}/{len(districts)}] id={district['id']} code_dagri={label}{note}"
        )
        item = fetch_coordinates(district, children_cache)
        if not item:
            failed.append(code_dagri)
            continue
        batch.append(item)
        if len(batch) == args.batch_size:
            flush(batch)
            batch = []
    if batch:
        flush(batch)

    print()
    found = len(districts) - len(failed)
    log(
        f"Selesai: {found} ditemukan, {len(failed)} gagal dari {len(districts)} baris, {batch_index} batch"
    )
    if args.dry_run:
        log("Dry run, database tidak diubah")
    else:
        log(f"Database: {updated} kecamatan terupdate di {DISTRICT_TABLE}")
    if failed:
        log(f"Gagal fetch ({len(failed)}): {', '.join(failed)}")
    if failed_batches:
        log(
            f"Batch gagal update DB: {failed_batches}; ulangi dengan CSV berisi data di file JSON batch tersebut"
        )
    return 1 if failed or failed_batches else 0


if __name__ == "__main__":
    sys.exit(main())
