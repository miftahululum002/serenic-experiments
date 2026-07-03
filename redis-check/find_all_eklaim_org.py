import zlib
from config import redis_conn
from constant import EKLAIM_BATCH_AGENT, TARGET_ORG
from rq import Queue

queue_name = EKLAIM_BATCH_AGENT
queue = Queue(queue_name, connection=redis_conn)

# 1. Cek semua job di semua registry
print("=== Mencari job d2a967c2... di SEMUA registry ===\n")

total_found = 0

def check_job(job, source):
    global total_found
    if not job:
        return False
    match = False
    try:
        kw = job.kwargs
        if kw.get("managing_organization_id") == TARGET_ORG:
            match = True
    except Exception:
        raw = redis_conn.hget(f"rq:job:{job.id}", "data")
        if raw and TARGET_ORG.encode() in raw:
            match = True
    if match:
        total_found += 1
        enc = getattr(job, 'kwargs', {}).get('encounter_id', '?') if not isinstance(getattr(job, 'kwargs', {}), Exception) else '?'
        print(f"  [{source}] {job.id}  enc={enc}")
    return match

# Pending queue
print("--- Pending Queue ---")
for job in queue.jobs:
    check_job(job, "pending")

# Started
print("\n--- Started Registry ---")
for jid in queue.started_job_registry.get_job_ids():
    job = queue.fetch_job(jid)
    check_job(job, "started")

# Failed
print("\n--- Failed Registry ---")
for jid in queue.failed_job_registry.get_job_ids():
    job = queue.fetch_job(jid)
    check_job(job, "failed")

# Finished
print("\n--- Finished Registry ---")
for jid in queue.finished_job_registry.get_job_ids():
    job = queue.fetch_job(jid)
    check_job(job, "finished")

# Deferred
print("\n--- Deferred Registry ---")
for jid in queue.deferred_job_registry.get_job_ids():
    job = queue.fetch_job(jid)
    check_job(job, "deferred")

# Scheduled
print("\n--- Scheduled Registry ---")
for jid in queue.scheduled_job_registry.get_job_ids():
    job = queue.fetch_job(jid)
    check_job(job, "scheduled")

# 2. Cek worker current_job
print("\n--- Worker current_job ---")
for k in redis_conn.keys("rq:worker:*"):
    cj = redis_conn.hget(k, "current_job_id")
    if cj:
        job = queue.fetch_job(cj.decode())
        if job:
            check_job(job, f"worker:{k.decode()[-12:]}")

# 3. Scan semua rq:job: keys (brute force)
print("\n--- Brute force scan rq:job:* ---")
all_job_keys = redis_conn.keys("rq:job:*")
scanned = 0
for jk in all_job_keys:
    raw = redis_conn.hget(jk, "data")
    if raw and TARGET_ORG.encode() in raw:
        jid = jk.decode().replace("rq:job:", "")
        job = queue.fetch_job(jid)
        if job:
            check_job(job, "brute_scan")
        else:
            total_found += 1
            print(f"  [brute_scan] {jid}  (undecodable but org ID found in raw)")
    scanned += 1
    if scanned % 500 == 0:
        print(f"    ... scanned {scanned}/{len(all_job_keys)}")

print(f"\n=== TOTAL ditemukan: {total_found} ===")
