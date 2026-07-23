from config import redis_conn
from utils.utility import get_org_id
from rq import Queue


def find_all_queues(org_id: str):
    all_rq_queue_keys = redis_conn.keys("rq:queue:*")
    queue_keys = [k for k in all_rq_queue_keys if redis_conn.type(k) == b"list"]

    print(f"=== SCAN SEMUA QUEUE untuk org: {org_id} ===\n")

    total_found = 0

    for qk in sorted(queue_keys):
        qname = qk.decode().replace("rq:queue:", "")
        try:
            queue = Queue(qname, connection=redis_conn)
        except Exception:
            continue

        for job in queue.jobs:
            try:
                kw = job.kwargs
                if kw.get("managing_organization_id") == org_id:
                    total_found += 1
                    print(f"[pending]  {qname:55s}  {job.id}")
                    if total_found <= 30:
                        print(f"           enc={kw.get('encounter_id','?')}, "
                              f"diag={kw.get('diagnosis_codes','?')}, "
                              f"proc={kw.get('procedure_codes','?')}")
            except Exception:
                pass

        for jid in queue.started_job_registry.get_job_ids():
            job = queue.fetch_job(jid)
            if job:
                try:
                    kw = job.kwargs
                    if kw.get("managing_organization_id") == org_id:
                        total_found += 1
                        print(f"[started]  {qname:55s}  {job.id}")
                        if total_found <= 30:
                            print(f"           enc={kw.get('encounter_id','?')}, "
                                  f"diag={kw.get('diagnosis_codes','?')}, "
                                  f"proc={kw.get('procedure_codes','?')}")
                except Exception:
                    pass

        for jid in queue.failed_job_registry.get_job_ids():
            job = queue.fetch_job(jid)
            if job:
                try:
                    kw = job.kwargs
                    if kw.get("managing_organization_id") == org_id:
                        total_found += 1
                        print(f"[failed]   {qname:55s}  {job.id}")
                except Exception:
                    pass

    print(f"\n=== TOTAL: {total_found} job ditemukan untuk org {org_id} ===")


if __name__ == "__main__":
    find_all_queues(get_org_id())
