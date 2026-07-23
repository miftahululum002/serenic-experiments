from config import redis_conn
from constant import DATA_PARSING_AGENT
from utils.utility import get_org_id
from utils.logger import get_logger
import re

logger = get_logger(__name__)


def find_jobs_by_org(queue_name: str, org_id: str) -> list[dict]:
    queue_key = f"rq:queue:{queue_name}"
    total = redis_conn.llen(queue_key)
    logger.debug(f"Scanning {total} pending jobs in queue {queue_name}")

    job_ids = redis_conn.lrange(queue_key, 0, total - 1)
    results = []
    for jid in job_ids:
        jid = jid.decode()
        desc = redis_conn.hget(f"rq:job:{jid}", "description")
        if desc and org_id.encode() in desc:
            desc_text = desc.decode()
            ts_match = re.search(r"start_timestamp=datetime\.datetime\(([^)]+)\)", desc_text)
            ts = ts_match.group(0) if ts_match else "?"
            created = redis_conn.hget(f"rq:job:{jid}", "created_at")
            results.append({
                "id": jid,
                "start_timestamp": ts,
                "created_at": created.decode() if created else "?",
            })

    logger.info(f"Found {len(results)} jobs for org {org_id} in queue {queue_name}")
    return results


def print_jobs(jobs: list[dict], queue_name: str, org_id: str):
    logger.info(f"Queue: {queue_name}")
    logger.info(f"Target org: {org_id}")
    logger.info(f"Total pending jobs: {redis_conn.llen(f'rq:queue:{queue_name}')}")

    if not jobs:
        logger.info(f"Tidak ada job untuk org {org_id}.")
        return

    logger.info(f"--- Pending jobs for org {org_id} ---")
    for i, job in enumerate(jobs, 1):
        logger.info(f"  {i}. {job['id']} | created_at: {job['created_at']}")

    logger.info(f"TOTAL: {len(jobs)} job ditemukan")


if __name__ == "__main__":
    org_id = get_org_id()
    jobs = find_jobs_by_org(DATA_PARSING_AGENT, org_id)
    print_jobs(jobs, DATA_PARSING_AGENT, org_id)
