"""Sinkronkan tabel ref_regencies dengan daftar kabupaten/kota BPS (sig.bps.go.id).

Daftar provinsi diambil dari BPS (level=provinsi), lalu per provinsi:
https://sig.bps.go.id/rest-bridging/getwilayah?level=kabupaten&parent={kode_provinsi}&periode_merge={periode}
Response disimpan ke output/bps/regency/{timestamp}_kabupaten_{kode_provinsi}_{periode}.json.
Jalankan bps/province.py dulu supaya provinsi di DB sudah sesuai BPS.

Kabupaten/kota BPS dipasangkan dengan baris ref_regencies (yang belum dihapus) secara
berurutan:
  1. nama sama (nama_dagri "KABUPATEN SORONG" ~ "Kab. Sorong", "KOTA SORONG" ~ "Kota Sorong");
  2. code_dagri sama;
  3. nama paling mirip di provinsi yang sama, asal kemiripannya >= --min-similarity.
Untuk setiap pasangan:
    id = code = kode_bps, code_dagri = kode_dagri, province_id = kode provinsi BPS, is_fix = true
dan kalau id berubah, id lama disimpan di old_id (kalau old_id masih kosong). Perubahan id
ikut ter-cascade ke tabel yang punya foreign key ke ref_regencies (ON UPDATE CASCADE).
kode_dagri BPS yang nol di belakangnya hilang (11.1) dinormalisasi jadi 11.10.

Kabupaten/kota yang id, code, code_dagri, dan province_id-nya sudah sesuai hanya di-set
is_fix = true. Kabupaten/kota BPS yang tidak ada di DB di-insert. Baris DB yang tidak ada di BPS dibiarkan.
Sebelum commit dicek setiap kabupaten/kota BPS ada di DB (aktif) dengan id, code, code_dagri,
dan province_id yang sesuai; kalau ada yang belum, transaksi dibatalkan.

id/code yang saling bertukar ditangani dengan memindahkan baris yang berubah ke id sementara
"tmp_{id}" dulu, lalu ke id final. Semua dijalankan dalam satu transaksi: kalau ada yang
gagal, semuanya di-rollback.

Request ke BPS yang kena rate limit (429), error 5xx, gagal koneksi, atau response-nya bukan
JSON dicoba ulang maksimal --max-retries kali, dengan jeda mengikuti header Retry-After atau
--retry-delay x 2^n (10, 20, 40, ... detik).

Rencana perubahan disimpan ke extracted/bps/regency/{timestamp}_sync_regency.json.

Koneksi database dibaca dari .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.

Contoh:
    python bps/regency.py --dry-run
    python bps/regency.py
    python bps/regency.py --province 92 --province 94 --dry-run
"""

import argparse
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

from bps.province import DEFAULT_PERIODE, fetch_bps_provinces  # noqa: E402
from utils.query import PROVINCE_TABLE, REGENCY_TABLE, connect  # noqa: E402
from utils.utility import (  # noqa: E402
    OUTPUT_DIR,
    PAYLOAD_DIR,
    build_url,
    fetch_json_with_retry,
    get_timestamp,
    log,
    write_json,
)

BPS_REGENCY_DIR = OUTPUT_DIR / "bps" / "regency"
SYNC_PAYLOAD_DIR = PAYLOAD_DIR / "bps" / "regency"


def fetch_bps_regencies(
    province: str, periode: str, max_retries: int = 5, retry_delay: float = 10
) -> list[dict]:
    """GET daftar kabupaten/kota BPS satu provinsi: [{kode_bps, nama_bps, kode_dagri, nama_dagri}, ...]."""
    url = build_url("kabupaten", province, periode)
    log(f"GET {url}")
    try:
        status, data = fetch_json_with_retry(url, max_retries, retry_delay)
    except RuntimeError as e:
        raise SystemExit(f"Gagal ambil data BPS provinsi {province}: {e}")
    path = write_json(
        BPS_REGENCY_DIR / f"{get_timestamp()}_kabupaten_{province}_{periode}.json", data
    )
    if not isinstance(data, list) or not data:
        raise SystemExit(f"Response BPS provinsi {province} kosong, tersimpan di {path}")
    log(f"  status {status}, {len(data)} kabupaten/kota, tersimpan di {path}")
    return data


def normalize_name(name: str) -> str:
    """Samakan penulisan nama: "KABUPATEN SORONG" ~ "Kab. Sorong", "KOTA SORONG" ~ "Kota Sorong"."""
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    name = " ".join(name.split())
    return re.sub(r"^(kabupaten|kab) ", "kabupaten ", name)


def bps_name(item: dict) -> str:
    """Nama lengkap dengan awalan Kabupaten/Kota; kode 71-99 di belakang adalah kota."""
    if item.get("nama_dagri"):
        return item["nama_dagri"]
    prefix = "KOTA" if int(item["kode_bps"][2:]) >= 71 else "KABUPATEN"
    return f"{prefix} {item['nama_bps']}"


def display_name(item: dict) -> str:
    """Nama untuk baris baru, mengikuti gaya DB: "Kab. Sorong", "Kota Sorong"."""
    name = normalize_name(bps_name(item)).title()
    return re.sub(r"^Kabupaten ", "Kab. ", name)


def normalize_code_dagri(code: str | None) -> str | None:
    """Kembalikan nol yang hilang di segmen kabupaten: 11.1 -> 11.10."""
    if not code:
        return None
    code = code.strip()
    return code + "0" if re.fullmatch(r"\d{2}\.\d", code) else code


def fetch_db_regencies(conn) -> list[dict]:
    """Semua baris ref_regencies, termasuk yang sudah dihapus (untuk cek bentrok id/code)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, name, province_id, code, code_dagri, deleted_at IS NOT NULL, "
            "is_fix IS TRUE "
            f"FROM {REGENCY_TABLE} ORDER BY id"
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "province_id": r[2],
                "code": r[3],
                "code_dagri": r[4],
                "deleted": r[5],
                "is_fix": r[6],
            }
            for r in cur.fetchall()
        ]


def target(item: dict) -> dict:
    """Nilai akhir satu kabupaten/kota BPS di DB."""
    return {
        "id": item["kode_bps"],
        "code": item["kode_bps"],
        "code_dagri": normalize_code_dagri(item.get("kode_dagri")),
        "province_id": item["province_id"],
    }


def build_plan(bps: list[dict], db_rows: list[dict], min_similarity: float) -> dict:
    """Pasangkan kabupaten/kota BPS dengan DB dan susun daftar update/insert."""
    active = [row for row in db_rows if not row["deleted"]]
    matched: dict[str, dict] = {}  # kode_bps -> baris DB
    used: set[str] = set()

    def take(item: dict, row: dict | None, how: str) -> None:
        if row and row["id"] not in used:
            matched[item["kode_bps"]] = {**row, "match": how}
            used.add(row["id"])

    by_name: dict[str, list[dict]] = {}
    for row in active:
        by_name.setdefault(normalize_name(row["name"]), []).append(row)
    for item in bps:
        rows = by_name.get(normalize_name(bps_name(item)), [])
        if len(rows) > 1:  # nama kembar: utamakan yang code_dagri / provinsinya sama
            rows = sorted(
                rows,
                key=lambda r: (
                    r["code_dagri"] != target(item)["code_dagri"],
                    r["province_id"] != item["province_id"],
                ),
            )
        take(item, rows[0] if rows else None, "nama")

    by_code_dagri = {row["code_dagri"]: row for row in active if row["code_dagri"]}
    for item in bps:
        if item["kode_bps"] not in matched:
            take(item, by_code_dagri.get(target(item)["code_dagri"]), "code_dagri")

    for item in bps:
        if item["kode_bps"] in matched:
            continue
        name = normalize_name(bps_name(item))
        candidates = [
            (SequenceMatcher(None, name, normalize_name(row["name"])).ratio(), row)
            for row in active
            if row["id"] not in used and row["province_id"] == item["province_id"]
        ]
        if candidates:
            score, row = max(candidates, key=lambda c: c[0])
            if score >= min_similarity:
                take(item, row, f"mirip {score:.2f}")

    updates, inserts, unchanged, fixes = [], [], [], []
    for item in bps:
        new = target(item)
        row = matched.get(item["kode_bps"])
        if not row:
            inserts.append({**new, "name": display_name(item)})
            continue
        old = {k: row[k] for k in ("id", "code", "code_dagri", "province_id")}
        if old == new:
            unchanged.append(row["id"])
            if not row["is_fix"]:
                fixes.append(row["id"])
            continue
        updates.append(
            {
                "name": row["name"],
                "bps_name": bps_name(item),
                "match": row["match"],
                **{f"old_{k}": v for k, v in old.items()},
                **new,
            }
        )

    # Baris yang tidak ikut disinkronkan tidak boleh menempati id/code tujuan.
    targets = {u["id"] for u in updates} | {i["id"] for i in inserts}
    conflicts = [
        row
        for row in db_rows
        if row["id"] not in used and (row["id"] in targets or row["code"] in targets)
    ]
    provinces = {item["province_id"] for item in bps}
    leftovers = [
        row for row in active if row["id"] not in used and row["province_id"] in provinces
    ]
    return {
        "updates": updates,
        "inserts": inserts,
        "unchanged": unchanged,
        "fixes": fixes,
        "conflicts": conflicts,
        "leftovers": leftovers,
    }


def missing_regencies(cur, bps: list[dict]) -> list[str]:
    """Kode BPS yang belum ada di DB sebagai baris aktif dengan id, code, code_dagri, province_id sesuai."""
    cur.execute(
        f"SELECT id, code, code_dagri, province_id FROM {REGENCY_TABLE} "
        "WHERE deleted_at IS NULL"
    )
    existing = set(cur.fetchall())
    return [
        item["kode_bps"]
        for item in bps
        if tuple(target(item).values()) not in existing
    ]


def apply_plan(conn, plan: dict, bps: list[dict]) -> None:
    """Jalankan update + insert dalam satu transaksi, lalu pastikan semua kabupaten/kota BPS ada di DB."""
    moved = [
        u
        for u in plan["updates"]
        if u["old_id"] != u["id"] or u["old_code"] != u["code"]
    ]
    with conn.cursor() as cur:
        if moved:
            # Pindah ke id/code sementara dulu supaya id yang saling bertukar tidak bentrok.
            cur.execute(
                f"UPDATE {REGENCY_TABLE} SET id = 'tmp_' || id, code = 'tmp_' || code "
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
                    u["province_id"],
                )
                for u in plan["updates"]
            ]
            execute_values(
                cur,
                f"""
                UPDATE {REGENCY_TABLE} AS r
                SET id = d.id,
                    code = d.code,
                    code_dagri = d.code_dagri,
                    province_id = d.province_id,
                    is_fix = true,
                    old_id = CASE WHEN d.old_id <> d.id THEN COALESCE(r.old_id, d.old_id)
                                  ELSE r.old_id END,
                    updated_at = now()
                FROM (VALUES %s) AS d (current_id, old_id, id, code, code_dagri, province_id)
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
                f"INSERT INTO {REGENCY_TABLE} "
                "(id, code, code_dagri, province_id, name, is_fix) VALUES %s",
                [
                    (i["id"], i["code"], i["code_dagri"], i["province_id"], i["name"], True)
                    for i in plan["inserts"]
                ],
            )
        if plan["fixes"]:
            cur.execute(
                f"UPDATE {REGENCY_TABLE} SET is_fix = true, updated_at = now() "
                "WHERE id = ANY(%s)",
                (plan["fixes"],),
            )
        missing = missing_regencies(cur, bps)
        if missing:
            raise RuntimeError(
                f"kabupaten/kota BPS belum ada/sesuai di DB: {', '.join(missing)}; "
                "transaksi dibatalkan"
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
        "--province",
        action="append",
        help="Kode provinsi BPS yang disinkronkan, bisa diulang (default: semua provinsi)",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.85,
        help="Kemiripan nama minimum (0-1) untuk pencocokan nama tidak persis (default: 0.85)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1,
        help="Jeda antar request ke BPS dalam detik (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tampilkan dan simpan rencana perubahan saja, tanpa mengubah DB",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maksimal coba ulang request BPS saat kena rate limit/gangguan (default: 5)",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=10,
        help="Jeda awal coba ulang dalam detik, berlipat 2 tiap percobaan (default: 10)",
    )
    args = parser.parse_args()

    provinces = [p["kode_bps"] for p in fetch_bps_provinces(args.periode, args.max_retries, args.retry_delay)]
    if args.province:
        unknown = set(args.province) - set(provinces)
        if unknown:
            raise SystemExit(f"Kode provinsi tidak ada di BPS: {', '.join(sorted(unknown))}")
        provinces = [p for p in provinces if p in args.province]

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {PROVINCE_TABLE} WHERE id = ANY(%s) AND deleted_at IS NULL",
                (provinces,),
            )
            missing = set(provinces) - {r[0] for r in cur.fetchall()}
        if missing:
            log(
                f"Provinsi {', '.join(sorted(missing))} belum ada di {PROVINCE_TABLE}; "
                "jalankan bps/province.py dulu"
            )
            return 1

        bps: list[dict] = []
        for i, province in enumerate(provinces):
            if i and args.delay:
                time.sleep(args.delay)
            log(f"== [{i + 1}/{len(provinces)}] provinsi {province}")
            bps.extend(
                {**item, "province_id": province}
                for item in fetch_bps_regencies(
                    province, args.periode, args.max_retries, args.retry_delay
                )
            )
            log(f"  total {len(bps)} kabupaten/kota BPS terkumpul")

        plan = build_plan(bps, fetch_db_regencies(conn), args.min_similarity)
        path = write_json(SYNC_PAYLOAD_DIR / f"{get_timestamp()}_sync_regency.json", plan)

        print()
        total = len(plan["updates"]) + len(plan["inserts"])
        for n, u in enumerate(plan["updates"], 1):
            changes = [
                f"{k} {u[f'old_{k}']} -> {u[k]}"
                for k in ("id", "code", "code_dagri", "province_id")
                if u[f"old_{k}"] != u[k]
            ]
            log(f"[{n}/{total}] [UPDATE] {u['name']} ({u['match']}: {u['bps_name']}): {', '.join(changes)}")
        for n, i in enumerate(plan["inserts"], len(plan["updates"]) + 1):
            log(
                f"[{n}/{total}] [INSERT] {i['id']} {i['name']} (provinsi {i['province_id']}, "
                f"code_dagri {i['code_dagri']})"
            )
        for row in plan["leftovers"]:
            log(f"[SKIP] {row['id']} {row['name']}: tidak ada di BPS, dibiarkan")
        log(
            f"{len(bps)} kabupaten/kota BPS: {len(plan['updates'])} update, "
            f"{len(plan['inserts'])} insert, {len(plan['unchanged'])} sudah sesuai "
            f"({len(plan['fixes'])} di-set is_fix); "
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
        if not plan["updates"] and not plan["inserts"] and not plan["fixes"]:
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
            f"Selesai: {REGENCY_TABLE} sudah sinkron dengan BPS periode {args.periode}, "
            f"{len(bps)} kabupaten/kota BPS ada di DB"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
