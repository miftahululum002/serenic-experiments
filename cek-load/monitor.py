"""Sampling backlog queue dari waktu ke waktu -> throughput, trend, ETA habis.

Karena registry `finished` punya TTL (delta-nya tidak akurat untuk throughput),
laju utama dihitung dari perubahan backlog + jumlah job yang keluar dari queue.

Contoh:
    python monitor.py --minutes 5 --interval 10
"""

import argparse
import csv
import os
import time
from datetime import datetime, timezone

from rq import Queue, Worker

from config import DATA_PARSING_AGENT, get_redis


def sample(conn, qname: str) -> dict:
    q = Queue(qname, connection=conn)
    busy = idle = 0
    for w in Worker.all(connection=conn):
        if qname not in w.queue_names():
            continue
        st = w.get_state()
        if st == "busy":
            busy += 1
        elif st == "idle":
            idle += 1
    return {
        "ts": datetime.now(timezone.utc),
        "pending": q.count,
        "started": q.started_job_registry.count,
        "failed": q.failed_job_registry.count,
        "finished": q.finished_job_registry.count,
        "busy": busy,
        "idle": idle,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--queue", default=DATA_PARSING_AGENT)
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--minutes", type=float, default=5.0)
    p.add_argument("--output", default=None)
    args = p.parse_args()

    out = args.output or (
        f"results/monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    conn = get_redis()
    conn.ping()

    deadline = time.time() + args.minutes * 60
    rows = []
    fields = ["ts", "pending", "started", "failed", "finished", "busy", "idle",
              "d_pending", "drain_per_s", "trend"]

    print(f"Monitor {args.queue} | interval {args.interval}s | "
          f"durasi {args.minutes} menit -> {out}", flush=True)
    print(f"{'jam':>8} {'pending':>8} {'start':>6} {'fail':>5} {'fin':>5} "
          f"{'busy':>5} {'idle':>5} {'dPend':>7} {'drain/s':>8}  trend", flush=True)

    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        prev = None
        while True:
            r = sample(conn, args.queue)
            if prev is None:
                d_pending, drain, trend = 0, 0.0, "-"
            else:
                dt = (r["ts"] - prev["ts"]).total_seconds()
                d_pending = r["pending"] - prev["pending"]
                drain = -d_pending / dt if dt > 0 else 0.0
                trend = "NAIK" if d_pending > 0 else (
                    "TURUN" if d_pending < 0 else "FLAT")
            r2 = dict(r, ts=r["ts"].isoformat(), d_pending=d_pending,
                      drain_per_s=round(drain, 4), trend=trend)
            writer.writerow(r2)
            f.flush()
            rows.append(r)
            print(f"{r['ts'].astimezone().strftime('%H:%M:%S'):>8} "
                  f"{r['pending']:>8} {r['started']:>6} {r['failed']:>5} "
                  f"{r['finished']:>5} {r['busy']:>5} {r['idle']:>5} "
                  f"{d_pending:>7} {drain:>8.3f}  {trend}", flush=True)
            prev = r
            if time.time() >= deadline:
                break
            time.sleep(args.interval)

    # --- ringkasan ---
    first, last = rows[0], rows[-1]
    span = (last["ts"] - first["ts"]).total_seconds()
    d_pend = last["pending"] - first["pending"]
    drain = -d_pend / span if span > 0 else 0.0
    print(f"\n=== RINGKASAN ({span:.0f} detik) ===")
    print(f"pending: {first['pending']} -> {last['pending']} ({d_pend:+d})")
    print(f"failed : {first['failed']} -> {last['failed']} "
          f"({last['failed'] - first['failed']:+d})")
    print(f"laju drain backlog: {drain:.4f} job/s = {drain*3600:.1f} job/jam")
    if drain > 0:
        eta_s = last["pending"] / drain
        print(f"ETA habis (jika tak ada job masuk lagi): {eta_s/3600:.1f} jam "
              f"({eta_s/60:.0f} menit)")
        print(f"Rata-rata durasi 1 job: {last['busy'] / drain:.1f} detik "
              f"(dengan {last['busy']} worker paralel)")
    elif drain < 0:
        print("Backlog TUMBUH — laju masuk > laju proses (server jenuh).")
    else:
        print("Backlog FLAT — tidak ada progres terdeteksi pada periode ini.")


if __name__ == "__main__":
    main()
