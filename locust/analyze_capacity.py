import argparse
import csv
import os
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
            })
    return rows


def read_locust(path: str) -> dict[datetime, float]:
    rps = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            ts = row.get("Timestamp") or row.get("timestamp")
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(str(ts))
            except ValueError:
                continue
            try:
                val = float(row.get("Request Count per Second") or 0)
            except (TypeError, ValueError):
                val = 0.0
            rps[t] = val
    return rps


def main():
    parser = argparse.ArgumentParser(description="Analisis kapasitas worker parsing")
    parser.add_argument("--monitor", required=True, help="CSV dari monitor_queue.py")
    parser.add_argument("--locust", help="CSV stats locust (optional, utk RPS)")
    parser.add_argument("--window", type=int, default=3,
                        help="Jumlah sample berturut-turut utk anggap jenuh")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    rows = read_monitor(args.monitor)
    if not rows:
        print("Monitor CSV kosong")
        return

    locust_rps = read_locust(args.locust) if args.locust else {}

    start = rows[0]
    end = rows[-1]
    pend = [r["pending"] for r in rows]
    total_pending = max(pend)
    processed = end["finished"] + end["failed"] - start["finished"] - start["failed"]
    produced = processed + end["pending"] - start["pending"]

    # Deteksi pertumbuhan backlog monoton (indikator jenuh)
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
    for run in growth_runs:
        if len(run) >= args.window:
            idx = run[0]
            saturated_at = rows[idx]["ts"]
            break

    print("=" * 60)
    print("Hasil Analisis Kapasitas Worker Parsing")
    print("=" * 60)
    print(f"Durasi monitor  : {start['ts']} -> {end['ts']}")
    print(f"Backlog awal    : {start['pending']}")
    print(f"Backlog akhir   : {end['pending']}")
    print(f"Backlog puncak  : {total_pending}")
    print(f"Job diproses    : {processed}")
    print(f"Job diproduksi  : {produced}")
    print(f"Worker busy max : {max(r['busy'] for r in rows)}")

    if saturated_at:
        print(f"\n[JENUH] Backlog tumbuh monoton sejak {saturated_at}")
        if locust_rps:
            near = min(locust_rps, key=lambda t: abs((t - saturated_at).total_seconds()))
            print(f"  -> RPS request saat itu: {locust_rps[near]:.2f}")
    else:
        print("\n[OK] Backlog tidak tumbuh monoton -> worker masih sanggup (belum jenuh)")

    if processed > 0 and (end["ts"] - start["ts"]).total_seconds() > 0:
        rate = processed / (end["ts"] - start["ts"]).total_seconds()
        print(f"Throughput pemrosesan: {rate:.2f} job/detik")

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            f.write(f"backlog_start={start['pending']}\n")
            f.write(f"backlog_end={end['pending']}\n")
            f.write(f"backlog_max={total_pending}\n")
            f.write(f"jobs_processed={processed}\n")
            f.write(f"jobs_produced={produced}\n")
            f.write(f"saturated_at={saturated_at}\n")
        print(f"\nDetail disimpan: {args.output}")


if __name__ == "__main__":
    main()
