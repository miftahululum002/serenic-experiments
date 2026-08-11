import argparse
import csv
import os
import signal
import sys
import time
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

    return {
        "ts": now,
        "queue": queue_name,
        "pending": queue.count,
        "started": queue.started_job_registry.count,
        "failed": queue.failed_job_registry.count,
        "finished": queue.finished_job_registry.count,
        "workers_busy": busy,
        "workers_idle": idle,
    }


def main():
    parser = argparse.ArgumentParser(description="Monitor RQ parsing queue backlog")
    parser.add_argument("--queue", default=DATA_PARSING_AGENT)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--samples", type=int, default=0, help="0 = until Ctrl+C")
    parser.add_argument(
        "--output",
        default=f"results/queue_monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
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
              "workers_busy", "workers_idle"]
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

        print(f"Monitoring queue {args.queue} every {args.interval}s -> {args.output}",
              flush=True)
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
