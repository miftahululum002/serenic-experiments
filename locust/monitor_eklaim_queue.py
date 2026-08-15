import argparse
import csv
import os
import signal
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import redis
from rq import Queue

from config import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_USER,
    REDIS_PASSWORD,
    AGENT_QUEUE_EKLAIM_BATCH,
)


def _org_id(job) -> str:
    try:
        return str(job.kwargs.get("managing_organization_id", "?"))
    except Exception:
        return "?"


def sample(conn, queue_name: str) -> dict:
    queue = Queue(queue_name, connection=conn)
    now = datetime.now(timezone.utc).isoformat()

    busy = 0
    idle = 0
    for key in conn.scan_iter(match="rq:worker:*", count=1000):
        state = conn.hget(key, b"state")
        if state is None:
            continue
        s = state.decode()
        if s == "busy":
            busy += 1
        elif s == "idle":
            idle += 1

    org_pending = Counter()
    for job in queue.jobs:
        org_pending[_org_id(job)] += 1

    org_started = Counter()
    for jid in queue.started_job_registry.get_job_ids():
        job = queue.fetch_job(jid)
        if job:
            org_started[_org_id(job)] += 1

    return {
        "ts": now,
        "queue": queue_name,
        "pending": len(queue.jobs),
        "started": queue.started_job_registry.count,
        "failed": queue.failed_job_registry.count,
        "finished": queue.finished_job_registry.count,
        "workers_busy": busy,
        "workers_idle": idle,
        "org_pending": dict(org_pending),
        "org_started": dict(org_started),
    }


def main():
    parser = argparse.ArgumentParser(description="Monitor RQ eklaim batch queue")
    parser.add_argument("--queue", default=AGENT_QUEUE_EKLAIM_BATCH)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--samples", type=int, default=0, help="0 = until Ctrl+C")
    parser.add_argument(
        "--output",
        default=f"results/eklaim_monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )
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

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    fields = ["ts", "queue", "pending", "started", "failed", "finished",
              "workers_busy", "workers_idle", "org_pending", "org_started"]
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        def write_sample():
            row = sample(conn, args.queue)
            writer.writerow(row)
            f.flush()
            print(f"[{row['ts']}] pending={row['pending']} "
                  f"started={row['started']} failed={row['failed']} "
                  f"busy={row['workers_busy']} idle={row['workers_idle']}",
                  flush=True)
            for org_id, count in row["org_pending"].items():
                print(f"    org {org_id[:8]}... pending={count}", flush=True)

        print(f"Monitoring eklaim queue {args.queue} every {args.interval}s "
              f"-> {args.output}", flush=True)
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

        count = 0
        while True:
            write_sample()
            count += 1
            if args.samples and count >= args.samples:
                break
            time.sleep(args.interval)

    print(f"Done. Results: {args.output}")


if __name__ == "__main__":
    main()
