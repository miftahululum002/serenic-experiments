"""Sinkronkan tabel ref_districts dengan daftar kecamatan BPS (sig.bps.go.id).

Dijalankan per kabupaten/kota. Kabupaten/kota diambil dari --regency, atau kalau tidak diisi,
semua kabupaten/kota aktif di ref_regencies yang sudah is_fix (hasil bps/regency.py). Per
kabupaten/kota:
  1. GET https://sig.bps.go.id/rest-bridging/getwilayah?level=kecamatan&parent={kode_kabupaten}&periode_merge={periode}
     dan response disimpan ke output/bps/district/{timestamp}_kecamatan_{kode_kabupaten}_{periode}.json;
  2. susun rencana perubahan, simpan ke extracted/bps/district/{timestamp}_sync_district_{kode_kabupaten}.json;
  3. update/insert ke DB lalu commit.
Kabupaten/kota yang gagal (bentrok, error DB, data BPS tidak didapat) di-rollback dan dilewati,
lalu dicatat di ringkasan akhir; kabupaten/kota lain tetap jalan.

Kecamatan BPS dipasangkan dengan baris ref_districts (yang belum dihapus) secara berurutan:
  1. nama sama (nama_bps atau nama_dagri) di kabupaten/kota yang sama; nama kecamatan banyak
     yang kembar antar kabupaten, jadi tidak dicocokkan lintas kabupaten;
  2. code_dagri sama;
  3. nama paling mirip di kabupaten/kota yang sama, asal kemiripannya >= --min-similarity.
Untuk setiap pasangan:
    id = code = kode_bps, code_dagri = kode_dagri, regency_id = kode kabupaten BPS, is_fix = true
dan kalau id berubah, id lama disimpan di old_id (kalau old_id masih kosong). Perubahan id/code
ikut ter-cascade ke tabel yang punya foreign key ke ref_districts (ON UPDATE CASCADE).
kode_dagri BPS yang nol di belakangnya hilang (11.01.1) dinormalisasi jadi 11.01.10.

Kecamatan yang id, code, code_dagri, dan regency_id-nya sudah sesuai hanya di-set
is_fix = true. Kecamatan BPS yang tidak ada di DB di-insert. Baris DB yang tidak ada di BPS
tidak diubah datanya, hanya kolom notes diisi "Tidak ada di data BPS periode {periode}"; kalau
baris itu belakangan cocok dengan BPS, catatan tersebut dihapus lagi. Sebelum commit dicek setiap kecamatan BPS ada di DB (aktif) dengan id, code,
code_dagri, dan regency_id yang sesuai; kalau ada yang belum, transaksi dibatalkan.

id/code yang saling bertukar ditangani dengan memindahkan baris yang berubah ke id sementara
"tmp_{id}" dulu, lalu ke id final. Satu kabupaten/kota = satu transaksi.

Data BPS yang salah dikoreksi lewat --exception-file (default exception-rules/district.csv),
CSV dengan kolom code,code_dagri,name. Kecamatan BPS dengan kode_bps = code dipakai satu
entri saja, dengan kode_dagri dan nama diganti nilai dari file itu (contoh: BPS mengembalikan
9105120 PRAFI dua kali, salah satunya dengan kode_dagri milik Warmare). kode_bps yang muncul
lebih dari sekali tapi tidak ada di file pengecualian membuat kabupaten/kota itu gagal, supaya
ketahuan sejak dry run. code_dagri yang dikosongkan di file berarti kecamatan itu belum punya
kode Kemendagri (code_dagri = NULL), contoh kecamatan pemekaran 7210121 Sigi Kota.

Request ke BPS yang kena rate limit (429), error 5xx, gagal koneksi, atau response-nya bukan
JSON dicoba ulang maksimal --max-retries kali, dengan jeda mengikuti header Retry-After atau
--retry-delay x 2^n (10, 20, 40, ... detik). Antar request diberi jeda --delay detik.

Koneksi database dibaca dari .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.

Contoh:
    python bps/district.py --dry-run
    python bps/district.py
    python bps/district.py --regency 9203 --regency 9204 --dry-run
"""

import argparse
import csv
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

from bps.province import DEFAULT_PERIODE  # noqa: E402
from utils.query import DISTRICT_TABLE, REGENCY_TABLE, connect  # noqa: E402
from utils.utility import (  # noqa: E402
    BASE_DIR,
    OUTPUT_DIR,
    PAYLOAD_DIR,
    build_url,
    fetch_json_with_retry,
    get_timestamp,
    log,
    write_json,
)

BPS_DISTRICT_DIR = OUTPUT_DIR / "bps" / "district"
EXCEPTION_FILE = BASE_DIR / "exception-rules" / "district.csv"
SYNC_PAYLOAD_DIR = PAYLOAD_DIR / "bps" / "district"
KEYS = ("id", "code", "code_dagri", "regency_id")
STAT_KEYS = ("updates", "inserts", "unchanged", "fixes", "leftovers")
NOTE_PREFIX = "Tidak ada di data BPS"


def fetch_bps_districts(
    regency: str, periode: str, max_retries: int = 5, retry_delay: float = 10
) -> list[dict]:
    """GET daftar kecamatan BPS satu kabupaten/kota: [{kode_bps, nama_bps, kode_dagri, nama_dagri}, ...]."""
    url = build_url("kecamatan", regency, periode)
    log(f"GET {url}")
    try:
        status, data = fetch_json_with_retry(url, max_retries, retry_delay)
    except RuntimeError as e:
        raise RuntimeError(f"gagal ambil data BPS: {e}")
    path = write_json(
        BPS_DISTRICT_DIR / f"{get_timestamp()}_kecamatan_{regency}_{periode}.json", data
    )
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"response BPS kosong, tersimpan di {path}")
    log(f"  status {status}, {len(data)} kecamatan, tersimpan di {path}")
    return data


def normalize_name(name: str) -> str:
    """Samakan penulisan nama: "KEC. Sorong-Barat" ~ "Sorong Barat"."""
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return re.sub(r"^(kecamatan|kec|distrik) ", "", " ".join(name.split()))


def bps_names(item: dict) -> list[str]:
    """Nama BPS dan nama Dagri yang sudah dinormalisasi (bisa beda ejaan: METEMANI / MATEMANI)."""
    names = [normalize_name(item[k]) for k in ("nama_bps", "nama_dagri") if item.get(k)]
    return list(dict.fromkeys(names))


def normalize_code_dagri(code: str | None) -> str | None:
    """Kembalikan nol yang hilang di segmen kecamatan: 11.01.1 -> 11.01.10."""
    if not code:
        return None
    code = code.strip()
    return code + "0" if re.fullmatch(r"\d{2}\.\d{2}\.\d", code) else code


def fetch_db_districts(conn) -> list[dict]:
    """Semua baris ref_districts, termasuk yang sudah dihapus (untuk cek bentrok id/code)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, name, regency_id, code, code_dagri, deleted_at IS NOT NULL, "
            "is_fix IS TRUE, notes "
            f"FROM {DISTRICT_TABLE} ORDER BY id"
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "regency_id": r[2],
                "code": r[3],
                "code_dagri": r[4],
                "deleted": r[5],
                "is_fix": r[6],
                "notes": r[7],
            }
            for r in cur.fetchall()
        ]


def target(item: dict) -> dict:
    """Nilai akhir satu kecamatan BPS di DB."""
    return {
        "id": item["kode_bps"],
        "code": item["kode_bps"],
        "code_dagri": normalize_code_dagri(item.get("kode_dagri")),
        "regency_id": item["regency_id"],
    }


def build_plan(
    bps: list[dict], db_rows: list[dict], min_similarity: float, periode: str
) -> dict:
    """Pasangkan kecamatan BPS dengan DB dan susun daftar update/insert/catatan."""
    active = [row for row in db_rows if not row["deleted"]]
    matched: dict[str, dict] = {}  # kode_bps -> baris DB
    used: set[str] = set()

    def take(item: dict, row: dict | None, how: str) -> None:
        if row and row["id"] not in used:
            matched[item["kode_bps"]] = {**row, "match": how}
            used.add(row["id"])

    by_regency_name: dict[tuple[str, str], dict] = {}
    for row in active:
        by_regency_name.setdefault(
            (row["regency_id"], normalize_name(row["name"])), row
        )
    for item in bps:
        for name in bps_names(item):
            row = by_regency_name.get((item["regency_id"], name))
            if row and row["id"] not in used:
                take(item, row, "nama")
                break

    by_code_dagri: dict[str, list[dict]] = {}
    for row in active:
        if row["code_dagri"]:
            by_code_dagri.setdefault(row["code_dagri"], []).append(row)
    for item in bps:
        if item["kode_bps"] in matched:
            continue
        rows = [
            r
            for r in by_code_dagri.get(target(item)["code_dagri"], [])
            if r["id"] not in used
        ]
        rows.sort(key=lambda r: r["regency_id"] != item["regency_id"])
        take(item, rows[0] if rows else None, "code_dagri")

    for item in bps:
        if item["kode_bps"] in matched:
            continue
        candidates = [
            (
                max(
                    SequenceMatcher(None, name, normalize_name(row["name"])).ratio()
                    for name in bps_names(item)
                ),
                row,
            )
            for row in active
            if row["id"] not in used and row["regency_id"] == item["regency_id"]
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
            inserts.append({**new, "name": item["nama_bps"].title()})
            continue
        old = {k: row[k] for k in KEYS}
        if old == new:
            unchanged.append(row["id"])
            if not row["is_fix"]:
                fixes.append(row["id"])
            continue
        updates.append(
            {
                "name": row["name"],
                "bps_name": item["nama_bps"],
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
    regencies = {item["regency_id"] for item in bps}
    leftovers = [
        row
        for row in active
        if row["id"] not in used and row["regency_id"] in regencies
    ]
    note = f"{NOTE_PREFIX} periode {periode}"
    return {
        "updates": updates,
        "inserts": inserts,
        "unchanged": unchanged,
        "fixes": fixes,
        "conflicts": conflicts,
        "leftovers": leftovers,
        "note": note,
        # Baris yang tidak ada di BPS dan catatannya belum terisi.
        "notes": [row["id"] for row in leftovers if row["notes"] != note],
        # Baris yang sekarang cocok dengan BPS tapi masih membawa catatan "tidak ada di BPS".
        "clear_notes": [
            row["id"]
            for row in matched.values()
            if (row["notes"] or "").startswith(NOTE_PREFIX)
        ],
    }


def missing_districts(cur, bps: list[dict]) -> list[str]:
    """Kode BPS yang belum ada di DB sebagai baris aktif dengan id, code, code_dagri, regency_id sesuai."""
    cur.execute(
        f"SELECT id, code, code_dagri, regency_id FROM {DISTRICT_TABLE} "
        "WHERE deleted_at IS NULL"
    )
    existing = set(cur.fetchall())
    return [
        item["kode_bps"] for item in bps if tuple(target(item).values()) not in existing
    ]


def apply_plan(conn, plan: dict, bps: list[dict]) -> None:
    """Jalankan update + insert dalam satu transaksi, lalu pastikan semua kecamatan BPS ada di DB."""
    moved = [
        u
        for u in plan["updates"]
        if u["old_id"] != u["id"] or u["old_code"] != u["code"]
    ]
    with conn.cursor() as cur:
        if moved:
            # Pindah ke id/code sementara dulu supaya id yang saling bertukar tidak bentrok.
            cur.execute(
                f"UPDATE {DISTRICT_TABLE} SET id = 'tmp_' || id, code = 'tmp_' || code "
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
                    u["regency_id"],
                )
                for u in plan["updates"]
            ]
            execute_values(
                cur,
                f"""
                UPDATE {DISTRICT_TABLE} AS r
                SET id = d.id,
                    code = d.code,
                    code_dagri = d.code_dagri,
                    regency_id = d.regency_id,
                    is_fix = true,
                    old_id = CASE WHEN d.old_id <> d.id THEN COALESCE(r.old_id, d.old_id)
                                  ELSE r.old_id END,
                    updated_at = now()
                FROM (VALUES %s) AS d (current_id, old_id, id, code, code_dagri, regency_id)
                WHERE r.id = d.current_id
                """,
                rows,
                page_size=1000,
            )
        if plan["inserts"]:
            execute_values(
                cur,
                f"INSERT INTO {DISTRICT_TABLE} "
                "(id, code, code_dagri, regency_id, name, is_fix) VALUES %s",
                [
                    (
                        i["id"],
                        i["code"],
                        i["code_dagri"],
                        i["regency_id"],
                        i["name"],
                        True,
                    )
                    for i in plan["inserts"]
                ],
                page_size=1000,
            )
        if plan["fixes"]:
            cur.execute(
                f"UPDATE {DISTRICT_TABLE} SET is_fix = true, updated_at = now() "
                "WHERE id = ANY(%s)",
                (plan["fixes"],),
            )
        if plan["clear_notes"]:
            # id lama sudah berubah ke kode BPS, jadi dicari lewat id atau old_id.
            cur.execute(
                f"UPDATE {DISTRICT_TABLE} SET notes = NULL, updated_at = now() "
                "WHERE (id = ANY(%s) OR old_id = ANY(%s)) AND notes LIKE %s",
                (plan["clear_notes"], plan["clear_notes"], f"{NOTE_PREFIX}%"),
            )
        if plan["notes"]:
            cur.execute(
                f"UPDATE {DISTRICT_TABLE} SET notes = %s, updated_at = now() "
                "WHERE id = ANY(%s)",
                (plan["note"], plan["notes"]),
            )
        cur.execute(f"SELECT count(*) FROM {DISTRICT_TABLE} WHERE id LIKE 'tmp\\_%%'")
        leftover_tmp = cur.fetchone()[0]
        if leftover_tmp:
            raise RuntimeError(
                f"{leftover_tmp} baris masih ber-id sementara tmp_, transaksi dibatalkan"
            )
        missing = missing_districts(cur, bps)
        if missing:
            raise RuntimeError(
                f"{len(missing)} kecamatan BPS belum ada/sesuai di DB: "
                f"{', '.join(missing[:20])}{' ...' if len(missing) > 20 else ''}; "
                "transaksi dibatalkan"
            )


def fetch_regencies(conn, regencies: list[str] | None) -> list[str]:
    """Kode kabupaten/kota yang diproses: dari --regency, atau semua yang aktif dan is_fix di DB."""
    with conn.cursor() as cur:
        if regencies:
            regencies = list(dict.fromkeys(regencies))
            cur.execute(
                f"SELECT id FROM {REGENCY_TABLE} WHERE id = ANY(%s) AND deleted_at IS NULL",
                (regencies,),
            )
            missing = set(regencies) - {r[0] for r in cur.fetchall()}
            if missing:
                raise SystemExit(
                    f"Kabupaten/kota {', '.join(sorted(missing))} belum ada di {REGENCY_TABLE}; "
                    "jalankan bps/regency.py dulu"
                )
            return regencies
        cur.execute(
            f"SELECT id FROM {REGENCY_TABLE} "
            "WHERE deleted_at IS NULL AND is_fix IS TRUE ORDER BY id"
        )
        return [r[0] for r in cur.fetchall()]


def log_plan(plan: dict) -> None:
    total = len(plan["updates"]) + len(plan["inserts"])
    for n, u in enumerate(plan["updates"], 1):
        changes = [
            f"{k} {u[f'old_{k}']} -> {u[k]}" for k in KEYS if u[f"old_{k}"] != u[k]
        ]
        log(
            f"  [{n}/{total}] [UPDATE] {u['name']} ({u['match']}: {u['bps_name']}): "
            f"{', '.join(changes)}"
        )
    for n, i in enumerate(plan["inserts"], len(plan["updates"]) + 1):
        log(
            f"  [{n}/{total}] [INSERT] {i['id']} {i['name']} (code_dagri {i['code_dagri']})"
        )
    for row in plan["leftovers"]:
        noted = ", notes diisi" if row["id"] in plan["notes"] else ", notes sudah terisi"
        log(f"  [SKIP] {row['id']} {row['name']}: tidak ada di BPS{noted}")
    for row_id in plan["clear_notes"]:
        log(f"  [NOTES] {row_id}: sudah ada di BPS, catatan dihapus")
    for row in plan["conflicts"]:
        log(
            f"  [CONFLICT] {row['id']} {row['name']} (code {row['code']}"
            f"{', terhapus' if row['deleted'] else ''}) menempati id/code tujuan"
        )


def read_exception_rules(path: Path) -> dict[str, dict]:
    """Baca koreksi data BPS: {code: {code_dagri, name}}; file tidak ada berarti tanpa koreksi."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(f)
        ]
    return {
        r["code"]: {"code_dagri": r.get("code_dagri") or None, "name": r.get("name") or None}
        for r in rows
        if r.get("code")
    }


def apply_exception_rules(bps: list[dict], rules: dict[str, dict]) -> list[dict]:
    """Koreksi data BPS dengan aturan pengecualian dan pastikan kode_bps tidak dobel."""
    result: list[dict] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in bps:
        kode = item["kode_bps"]
        if kode in seen:
            if kode not in rules:
                duplicates.add(kode)
            continue
        seen.add(kode)
        rule = rules.get(kode)
        if rule:
            fixed = {**item}
            # code_dagri kosong di aturan berarti kecamatan ini belum punya kode Kemendagri.
            fixed["kode_dagri"] = rule["code_dagri"]
            if rule["name"]:
                fixed["nama_bps"] = fixed["nama_dagri"] = rule["name"].upper()
            entries = [i for i in bps if i["kode_bps"] == kode]
            if len(entries) > 1 or fixed != item:
                found = "; ".join(f"{i['nama_bps']} {i['kode_dagri']}" for i in entries)
                log(
                    f"  [RULE] {kode}: {len(entries)} entri BPS ({found}) "
                    f"-> {fixed['nama_bps']} {fixed['kode_dagri']}"
                )
            item = fixed
        result.append(item)
    if duplicates:
        raise RuntimeError(
            f"kode_bps dobel di data BPS: {', '.join(sorted(duplicates))}; "
            f"tambahkan koreksinya ke {EXCEPTION_FILE.relative_to(BASE_DIR)}"
        )
    return result


def sync_regency(regency: str, args, rules: dict[str, dict]) -> dict:
    """Fetch, cocokkan, dan (kalau bukan dry run) simpan kecamatan satu kabupaten/kota.

    Return statistik {bps, updates, inserts, unchanged, fixes, leftovers}. Raise RuntimeError
    kalau gagal; perubahan kabupaten/kota ini di-rollback.
    """
    bps = [
        {**item, "regency_id": regency}
        for item in fetch_bps_districts(
            regency, args.periode, args.max_retries, args.retry_delay
        )
    ]
    bps = apply_exception_rules(bps, rules)
    # Koneksi dibuka per kabupaten/kota supaya tidak ada koneksi idle selama request ke BPS.
    conn = connect()
    try:
        plan = build_plan(
            bps, fetch_db_districts(conn), args.min_similarity, args.periode
        )
        path = write_json(
            SYNC_PAYLOAD_DIR / f"{get_timestamp()}_sync_district_{regency}.json", plan
        )
        log_plan(plan)
        stats = {"bps": len(bps), **{k: len(plan[k]) for k in STAT_KEYS}}
        log(
            f"  {len(bps)} kecamatan BPS: {stats['updates']} update, {stats['inserts']} insert, "
            f"{stats['unchanged']} sudah sesuai ({stats['fixes']} di-set is_fix), "
            f"{stats['leftovers']} tidak ada di BPS; rencana tersimpan di {path}"
        )
        if plan["conflicts"]:
            raise RuntimeError(
                f"{len(plan['conflicts'])} baris DB bentrok dengan id/code tujuan"
            )
        changes = ("updates", "inserts", "fixes", "notes", "clear_notes")
        if args.dry_run or not any(plan[k] for k in changes):
            return stats
        try:
            apply_plan(conn, plan, bps)
            conn.commit()
        except (psycopg2.Error, RuntimeError) as e:
            conn.rollback()
            raise RuntimeError(f"perubahan di-rollback: {e}") from e
        log(f"  tersimpan ke {DISTRICT_TABLE}")
        return stats
    finally:
        conn.close()


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
        "--regency",
        action="append",
        help="Kode kabupaten/kota BPS yang disinkronkan, bisa diulang "
        f"(default: semua kabupaten/kota aktif dan is_fix di {REGENCY_TABLE})",
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
        default=0.5,
        help="Jeda antar request ke BPS dalam detik (default: 0.5)",
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
    parser.add_argument(
        "--exception-file",
        type=Path,
        default=EXCEPTION_FILE,
        help="CSV koreksi data BPS dengan kolom code,code_dagri,name "
        f"(default: {EXCEPTION_FILE.relative_to(BASE_DIR)})",
    )
    args = parser.parse_args()
    rules = read_exception_rules(args.exception_file)
    if rules:
        log(f"{len(rules)} aturan pengecualian dari {args.exception_file}")

    conn = connect()
    try:
        regencies = fetch_regencies(conn, args.regency)
    finally:
        conn.close()
    if not regencies:
        log(f"Tidak ada kabupaten/kota yang diproses di {REGENCY_TABLE}")
        return 1
    log(f"{len(regencies)} kabupaten/kota akan diproses")

    totals = dict.fromkeys(("bps", *STAT_KEYS), 0)
    failed: list[str] = []
    for i, regency in enumerate(regencies):
        if i and args.delay:
            time.sleep(args.delay)
        print()
        log(f"== [{i + 1}/{len(regencies)}] kabupaten/kota {regency}")
        try:
            stats = sync_regency(regency, args, rules)
        except RuntimeError as e:
            log(f"  [FAIL] kabupaten/kota {regency}: {e}")
            failed.append(regency)
            continue
        for k, v in stats.items():
            totals[k] += v

    print()
    log(
        f"Selesai{' (dry run, database tidak diubah)' if args.dry_run else ''}: "
        f"{len(regencies) - len(failed)}/{len(regencies)} kabupaten/kota berhasil, "
        f"{totals['bps']} kecamatan BPS: {totals['updates']} update, {totals['inserts']} insert, "
        f"{totals['unchanged']} sudah sesuai ({totals['fixes']} di-set is_fix), "
        f"{totals['leftovers']} tidak ada di BPS"
    )
    if failed:
        log(f"Kabupaten/kota gagal ({len(failed)}): {', '.join(failed)}")
        log("Ulangi dengan --regency untuk kabupaten/kota tersebut setelah dibereskan")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
