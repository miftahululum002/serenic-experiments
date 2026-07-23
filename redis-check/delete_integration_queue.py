from config import redis_conn
from constant import DATA_PARSING_AGENT
from utils.utility import get_org_id
import sys


DRY_RUN = False


def find_jobs_by_org(queue_name: str, org_id: str) -> list[str]:
    queue_key = f"rq:queue:{queue_name}"
    total = redis_conn.llen(queue_key)
    job_ids = redis_conn.lrange(queue_key, 0, total - 1)

    results = []
    for jid in job_ids:
        desc = redis_conn.hget(f"rq:job:{jid}", "description")
        if desc and org_id.encode() in desc:
            results.append(jid.decode())

    return results


def delete_jobs(queue_name: str, job_ids: list[str]):
    queue_key = f"rq:queue:{queue_name}"
    for jid in job_ids:
        redis_conn.lrem(queue_key, 0, jid)
        redis_conn.delete(f"rq:job:{jid}")


def print_jobs(job_ids: list[str], queue_name: str, org_id: str):
    print(f"Target org ID: {org_id}")
    print(f"Queue: {queue_name}")
    print(f"Mode: {'DRY RUN (no delete)' if DRY_RUN else 'LIVE (will delete)'}")
    print(f"Total pending jobs: {redis_conn.llen(f'rq:queue:{queue_name}')}")

    if not job_ids:
        print(f"\nTidak ada job dengan org ID '{org_id}'.")
        return

    print(f"\nDitemukan: {len(job_ids)} job")
    for jid in job_ids:
        print(f"  {jid}")


if __name__ == "__main__":
    org_id = get_org_id()
    jobs = find_jobs_by_org(DATA_PARSING_AGENT, org_id)
    print_jobs(jobs, DATA_PARSING_AGENT, org_id)

    if not jobs:
        sys.exit(0)

    if DRY_RUN:
        print("\nDry run: tidak ada yang dihapus.")
    else:
        delete_jobs(DATA_PARSING_AGENT, jobs)
        print(f"\nBerhasil menghapus {len(jobs)} job dari queue '{DATA_PARSING_AGENT}'.")
