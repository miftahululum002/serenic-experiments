from config import redis_conn
from constant import EKLAIM_BATCH_AGENT
from utils.utility import get_org_id
from rq import Queue


def check_running_detailed(org_id: str):
    queue_name = EKLAIM_BATCH_AGENT
    queue = Queue(queue_name, connection=redis_conn)

    print(f"=== Queue: {queue_name} ===")
    print(f"Pending jobs: {len(queue.jobs)}")

    print(f"\nStartedJobRegistry: {queue.started_job_registry.count}")
    started_ids = queue.started_job_registry.get_job_ids()
    print(f"Started job IDs: {started_ids}")

    print("\n=== Cek Worker yang sedang sibuk ===")
    busy = 0
    for k in redis_conn.keys("rq:worker:*"):
        state = redis_conn.hget(k, b"state")
        q = redis_conn.hget(k, b"queues")
        current = redis_conn.hget(k, b"current_job_id")
        if state and state != b"idle":
            busy += 1
            print(f"  {k.decode()}: state={state.decode()}, queues={q}, current_job={current}")
            if current:
                job = queue.fetch_job(current.decode())
                if job:
                    try:
                        kw = job.kwargs
                        print(f"    -> org: {kw.get('managing_organization_id')}, enc: {kw.get('encounter_id')}")
                    except Exception:
                        print(f"    -> (cannot decode)")

    if busy == 0:
        print("  (semua worker idle)")

    for reg_name, reg in [
        ("started", queue.started_job_registry),
        ("failed", queue.failed_job_registry),
        ("finished", queue.finished_job_registry),
    ]:
        ids = reg.get_job_ids()
        for jid in ids:
            job = queue.fetch_job(jid)
            if job:
                try:
                    kw = job.kwargs
                    if kw.get("managing_organization_id") == org_id:
                        print(f"\nDitemukan di {reg_name}: {jid}")
                except Exception:
                    pass

    print(f"\n=== Cek pending jobs {org_id[:8]}... ===")
    found = 0
    for job in queue.jobs:
        try:
            kw = job.kwargs
            if kw.get("managing_organization_id") == org_id:
                print(f"  {job.id}")
                found += 1
        except Exception:
            pass
    print(f"Total pending {org_id[:8]}...: {found}")

    print("\n=== Semua worker detail ===")
    for k in redis_conn.keys("rq:worker:*"):
        data = redis_conn.hgetall(k)
        queues = data.get(b"queues", b"").decode()
        if "eklaim" in queues:
            print(f"  {k.decode()}: queues={queues}, state={data.get(b'state', b'?').decode()}, current_job={data.get(b'current_job_id', b'?').decode()}")


if __name__ == "__main__":
    check_running_detailed(get_org_id())
