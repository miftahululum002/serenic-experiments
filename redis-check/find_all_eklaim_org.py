from config import redis_conn
from constant import EKLAIM_BATCH_AGENT
from utils.utility import get_org_id
from rq import Queue


def check_job(job, org_id: str, source: str, counter: dict) -> bool:
    if not job:
        return False
    match = False
    try:
        kw = job.kwargs
        if kw.get("managing_organization_id") == org_id:
            match = True
    except Exception:
        raw = redis_conn.hget(f"rq:job:{job.id}", "data")
        if raw and org_id.encode() in raw:
            match = True
    if match:
        counter["total"] += 1
        enc = "?"
        try:
            enc = job.kwargs.get("encounter_id", "?")
        except Exception:
            pass
        print(f"  [{source}] {job.id}  enc={enc}")
    return match


def find_all_eklaim_org(org_id: str):
    queue_name = EKLAIM_BATCH_AGENT
    queue = Queue(queue_name, connection=redis_conn)

    print(f"=== Mencari job {org_id[:8]}... di SEMUA registry ===\n")
    counter = {"total": 0}

    print("--- Pending Queue ---")
    for job in queue.jobs:
        check_job(job, org_id, "pending", counter)

    print("\n--- Started Registry ---")
    for jid in queue.started_job_registry.get_job_ids():
        check_job(queue.fetch_job(jid), org_id, "started", counter)

    print("\n--- Failed Registry ---")
    for jid in queue.failed_job_registry.get_job_ids():
        check_job(queue.fetch_job(jid), org_id, "failed", counter)

    print("\n--- Finished Registry ---")
    for jid in queue.finished_job_registry.get_job_ids():
        check_job(queue.fetch_job(jid), org_id, "finished", counter)

    print("\n--- Deferred Registry ---")
    for jid in queue.deferred_job_registry.get_job_ids():
        check_job(queue.fetch_job(jid), org_id, "deferred", counter)

    print("\n--- Scheduled Registry ---")
    for jid in queue.scheduled_job_registry.get_job_ids():
        check_job(queue.fetch_job(jid), org_id, "scheduled", counter)

    print("\n--- Worker current_job ---")
    for k in redis_conn.keys("rq:worker:*"):
        cj = redis_conn.hget(k, "current_job_id")
        if cj:
            job = queue.fetch_job(cj.decode())
            if job:
                check_job(job, org_id, f"worker:{k.decode()[-12:]}", counter)

    print("\n--- Brute force scan rq:job:* ---")
    all_job_keys = redis_conn.keys("rq:job:*")
    scanned = 0
    for jk in all_job_keys:
        raw = redis_conn.hget(jk, "data")
        if raw and org_id.encode() in raw:
            jid = jk.decode().replace("rq:job:", "")
            job = queue.fetch_job(jid)
            if job:
                check_job(job, org_id, "brute_scan", counter)
            else:
                counter["total"] += 1
                print(f"  [brute_scan] {jid}  (undecodable but org ID found in raw)")
        scanned += 1
        if scanned % 500 == 0:
            print(f"    ... scanned {scanned}/{len(all_job_keys)}")

    print(f"\n=== TOTAL ditemukan: {counter['total']} ===")


if __name__ == "__main__":
    find_all_eklaim_org(get_org_id())
