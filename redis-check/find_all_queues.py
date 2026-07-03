import zlib
from config import redis_conn
from constant import TARGET_ORG
from rq import Queue

# Dapatkan semua queue dari Redis keys (hanya tipe list)
all_rq_queue_keys = redis_conn.keys("rq:queue:*")
queue_keys = []
for k in all_rq_queue_keys:
    if redis_conn.type(k) == b"list":
        queue_keys.append(k)

print(f"=== SCAN SEMUA QUEUE untuk org: {TARGET_ORG} ===\n")

total_found = 0
all_found_jobs = {}  # jid -> {queue, status}

for qk in sorted(queue_keys):
    qname = qk.decode().replace("rq:queue:", "")
    try:
        queue = Queue(qname, connection=redis_conn)
    except Exception:
        continue

    # Pending jobs
    for job in queue.jobs:
        try:
            kw = job.kwargs
            if kw.get("managing_organization_id") == TARGET_ORG:
                total_found += 1
                all_found_jobs[job.id] = {"queue": qname, "status": "pending"}
                print(f"[pending]  {qname:55s}  {job.id}")
                if total_found <= 30:
                    print(f"           enc={kw.get('encounter_id','?')}, "
                          f"diag={kw.get('diagnosis_codes','?')}, "
                          f"proc={kw.get('procedure_codes','?')}")
        except Exception:
            pass

    # Started jobs
    for jid in queue.started_job_registry.get_job_ids():
        job = queue.fetch_job(jid)
        if job:
            try:
                kw = job.kwargs
                if kw.get("managing_organization_id") == TARGET_ORG:
                    total_found += 1
                    all_found_jobs[job.id] = {"queue": qname, "status": "started"}
                    print(f"[started]  {qname:55s}  {job.id}")
                    if total_found <= 30:
                        print(f"           enc={kw.get('encounter_id','?')}, "
                              f"diag={kw.get('diagnosis_codes','?')}, "
                              f"proc={kw.get('procedure_codes','?')}")
            except Exception:
                pass

    # Failed jobs
    for jid in queue.failed_job_registry.get_job_ids():
        job = queue.fetch_job(jid)
        if job:
            try:
                kw = job.kwargs
                if kw.get("managing_organization_id") == TARGET_ORG:
                    total_found += 1
                    all_found_jobs[job.id] = {"queue": qname, "status": "failed"}
                    print(f"[failed]   {qname:55s}  {job.id}")
            except Exception:
                pass

print(f"\n=== TOTAL: {total_found} job ditemukan untuk org {TARGET_ORG} ===")
if total_found > 0:
    print("\n=== HAPUS SEMUA? (y/n) ===")
