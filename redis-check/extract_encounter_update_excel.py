import sys
import json
from datetime import datetime
import openpyxl
from config import redis_conn
from constant import DATA_PARSING_AGENT, FILENAME
from utils.utility import get_org_id, get_now, get_timestamp_file
from utils.logger import get_logger

sys.path.insert(0, "/Users/miftahululum002/projects/serenic/serenic_api_service")

logger = get_logger(__name__)

SOURCE_NAMES = [
    "triase_igd", "cppt", "asesmen_awal", "resume_medis",
    "penunjang_lab", "penunjang_radiologi", "laporan_operasi",
    "prosedur_medis_lain", "diagnosis_aktif", "diagnosis_awal",
    "obat", "billing",
]


def to_json_str(value):
    if value is None:
        return ""
    if hasattr(value, "model_dump"):
        obj = value.model_dump()
    elif isinstance(value, (list, tuple)):
        obj = [v.model_dump() if hasattr(v, "model_dump") else v for v in value]
    else:
        obj = value
    return json.dumps(obj, default=str)


def count_items(value):
    if value is None:
        return 0
    if hasattr(value, "root"):
        return len(value.root)
    if isinstance(value, (list, tuple)):
        return len(value)
    return 1


def location_id(location):
    if location is None:
        return "-"
    if isinstance(location, str):
        return location
    return getattr(location, "id", "-")


def extract_from_job(job, org_id):
    try:
        args = job.args
        req = args[0]
        job_org = args[2] if len(args) > 2 else None
        if job_org != org_id:
            return []
        rows = []
        for u in req.updates:
            row = {
                "job_id": job.id,
                "norec": u.norec,
                "noregistrasi": u.noregistrasi,
                "created_at": str(u.created_at),
                "updated_at": str(u.updated_at),
                "location_id": location_id(u.location),
                "data_sources": "|".join(
                    name for name in SOURCE_NAMES
                    if getattr(u.sources, name, None) is not None
                ),
            }
            for name in SOURCE_NAMES:
                value = getattr(u.sources, name, None)
                row[f"{name}_count"] = count_items(value)
                row[f"{name}_json"] = to_json_str(value)
            rows.append(row)
        return rows
    except Exception as e:
        logger.error(f"Gagal decode job {job.id}: {e}")
        return []


def extract_encounter_update(org_id, limit=None):
    queue_key = f"rq:queue:{DATA_PARSING_AGENT}"
    total = redis_conn.llen(queue_key)
    job_ids = redis_conn.lrange(queue_key, 0, total - 1)
    if limit:
        job_ids = job_ids[:limit]

    from rq import Queue
    q = Queue(DATA_PARSING_AGENT, connection=redis_conn)

    all_rows = []
    for raw_jid in job_ids:
        jid = raw_jid.decode()
        try:
            job = q.fetch_job(jid)
        except Exception as e:
            logger.error(f"Fetch gagal {jid}: {e}")
            continue
        if job is None:
            continue
        all_rows.extend(extract_from_job(job, org_id))

    logger.info(f"Scanned {len(job_ids)} jobs, dapat {len(all_rows)} EncounterUpdate untuk org {org_id}")
    return all_rows


def write_excel(path: str, rows: list[dict]):
    headers = [
        "job_id", "norec", "noregistrasi", "created_at", "updated_at",
        "location_id", "data_sources",
    ]
    for name in SOURCE_NAMES:
        headers.append(f"{name}_count")
    for name in SOURCE_NAMES:
        headers.append(f"{name}_json")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "encounter_update"
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    wb.save(path)
    return path


if __name__ == "__main__":
    org_id = get_org_id()
    now = get_now()
    rows = extract_encounter_update(org_id)
    if rows:
        dirpath = f"results/{org_id}/{now.strftime('%Y%m%d')}"
        filename = f"{dirpath}/{FILENAME.data_parsing}_encounter_update_detail_{get_timestamp_file()}.xlsx"
        write_excel(filename, rows)
        logger.info(f"Disimpan {len(rows)} baris ke {filename}")
    else:
        logger.info("Tidak ada EncounterUpdate ditemukan.")
