import os
import time

from utils.utility import BASE_DIR, log, to_float

import psycopg2
from psycopg2.extras import execute_values

VILLAGE_PAYLOAD_DIR = BASE_DIR / "village_payload"
TABLE = "ref_villages"
REQUIRED_DB_ENV = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")


def connect():
    missing = [key for key in REQUIRED_DB_ENV if not os.environ.get(key)]
    if missing:
        raise SystemExit(f"Env belum diisi: {', '.join(missing)}")
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT") or 5432,
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def id_column_type(conn) -> str:
    """Tipe kolom id (varchar/bigint/...) supaya village_id di VALUES di-cast sesuai dan index tetap terpakai."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attname = 'id' AND NOT attisdropped",
            (TABLE,),
        )
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"Kolom id tidak ditemukan di tabel {TABLE}")
    return row[0]


def build_update_sql(id_type: str) -> tuple[str, str]:
    sql = f"""
        UPDATE {TABLE} AS v
        SET postal_code = d.postal_code,
            latitude = d.latitude,
            longitude = d.longitude
        FROM (VALUES %s) AS d (id, postal_code, latitude, longitude)
        WHERE v.id = d.id::{id_type}
    """
    template = "(%s, %s, %s::double precision, %s::double precision)"
    return sql, template


def update_batches(
    conn, rows: list[tuple], batch_size: int, delay: float
) -> tuple[int, int, list[int]]:
    """Jalankan update per batch; batch yang gagal di-rollback dan dilewati. Return (terupdate, tidak ditemukan, batch gagal)."""
    sql, template = build_update_sql(id_column_type(conn))
    total_batches = (len(rows) + batch_size - 1) // batch_size
    updated = not_found = 0
    failed = []

    for index in range(total_batches):
        batch = rows[index * batch_size : (index + 1) * batch_size]
        try:
            with conn.cursor() as cur:
                execute_values(cur, sql, batch, template=template, page_size=len(batch))
                affected = cur.rowcount
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            failed.append(index + 1)
            log(f"Batch {index + 1}/{total_batches} GAGAL: {e}".strip())
            continue

        updated += affected
        not_found += len(batch) - affected
        log(
            f"Batch {index + 1}/{total_batches}: {affected}/{len(batch)} baris terupdate"
        )
        if delay and index + 1 < total_batches:
            time.sleep(delay)

    return updated, not_found, failed


def item_to_row(item: dict) -> tuple | None:
    """Item payload {village_id, postal_code, latitude, longitude} -> tuple VALUES; None jika tanpa village_id."""
    village_id = str(item.get("village_id") or "").strip()
    if not village_id:
        return None
    return (
        village_id,
        item.get("postal_code") or None,
        to_float(item.get("latitude")),
        to_float(item.get("longitude")),
    )


def update_items(
    items: list[dict], batch_size: int = 100, delay: float = 0
) -> tuple[int, int, list[int]]:
    """Update ref_villages dari list item payload dengan koneksi sendiri yang langsung ditutup.

    Koneksi dibuka per pemanggilan supaya tidak ada koneksi idle selama proses fetch yang lama.
    """
    rows = [row for row in map(item_to_row, items) if row]
    if not rows:
        return 0, 0, []
    conn = connect()
    try:
        return update_batches(conn, rows, batch_size, delay)
    finally:
        conn.close()


PROVINCE_TABLE = "ref_provinces"
REGENCY_TABLE = "ref_regencies"
DISTRICT_TABLE = "ref_districts"


def update_region_coordinates(
    conn, table: str, rows: list[tuple], batch_size: int = 100
) -> tuple[int, list[int]]:
    """Bulk update latitude, longitude dari tuple (id, latitude, longitude), commit per batch.

    Batch yang gagal di-rollback dan dilewati. Return (terupdate, nomor batch gagal).
    """
    sql = f"""
        UPDATE {table} AS r
        SET latitude = d.latitude,
            longitude = d.longitude,
            is_fix = true
        FROM (VALUES %s) AS d (id, latitude, longitude)
        WHERE r.id = d.id
    """
    template = "(%s::varchar, %s::numeric, %s::numeric)"
    total_batches = (len(rows) + batch_size - 1) // batch_size
    updated = 0
    failed = []
    for index in range(total_batches):
        batch = rows[index * batch_size : (index + 1) * batch_size]
        try:
            with conn.cursor() as cur:
                execute_values(cur, sql, batch, template=template, page_size=len(batch))
                affected = cur.rowcount
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            failed.append(index + 1)
            log(f"Batch {index + 1}/{total_batches} GAGAL: {e}".strip())
            continue
        updated += affected
        log(
            f"Batch {index + 1}/{total_batches}: {affected}/{len(batch)} baris terupdate"
        )
    return updated, failed
