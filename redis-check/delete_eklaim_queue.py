from config import redis_conn
from constant import EKLAIM_BATCH_AGENT
from utils.utility import get_org_id
from rq import Queue

DRY_RUN = False


def find_jobs_by_org(queue_name: str, org_id: str) -> list:
    queue = Queue(queue_name, connection=redis_conn)
    to_delete = []
    for i, job in enumerate(queue.jobs, start=1):
        match = False
        try:
            kwargs = job.kwargs
            if kwargs.get("managing_organization_id") == org_id:
                match = True
        except Exception:
            raw_data = redis_conn.hget(f"rq:job:{job.id}", "data")
            if raw_data and org_id.encode() in raw_data:
                match = True

        if match:
            to_delete.append(job)
            enc_id = "(unknown)"
            try:
                enc_id = job.kwargs.get("encounter_id", "(unknown)")
            except Exception:
                pass
            print(f"  [{i}] {job.id}  encounter: {enc_id}")
    return to_delete


def delete_jobs(to_delete: list, queue_name: str):
    for job in to_delete:
        job.delete()
    print(f"Berhasil menghapus {len(to_delete)} job dari queue '{queue_name}'.")


if __name__ == "__main__":
    org_id = get_org_id()
    queue_name = EKLAIM_BATCH_AGENT
    queue = Queue(queue_name, connection=redis_conn)

    print(f"Target org ID: {org_id}")
    print(f"Mode: {'DRY RUN (no delete)' if DRY_RUN else 'LIVE (will delete)'}")
    print(f"Total job di queue: {len(queue.jobs)}\n")

    to_delete = find_jobs_by_org(queue_name, org_id)

    if not to_delete:
        print(f"Tidak ada job dengan org ID '{org_id}'.")
    elif DRY_RUN:
        print("\nDry run: tidak ada yang dihapus.")
    else:
        delete_jobs(to_delete, queue_name)
