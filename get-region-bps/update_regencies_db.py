"""Update latitude, longitude di tabel ref_regencies dari wilayah.cahyadsn.com.

CSV wajib punya kolom id (id di ref_regencies) dan code_dagri (contoh 93.04); kolom name dan
province_id opsional. Tiap code_dagri di-GET ke
https://wilayah.cahyadsn.com/api/?kode={code_dagri}&detail=1.

code_dagri yang nol di belakangnya hilang (contoh 11.1) dinormalisasi jadi 11.10 sebelum
di-request. Kalau kode tetap tidak ditemukan dan CSV punya name + province_id (contoh
kabupaten di provinsi pemekaran Papua yang masih memakai kode lama 91.18, padahal sekarang
93.04), kode baru dicari dari daftar kabupaten/kota di province_id-nya
(?kode={province_id}&level=2) berdasarkan nama.

Response disimpan ke regency_detail/YYYYMMDD/{kode}.json. Setiap --batch-size (default 20)
data yang berhasil di-fetch disimpan ke regency_payload/regency_payload_{n}.json, isinya:
    [{"id", "code_dagri", "name", "latitude", "longitude"}, ...]
lalu langsung di-update ke DB dengan satu query UPDATE ... FROM (VALUES ...)
WHERE ref_regencies.id = id dan di-commit. Kabupaten/kota yang gagal di-fetch atau tanpa
lat/lng dilewati.

Koneksi database dibaca dari .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.

Contoh:
    python update_regencies_db.py --file data/regency.csv
    python update_regencies_db.py --file data/regency.csv --delay 2 --dry-run
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import psycopg2

import config  # noqa: F401  memuat .env saat di-import
from utils.query import REGENCY_TABLE, connect, update_region_coordinates
from utils.utility import (
    BASE_DIR,
    fetch_region_children,
    fetch_region_detail,
    log,
    to_float,
    write_json,
)

REGENCY_DETAIL_DIR = BASE_DIR / "regency_detail"
REGENCY_PAYLOAD_DIR = BASE_DIR / "regency_payload"


def read_regencies(path: Path) -> list[dict]:
    """Baca baris {id, code_dagri, name?, province_id?} dari CSV; baris tanpa id atau code_dagri dilewati."""
    with path.open(newline="") as f:
        rows = [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(f)
        ]
    return [r for r in rows if r.get("id") and r.get("code_dagri")]


def normalize_code(code_dagri: str) -> str:
    """Kembalikan nol yang hilang di segmen kabupaten: 11.1 -> 11.10, 91.2 -> 91.20."""
    code = code_dagri.strip()
    return code + "0" if re.fullmatch(r"\d{2}\.\d", code) else code


def normalize_name(name: str) -> str:
    """Samakan penulisan nama: "Kab. Pangkajene Dan Kepulauan" ~ "Kabupaten Pangkajene dan Kepulauan"."""
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    name = re.sub(r"^(kabupaten|kab) ", "kab ", " ".join(name.split()))
    return name


def find_code_by_name(parent_id: str, name: str, children_cache: dict[str, dict[str, str]]) -> str | None:
    """Cari kode wilayah dari daftar anak parent_id berdasarkan nama; daftar di-cache per parent."""
    if parent_id not in children_cache:
        children = fetch_region_children(parent_id)
        children_cache[parent_id] = {normalize_name(c["nama"]): c["kode"] for c in children if c.get("nama")}
    return children_cache[parent_id].get(normalize_name(name))


def fetch_coordinates(regency: dict, children_cache: dict[str, dict[str, str]]) -> dict | None:
    """Fetch lat/lng satu kabupaten/kota. Return item payload, atau None kalau gagal."""
    code_dagri = regency["code_dagri"]
    name = regency.get("name", "")
    parent_id = regency.get("province_id", "")
    kode = normalize_code(code_dagri)
    detail = fetch_region_detail(kode, REGENCY_DETAIL_DIR)
    if not detail and parent_id and name:
        new_kode = find_code_by_name(parent_id, name, children_cache)
        if new_kode and new_kode != kode:
            log(f"  kode baru {new_kode} ditemukan dari nama di wilayah {parent_id}")
            kode = new_kode
            detail = fetch_region_detail(kode, REGENCY_DETAIL_DIR)
    latitude = to_float(detail.get("lat")) if detail else None
    longitude = to_float(detail.get("lng")) if detail else None
    if latitude is None or longitude is None:
        log(f"[FAIL] {kode} {name}: lat/lng tidak ditemukan" if name else f"[FAIL] {kode}: lat/lng tidak ditemukan")
        return None
    return {
        "id": regency["id"],
        "code_dagri": kode,
        "name": name or detail.get("nama"),
        "latitude": latitude,
        "longitude": longitude,
    }


def update_batch(index: int, items: list[dict]) -> tuple[int, bool]:
    """Update satu batch ke ref_regencies dengan koneksi sendiri. Return (terupdate, gagal)."""
    rows = [(item["id"], item["latitude"], item["longitude"]) for item in items]
    try:
        conn = connect()
        try:
            updated, failed = update_region_coordinates(conn, REGENCY_TABLE, rows, len(rows))
        finally:
            conn.close()
    except psycopg2.Error as e:  # koneksi putus dll.; JSON batch tetap tersimpan untuk diulang
        log(f"  batch {index}: update DB GAGAL ({e})")
        return 0, True
    log(f"  batch {index}: {updated}/{len(rows)} kabupaten/kota terupdate di {REGENCY_TABLE}")
    return updated, bool(failed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="File CSV dengan kolom id,code_dagri (opsional: name,province_id)",
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

    regencies = read_regencies(args.file)
    if not regencies:
        log(f"Tidak ada baris id,code_dagri di {args.file}")
        return 1
    if not args.dry_run:
        # Cek env & koneksi di awal supaya tidak baru gagal setelah batch pertama.
        connect().close()
    log(f"{len(regencies)} kabupaten/kota akan di-fetch, update DB setiap {args.batch_size} data")

    children_cache: dict[str, dict[str, str]] = {}
    batch: list[dict] = []
    batch_index = 0
    failed: list[str] = []
    updated = 0
    failed_batches: list[int] = []

    def flush(items: list[dict]) -> None:
        nonlocal batch_index, updated
        batch_index += 1
        path = write_json(REGENCY_PAYLOAD_DIR / f"regency_payload_{batch_index}.json", items)
        log(f"  batch {batch_index}: {len(items)} data tersimpan di {path}")
        if args.dry_run:
            return
        batch_updated, batch_failed = update_batch(batch_index, items)
        updated += batch_updated
        if batch_failed:
            failed_batches.append(batch_index)

    for i, regency in enumerate(regencies):
        if i and args.delay:
            time.sleep(args.delay)
        code_dagri = regency["code_dagri"]
        name = regency.get("name", "")
        kode = normalize_code(code_dagri)
        note = f" (code_dagri CSV {code_dagri})" if kode != code_dagri else ""
        label = f"{kode} {name}" if name else kode
        log(f"== [{i + 1}/{len(regencies)}] id={regency['id']} code_dagri={label}{note}")
        item = fetch_coordinates(regency, children_cache)
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
    found = len(regencies) - len(failed)
    log(f"Selesai: {found} ditemukan, {len(failed)} gagal dari {len(regencies)} baris, {batch_index} batch")
    if args.dry_run:
        log("Dry run, database tidak diubah")
    else:
        log(f"Database: {updated} kabupaten/kota terupdate di {REGENCY_TABLE}")
    if failed:
        log(f"Gagal fetch ({len(failed)}): {', '.join(failed)}")
    if failed_batches:
        log(f"Batch gagal update DB: {failed_batches}; ulangi dengan CSV berisi data di file JSON batch tersebut")
    return 1 if failed or failed_batches else 0


if __name__ == "__main__":
    sys.exit(main())
