import argparse
import csv
from datetime import datetime


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def read_monitor(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append({
                "ts": parse_ts(row["ts"]),
                "pending": int(row["pending"]),
                "started": int(row["started"]),
                "failed": int(row["failed"]),
                "finished": int(row["finished"]),
                "busy": int(row["workers_busy"]),
                "idle": int(row["workers_idle"]),
            })
    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Analisis kapasitas worker data parsing dari monitor queue"
    )
    parser.add_argument("--monitor", required=True, help="CSV dari monitor_queue.py")
    parser.add_argument("--window", type=int, default=3,
                        help="Jumlah sample berturut-turut utk anggap jenuh")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    rows = read_monitor(args.monitor)
    if not rows:
        print("Monitor CSV kosong")
        return

    start, end = rows[0], rows[-1]
    duration = (end["ts"] - start["ts"]).total_seconds()
    pend = [r["pending"] for r in rows]

    # finished_job_registry RQ punya TTL (default 1 jam / max 500 job).
    # Delta finished valid selama registry belum penuh; fallback ke delta
    # pending jika finished tidak bergerak.
    finished_delta = end["finished"] - start["finished"]
    failed_delta = end["failed"] - start["failed"]
    processed = finished_delta + failed_delta
    if processed <= 0 and pend[0] > 0:
        processed = pend[0] - pend[-1]

    # Pertumbuhan backlog monoton (indikator jenuh)
    growth_runs = []
    run = []
    for i in range(1, len(rows)):
        if rows[i]["pending"] > rows[i - 1]["pending"]:
            run.append(i)
        else:
            if run:
                growth_runs.append(run)
                run = []
    if run:
        growth_runs.append(run)

    saturated_at = None
    for growth_run in growth_runs:
        if len(growth_run) >= args.window:
            saturated_at = rows[growth_run[0]]["ts"]
            break

    print("=" * 60)
    print("Hasil Analisis Kapasitas Worker Data Parsing")
    print("=" * 60)
    print(f"Durasi monitor  : {start['ts']} -> {end['ts']}")
    print(f"Backlog awal    : {start['pending']}")
    print(f"Backlog akhir   : {end['pending']}")
    print(f"Backlog puncak  : {max(pend)}")
    print(f"Job diproses    : {processed}")
    print(f"Worker busy max : {max(r['busy'] for r in rows)} / {max(r['busy'] + r['idle'] for r in rows)}")

    if saturated_at:
        print(f"\n[JENUH] Backlog tumbuh monoton sejak {saturated_at}")
        print("  -> laju masuk (API) > kapasitas proses worker")
    else:
        print("\n[OK] Backlog tidak tumbuh monoton -> worker masih sanggup (belum jenuh)")

    if processed > 0 and duration > 0:
        rate = processed / duration
        print(f"\nThroughput pemrosesan worker: {rate:.2f} job/detik")
        print(f"  = {rate * 3600:.0f} job/jam")
        print(f"  = {rate * 86400:.0f} job/hari")
        print(f"  (delta finished {finished_delta} + failed {failed_delta} "
              f"selama {duration:.0f} detik)")

    if args.output:
        import os
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            f.write(f"backlog_start={start['pending']}\n")
            f.write(f"backlog_end={end['pending']}\n")
            f.write(f"backlog_max={max(pend)}\n")
            f.write(f"jobs_processed={processed}\n")
            f.write(f"throughput_jobs_per_sec={rate if processed > 0 and duration > 0 else 0:.2f}\n")
            f.write(f"saturated_at={saturated_at}\n")
        print(f"\nDetail disimpan: {args.output}")


if __name__ == "__main__":
    main()
