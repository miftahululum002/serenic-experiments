"""Monitor live queue data parsing + estimasi kapasitas worker.

Menggabungkan peran monitor_queue.py dan analyze_worker_capacity.py menjadi
satu proses: sampling backlog setiap interval, menghitung throughput proses
live (job/detik), mendeteksi jenuh (backlog tumbuh monoton), lalu mengestimasi
kapasitas server dari rata-rata throughput saat periode jenuh.

Contoh:
    python monitor_queue_capacity.py --interval 5 --output results/capacity.csv
    python monitor_queue_capacity.py --window 3 --min-saturated-samples 4
"""

import argparse
import csv
import os
import signal
import sys
import time
from datetime import datetime, timezone
from statistics import fmean

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
        "ts": datetime.now(timezone.utc).isoformat(),
        "queue": queue_name,
        "pending": len(queue.jobs),
        "started": queue.started_job_registry.count,
        "failed": queue.failed_job_registry.count,
        "finished": queue.finished_job_registry.count,
        "workers_busy": busy,
        "workers_idle": idle,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Monitor live queue data parsing + estimasi kapasitas server"
    )
    parser.add_argument("--queue", default=DATA_PARSING_AGENT)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--samples", type=int, default=0, help="0 = until Ctrl+C")
    parser.add_argument("--window", type=int, default=3,
                        help="Sample berturut-turut backlog naik utk anggap jenuh")
    parser.add_argument("--min-saturated-samples", type=int, default=4,
                        help="Jumlah sample minimal periode jenuh utk estimasi kapasitas")
    parser.add_argument(
        "--output",
        default=f"results/capacity_monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
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

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    fields = ["ts", "queue", "pending", "started", "failed", "finished",
              "workers_busy", "workers_idle", "rate_jobs_per_sec",
              "trend", "status"]

    prev = None
    growth_run = 0
    saturated = False
    saturated_rates = []
    saturated_since = None

    def status_label() -> str:
        return "JENUH" if saturated else "OK"

    def render(row: dict) -> str:
        rate = row["rate_jobs_per_sec"]
        trend = row["trend"]
        return (f"[{row['ts'][11:19]}] pending={row['pending']:>5} "
                f"started={row['started']:>5} failed={row['failed']:>4} "
                f"finished={row['finished']:>5} busy={row['workers_busy']:>3}"
                f"/{row['workers_busy'] + row['workers_idle']:<3} "
                f"rate={rate:>6.2f} job/s {trend:>7} {status_label()}")

    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        def write_sample():
            nonlocal prev, growth_run, saturated, saturated_since

            row = sample(conn, args.queue)

            if prev is None:
                rate = 0.0
                trend = "-"
            else:
                dt = (datetime.fromisoformat(row["ts"])
                      - datetime.fromisoformat(prev["ts"])).total_seconds()
                processed = ((row["finished"] + row["failed"])
                             - (prev["finished"] + prev["failed"]))
                rate = processed / dt if dt > 0 else 0.0
                if row["pending"] > prev["pending"]:
                    growth_run += 1
                    trend = "NAIK"
                elif row["pending"] < prev["pending"]:
                    growth_run = 0
                    trend = "TURUN"
                else:
                    trend = "FLAT"

            if not saturated and growth_run >= args.window:
                saturated = True
                saturated_since = row["ts"]

            if saturated:
                saturated_rates.append(rate)
                if len(saturated_rates) >= args.min_saturated_samples:
                    capacity = fmean(saturated_rates)
                else:
                    capacity = None
            else:
                capacity = None

            row["rate_jobs_per_sec"] = rate
            row["trend"] = trend
            row["status"] = status_label()

            writer.writerow(row)
            f.flush()

            line = render(row)
            if capacity is not None:
                line += f" | CAP: {capacity:.2f} job/s ({capacity*3600:.0f}/jam)"
            print(line, flush=True)

            prev = row

        print(f"Monitoring {args.queue} setiap {args.interval}s -> {args.output}",
              flush=True)
        print("KOLOM: rate = throughput proses (delta finished+failed per detik), "
              "trend = arah backlog, CAP = estimasi kapasitas server (job/detik)",
              flush=True)
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

        count = 0
        while True:
            write_sample()
            count += 1
            if args.samples and count >= args.samples:
                break
            time.sleep(args.interval)

    print(f"\nDone. Hasil: {args.output}")
    if saturated_rates and len(saturated_rates) >= args.min_saturated_samples:
        cap = fmean(saturated_rates)
        print(f"Kapasitas server (data parsing): {cap:.2f} job/detik "
              f"= {cap*3600:.0f} job/jam = {cap*86400:.0f} job/hari")
        print(f"(jenuh sejak {saturated_since}, estimasi dari "
              f"{len(saturated_rates)} sample)")
    else:
        print("Tidak terdeteksi jenuh selama periode ini — server belum "
              "mencapai batas kapasitas.")


if __name__ == "__main__":
    main()
