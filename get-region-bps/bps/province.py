"""Sinkronkan tabel ref_provinces dengan daftar provinsi BPS (sig.bps.go.id).

Data diambil dari
https://sig.bps.go.id/rest-bridging/getwilayah?level=provinsi&parent=0&periode_merge={periode}
dan response disimpan ke output/bps/province/{timestamp}_provinsi_{periode}.json.

Provinsi BPS dipasangkan dengan baris ref_provinces (yang belum dihapus) berdasarkan nama,
karena id lama belum tentu sama dengan kode BPS (contoh: di DB id 92 = Papua Tengah, padahal
di BPS 92 = Papua Barat Daya). Untuk setiap pasangan:
    id = code = kode_bps, code_dagri = kode_dagri
dan kalau id berubah, id lama disimpan di old_id (kalau old_id masih kosong). Perubahan id
ikut ter-cascade ke tabel yang punya foreign key ke ref_provinces (ON UPDATE CASCADE).
Provinsi BPS yang tidak ada di DB di-insert. Baris DB yang tidak ada di BPS dibiarkan.
Sebelum commit dicek setiap provinsi BPS ada di DB (aktif) dengan id, code, dan code_dagri
yang sesuai; kalau ada yang belum, transaksi dibatalkan.

id/code yang saling bertukar (92 -> 96, 96 -> 92) ditangani dengan memindahkan baris yang
berubah ke id sementara "tmp_{id}" dulu, lalu ke id final. Semua dijalankan dalam satu
transaksi: kalau ada yang gagal, semuanya di-rollback.

Rencana perubahan disimpan ke extracted/province/{timestamp}_sync_province.json.

Koneksi database dibaca dari .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.

Contoh:
    python bps/province.py --dry-run
    python bps/province.py
    python bps/province.py --periode 2025_2.2025
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

import config  # noqa: E402  memuat .env saat di-import
from utils.query import PROVINCE_TABLE, connect  # noqa: E402
from utils.utility import (  # noqa: E402
    OUTPUT_DIR,
    PAYLOAD_DIR,
    build_url,
    fetch,
    get_timestamp,
    log,
    write_json,
)

DEFAULT_PERIODE = config.DEFAULT_PERIODE_BPS
BPS_PROVINCE_DIR = OUTPUT_DIR / "bps" / "province"
SYNC_PAYLOAD_DIR = PAYLOAD_DIR / "bps" / "province"


def fetch_bps_provinces(periode: str) -> list[dict]:
    """GET daftar provinsi BPS: [{kode_bps, nama_bps, kode_dagri, nama_dagri}, ...]."""
    url = build_url("provinsi", "0", periode)
    log(f"GET {url}")
    try:
        status, body = fetch(url)
    except (HTTPError, URLError) as e:
        raise SystemExit(f"Gagal ambil data BPS: {e}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise SystemExit(f"Response BPS bukan JSON (status {status})")
    path = write_json(
        BPS_PROVINCE_DIR / f"{get_timestamp()}_provinsi_{periode}.json", data
    )
    if not isinstance(data, list) or not data:
        raise SystemExit(f"Response BPS kosong, tersimpan di {path}")
    log(f"  status {status}, {len(data)} provinsi, tersimpan di {path}")
    return data


def normalize_name(name: str) -> str:
    """Samakan penulisan nama: "DI YOGYAKARTA" ~ "DI Yogyakarta", "D.K.I. Jakarta" ~ "DKI Jakarta"."""
    name = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return " ".join(name.split())


def fetch_db_provinces(conn) -> list[dict]:
    """Semua baris ref_provinces, termasuk yang sudah dihapus (untuk cek bentrok id/code)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, name, code, code_dagri, deleted_at IS NOT NULL "
            f"FROM {PROVINCE_TABLE} ORDER BY id"
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "code": r[2],
                "code_dagri": r[3],
                "deleted": r[4],
            }
            for r in cur.fetchall()
        ]


def build_plan(bps: list[dict], db_rows: list[dict]) -> dict:
    """Pasangkan provinsi BPS dengan DB berdasarkan nama dan susun daftar update/insert."""
    by_name: dict[str, dict] = {}
    for row in db_rows:
        if not row["deleted"]:
            by_name.setdefault(normalize_name(row["name"]), row)

    updates, inserts, unchanged = [], [], []
    matched_ids: set[str] = set()
    for item in bps:
        kode, kode_dagri = item["kode_bps"], item.get("kode_dagri") or None
        row = by_name.get(normalize_name(item["nama_bps"]))
        if not row or row["id"] in matched_ids:
            inserts.append(
                {
                    "id": kode,
                    "code": kode,
                    "code_dagri": kode_dagri,
                    "name": item["nama_bps"].title(),
                }
            )
            continue
        matched_ids.add(row["id"])
        if (row["id"], row["code"], row["code_dagri"]) == (kode, kode, kode_dagri):
            unchanged.append(row["id"])
            continue
        updates.append(
            {
                "name": row["name"],
                "old_id": row["id"],
                "old_code": row["code"],
                "old_code_dagri": row["code_dagri"],
                "id": kode,
                "code": kode,
                "code_dagri": kode_dagri,
            }
        )

    # Baris yang tidak ikut disinkronkan tidak boleh menempati id/code tujuan.
    targets = {u["id"] for u in updates} | {i["id"] for i in inserts}
    conflicts = [
        row
        for row in db_rows
        if row["id"] not in matched_ids
        and (row["id"] in targets or row["code"] in targets)
    ]
    leftovers = [
        row for row in db_rows if row["id"] not in matched_ids and not row["deleted"]
    ]
    return {
        "updates": updates,
        "inserts": inserts,
        "unchanged": unchanged,
        "conflicts": conflicts,
        "leftovers": leftovers,
    }


def missing_provinces(cur, bps: list[dict]) -> list[str]:
    """Kode BPS yang belum ada di DB sebagai baris aktif dengan id = code = kode_bps dan code_dagri sesuai."""
    cur.execute(
        f"SELECT id, code, code_dagri FROM {PROVINCE_TABLE} WHERE deleted_at IS NULL"
    )
    existing = set(cur.fetchall())
    return [
        item["kode_bps"]
        for item in bps
        if (item["kode_bps"], item["kode_bps"], item.get("kode_dagri") or None)
        not in existing
    ]


def apply_plan(conn, plan: dict, bps: list[dict]) -> None:
    """Jalankan update + insert dalam satu transaksi, lalu pastikan semua provinsi BPS ada di DB."""
    moved = [
        u
        for u in plan["updates"]
        if u["old_id"] != u["id"] or u["old_code"] != u["code"]
    ]
    with conn.cursor() as cur:
        if moved:
            # Pindah ke id/code sementara dulu supaya id yang saling bertukar tidak bentrok.
            cur.execute(
                f"UPDATE {PROVINCE_TABLE} SET id = 'tmp_' || id, code = 'tmp_' || code "
                "WHERE id = ANY(%s)",
                ([u["old_id"] for u in moved],),
            )
        if plan["updates"]:
            moved_ids = {u["old_id"] for u in moved}
            rows = [
                (
                    f"tmp_{u['old_id']}" if u["old_id"] in moved_ids else u["old_id"],
                    u["old_id"],
                    u["id"],
                    u["code"],
                    u["code_dagri"],
                )
                for u in plan["updates"]
            ]
            execute_values(
                cur,
                f"""
                UPDATE {PROVINCE_TABLE} AS r
                SET id = d.id,
                    code = d.code,
                    code_dagri = d.code_dagri,
                    is_fix = true,
                    old_id = CASE WHEN d.old_id <> d.id THEN COALESCE(r.old_id, d.old_id)
                                  ELSE r.old_id END,
                    updated_at = now()
                FROM (VALUES %s) AS d (current_id, old_id, id, code, code_dagri)
                WHERE r.id = d.current_id
                """,
                rows,
                page_size=len(rows),
            )
            if cur.rowcount != len(rows):
                raise RuntimeError(
                    f"{cur.rowcount}/{len(rows)} baris terupdate, transaksi dibatalkan"
                )
        if plan["inserts"]:
            execute_values(
                cur,
                f"INSERT INTO {PROVINCE_TABLE} (id, code, code_dagri, name, is_true) VALUES %s",
                [
                    (i["id"], i["code"], i["code_dagri"], i["name"], True)
                    for i in plan["inserts"]
                ],
            )
        missing = missing_provinces(cur, bps)
        if missing:
            raise RuntimeError(
                f"provinsi BPS belum ada/sesuai di DB: {', '.join(missing)}; transaksi dibatalkan"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--periode",
        default=DEFAULT_PERIODE,
        help=f"periode_merge BPS (default: {DEFAULT_PERIODE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tampilkan dan simpan rencana perubahan saja, tanpa mengubah DB",
    )
    args = parser.parse_args()

    bps = fetch_bps_provinces(args.periode)
    conn = connect()
    try:
        db_rows = fetch_db_provinces(conn)
        plan = build_plan(bps, db_rows)
        path = write_json(
            SYNC_PAYLOAD_DIR / f"{get_timestamp()}_sync_province.json", plan
        )

        for u in plan["updates"]:
            log(
                f"[UPDATE] {u['name']}: id {u['old_id']} -> {u['id']}, "
                f"code {u['old_code']} -> {u['code']}, "
                f"code_dagri {u['old_code_dagri']} -> {u['code_dagri']}"
            )
        for i in plan["inserts"]:
            log(f"[INSERT] {i['id']} {i['name']} (code_dagri {i['code_dagri']})")
        for row in plan["leftovers"]:
            log(f"[SKIP] {row['id']} {row['name']}: tidak ada di BPS, dibiarkan")
        log(
            f"{len(plan['updates'])} update, {len(plan['inserts'])} insert, "
            f"{len(plan['unchanged'])} sudah sesuai; "
            f"rencana tersimpan di {path}"
        )

        if plan["conflicts"]:
            for row in plan["conflicts"]:
                log(
                    f"[CONFLICT] {row['id']} {row['name']} (code {row['code']}"
                    f"{', terhapus' if row['deleted'] else ''}) menempati id/code tujuan"
                )
            log("Dibatalkan: bereskan baris yang bentrok dulu, database tidak diubah")
            return 1
        if args.dry_run:
            log("Dry run, database tidak diubah")
            return 0
        if not plan["updates"] and not plan["inserts"]:
            log("Tidak ada perubahan")
            return 0

        try:
            apply_plan(conn, plan, bps)
            conn.commit()
        except (psycopg2.Error, RuntimeError) as e:
            conn.rollback()
            log(f"GAGAL, semua perubahan di-rollback: {e}")
            return 1
        log(
            f"Selesai: {PROVINCE_TABLE} sudah sinkron dengan BPS periode {args.periode}, "
            f"{len(bps)} provinsi BPS ada di DB"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
