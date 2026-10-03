import sys
from datetime import datetime
from config import redis_conn
from constant import DATA_PARSING_AGENT, FILENAME
from utils.utility import get_org_id, write_csv, get_now, get_timestamp_file
from utils.logger import get_logger

sys.path.insert(0, "/Users/miftahululum002/projects/serenic/serenic_api_service")

logger = get_logger(__name__)

SOURCE_NAMES = [
    "triase_igd", "cppt", "asesmen_awal", "resume_medis",
    "penunjang_lab", "penunjang_radiologi", "laporan_operasi",
    "prosedur_medis_lain", "diagnosis_aktif", "diagnosis_awal",
    "obat", "billing",
]


def source_count(value):
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
            counts = {}
            present = []
            for name in SOURCE_NAMES:
                value = getattr(u.sources, name, None)
                if value is not None:
                    present.append(name)
                    counts[name] = source_count(value)
            rows.append({
                "job_id": job.id,
                "norec": u.norec,
                "noregistrasi": u.noregistrasi,
                "created_at": u.created_at,
                "updated_at": u.updated_at,
                "location_id": location_id(u.location),
                "data_sources": "|".join(present),
                "source_counts": ";".join(f"{k}:{v}" for k, v in counts.items()),
            })
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

    all_rows = []
    for raw_jid in job_ids:
        jid = raw_jid.decode()
        try:
            from rq import Queue
            q = Queue(DATA_PARSING_AGENT, connection=redis_conn)
            job = q.fetch_job(jid)
        except Exception as e:
            logger.error(f"Fetch gagal {jid}: {e}")
            continue
        if job is None:
            continue
        all_rows.extend(extract_from_job(job, org_id))

    logger.info(f"Scanned {len(job_ids)} jobs, dapat {len(all_rows)} EncounterUpdate untuk org {org_id}")
    return all_rows


if __name__ == "__main__":
    org_id = get_org_id()
    now = get_now()
    rows = extract_encounter_update(org_id)
    if rows:
        dirpath = f"results/{org_id}/{now.strftime('%Y%m%d')}"
        filename = f"{dirpath}/{FILENAME.data_parsing}_encounter_update_{get_timestamp_file()}.csv"
        write_csv(filename, rows, [
            "job_id", "norec", "noregistrasi", "created_at", "updated_at",
            "location_id", "data_sources", "source_counts",
        ])
        logger.info(f"Disimpan {len(rows)} baris ke {filename}")
    else:
        logger.info("Tidak ada EncounterUpdate ditemukan.")
