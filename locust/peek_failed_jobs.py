import argparse
import csv
import json
import os
import re
from datetime import datetime, timezone

import redis
from rq import Queue

from config import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_USER,
    REDIS_PASSWORD,
    DATA_PARSING_AGENT,
)

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _org_id(description: str, job=None) -> str:
    if job is not None:
        try:
            org = job.kwargs.get("managing_organization_id")
            if org:
                return str(org)
        except Exception:
            pass
    match = UUID_RE.search(description or "")
    return match.group(0) if match else "?"


def _exception_summary(exc_info: str) -> str:
    if not exc_info:
        return "?"
    try:
        parsed = json.loads(exc_info)
        if isinstance(parsed, str):
            exc_info = parsed
        else:
            exc_info = json.dumps(parsed)
    except (ValueError, TypeError):
        pass
    lines = [ln.strip() for ln in exc_info.splitlines() if ln.strip()]
    if not lines:
        return exc_info[:300]
    return "\n".join(lines[-2:])[:300]


def get_failed_jobs(conn, queue_name: str) -> list[dict]:
    queue = Queue(queue_name, connection=conn)
    jobs = []
    for jid in queue.failed_job_registry.get_job_ids():
        raw = conn.hgetall(f"rq:job:{jid}")
        fields = {}
        for key, value in raw.items():
            key = key.decode() if isinstance(key, bytes) else key
            value = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
            fields[key] = value

        job = None
        try:
            job = queue.fetch_job(jid)
        except Exception:
            job = None

        jobs.append({
            "id": jid,
            "description": fields.get("description", "?"),
            "org_id": _org_id(fields.get("description", ""), job),
            "enqueued_at": fields.get("enqueued_at", ""),
            "ended_at": fields.get("ended_at", ""),
            "exception": _exception_summary(fields.get("exc_info", "")),
        })
    jobs.sort(key=lambda j: j["ended_at"], reverse=True)
    return jobs


def main():
    parser = argparse.ArgumentParser(description="Lihat job FAILED pada queue")
    parser.add_argument("--queue", default=DATA_PARSING_AGENT)
    parser.add_argument("--org", help="Filter hanya organisasi tertentu")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", help="Path CSV (opsional)")
    parser.add_argument("--redis-host", default=REDIS_HOST)
    parser.add_argument("--redis-port", type=int, default=REDIS_PORT)
    parser.add_argument("--redis-user", default=REDIS_USER)
    parser.add_argument("--redis-password", default=REDIS_PASSWORD)
    args = parser.parse_args()

    conn = redis.Redis(
        host=args.redis_host,
        port=args.redis_port,
        username=args.redis_user,
        password=args.redis_password,
        decode_responses=False,
    )
    conn.ping()

    jobs = get_failed_jobs(conn, args.queue)
    if args.org:
        jobs = [j for j in jobs if j["org_id"].startswith(args.org)]

    print(f"=== FAILED jobs queue {args.queue} ===")
    print(f"Total failed: {len(jobs)} (ditampilkan {min(len(jobs), args.limit)})")
    print("-" * 100)
    for job in jobs[: args.limit]:
        print(f"[{job['id'][:8]}] {job['description']}")
        print(f"    org={job['org_id']}  enqueued={job['enqueued_at']}  ended={job['ended_at']}")
        print(f"    exc: {job['exception']}")
        print("-" * 100)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "description", "org_id",
                                                   "enqueued_at", "ended_at", "exception"])
            writer.writeheader()
            writer.writerows(jobs)
        print(f"Disimpan: {args.output}")


if __name__ == "__main__":
    main()
