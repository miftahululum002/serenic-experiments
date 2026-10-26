"""Update code_dagri di tabel ref_districts berdasarkan nama kecamatan dari wilayah.cahyadsn.com.

CSV wajib punya kolom id (id kabupaten/kota, = ref_districts.regency_id) dan code_dagri
(kode kabupaten/kota, contoh 96.02). Per baris:
  1. GET https://wilayah.cahyadsn.com/api/?kode={code_dagri}&level=3 -> daftar kecamatan
     [{kode, nama}, ...]; response disimpan ke output/district_code/YYYYMMDD/{timestamp}_{kode}.json.
  2. Ambil kecamatan di ref_districts dengan regency_id = id.
  3. Cocokkan berdasarkan nama (huruf besar/kecil, tanda baca, awalan "Kecamatan"/"Distrik"
     diabaikan). Kalau tidak ada yang sama persis, dipakai nama paling mirip (beda ejaan
     tipis seperti Metemani ~ Matemani) asal kemiripannya >= --min-similarity.
  4. UPDATE ref_districts SET code_dagri = kode WHERE id = id, satu query per kabupaten/kota,
     lalu di-commit. Payload disimpan ke district_code/district_code_{code_dagri}.json.

Kecamatan yang tidak ketemu pasangannya dicatat dan tidak diubah.

Koneksi database dibaca dari .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.

Contoh:
    python update_district_code_by_regency.py --file data/regency_fixing.csv --dry-run
    python update_district_code_by_regency.py --file data/regency_fixing.csv
"""

import argparse
import csv
import re
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

import config  # noqa: F401  memuat .env saat di-import
from utils.query import DISTRICT_TABLE, connect
from utils.utility import (
    PAYLOAD_DIR,
    OUTPUT_DIR,
    fetch_region_children,
    get_timestamp,
    log,
    write_json,
)

DISTRICT_CODE_DETAIL_DIR = OUTPUT_DIR / "district_code"
DISTRICT_CODE_PAYLOAD_DIR = PAYLOAD_DIR / "district_code"


def read_regencies(path: Path) -> list[dict]:
    """Baca baris {id, code_dagri} dari CSV; baris tanpa id atau code_dagri dilewati."""
    with path.open(newline="") as f:
        rows = [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(f)
        ]
    return [r for r in rows if r.get("id") and r.get("code_dagri")]


def normalize_name(name: str) -> str:
    """Samakan penulisan nama: "KEC. Sorong-Barat" ~ "Sorong Barat"."""
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return re.sub(r"^(kecamatan|kec|distrik) ", "", " ".join(name.split()))


def fetch_districts(regency_id: str) -> list[tuple[str, str, str | None]]:
    """Kecamatan aktif di ref_districts milik satu kabupaten/kota: [(id, name, code_dagri), ...]."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, name, code_dagri FROM {DISTRICT_TABLE} "
                "WHERE regency_id = %s AND deleted_at IS NULL ORDER BY name",
                (regency_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def match_districts(
    districts: list[tuple[str, str, str | None]],
    api_children: list[dict],
    min_similarity: float,
) -> tuple[list[dict], list[str]]:
    """Pasangkan kecamatan DB dengan kecamatan API berdasarkan nama.

    Nama yang sama persis dipasangkan dulu, sisanya pakai nama paling mirip. Satu kode API
    hanya dipakai sekali. Return (item payload, nama DB yang tidak ketemu).
    """
    api_by_name = {
        normalize_name(c["nama"]): c
        for c in api_children
        if c.get("nama") and c.get("kode")
    }
    used: set[str] = set()
    matched: dict[str, dict] = {}

    for district_id, name, _ in districts:
        child = api_by_name.get(normalize_name(name))
        if child and child["kode"] not in used:
            matched[district_id] = child
            used.add(child["kode"])

    for district_id, name, _ in districts:
        if district_id in matched:
            continue
        candidates = [
            (SequenceMatcher(None, normalize_name(name), key).ratio(), child)
            for key, child in api_by_name.items()
            if child["kode"] not in used
        ]
        if not candidates:
            continue
        score, child = max(candidates, key=lambda c: c[0])
        if score >= min_similarity:
            log(f"  {name} ~ {child['nama']} (kemiripan {score:.2f})")
            matched[district_id] = child
            used.add(child["kode"])

    items, missing = [], []
    for district_id, name, old_code in districts:
        child = matched.get(district_id)
        if not child:
            missing.append(name)
            continue
        items.append(
            {
                "id": district_id,
                "name": name,
                "api_name": child["nama"],
                "old_code_dagri": old_code,
                "code_dagri": child["kode"],
            }
        )
    return items, missing


def update_codes(items: list[dict]) -> int:
    """Bulk update code_dagri di ref_districts dalam satu transaksi. Return jumlah baris terupdate."""
    sql = f"""
        UPDATE {DISTRICT_TABLE} AS r
        SET code_dagri = d.code_dagri
        FROM (VALUES %s) AS d (id, code_dagri)
        WHERE r.id = d.id
    """
    rows = [(item["id"], item["code_dagri"]) for item in items]
    conn = connect()
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                sql,
                rows,
                template="(%s::varchar, %s::varchar)",
                page_size=len(rows),
            )
            affected = cur.rowcount
        conn.commit()
        return affected
    except psycopg2.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=False,
        default="data/regency_fixing.csv",
        help="File CSV dengan kolom id,code_dagri kabupaten/kota",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.85,
        help="Kemiripan nama minimum (0-1) untuk nama yang tidak sama persis (default: 0.85)",
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
        help="Fetch, cocokkan, dan simpan payload saja, tanpa update DB",
    )
    args = parser.parse_args()

    regencies = read_regencies(args.file)
    if not regencies:
        log(f"Tidak ada baris id,code_dagri di {args.file}")
        return 1
    connect().close()  # cek env & koneksi di awal
    log(f"{len(regencies)} kabupaten/kota akan diproses")

    updated = 0
    failed_regencies: list[str] = []
    missing_all: list[str] = []

    for i, regency in enumerate(regencies):
        if i and args.delay:
            time.sleep(args.delay)
        regency_id, kode = regency["id"], regency["code_dagri"]
        print()
        log(f"== [{i + 1}/{len(regencies)}] regency_id={regency_id} code_dagri={kode}")

        children = fetch_region_children(kode)
        path = write_json(
            DISTRICT_CODE_DETAIL_DIR
            / datetime.now().strftime("%Y%m%d")
            / f"{get_timestamp()}_{kode}.json",
            children,
        )
        log(f"  {len(children)} kecamatan dari API, tersimpan di {path}")
        if not children:
            failed_regencies.append(kode)
            continue

        districts = fetch_districts(regency_id)
        log(
            f"  {len(districts)} kecamatan di {DISTRICT_TABLE} dengan regency_id={regency_id}"
        )
        if not districts:
            failed_regencies.append(kode)
            continue

        items, missing = match_districts(districts, children, args.min_similarity)
        for item in items:
            change = (
                "tetap"
                if item["old_code_dagri"] == item["code_dagri"]
                else f"{item['old_code_dagri']} -> {item['code_dagri']}"
            )
            log(f"  {item['id']} {item['name']}: {change}")
        for name in missing:
            log(f"  [FAIL] {name}: tidak ada nama yang cocok di API")
        missing_all.extend(f"{kode}/{name}" for name in missing)

        if not items:
            continue
        path = write_json(
            DISTRICT_CODE_PAYLOAD_DIR / f"district_code_{kode}.json", items
        )
        log(f"  {len(items)} data tersimpan di {path}")
        if args.dry_run:
            continue
        try:
            affected = update_codes(items)
        except psycopg2.Error as e:
            log(f"  update DB GAGAL ({e})")
            failed_regencies.append(kode)
            continue
        updated += affected
        log(f"  {affected}/{len(items)} kecamatan terupdate di {DISTRICT_TABLE}")

    print()
    if args.dry_run:
        log("Dry run, database tidak diubah")
    else:
        log(f"Selesai: {updated} kecamatan terupdate di {DISTRICT_TABLE}")
    if missing_all:
        log(f"Tidak cocok ({len(missing_all)}): {', '.join(missing_all)}")
    if failed_regencies:
        log(f"Kabupaten/kota gagal: {', '.join(failed_regencies)}")
    return 1 if missing_all or failed_regencies else 0


if __name__ == "__main__":
    sys.exit(main())
