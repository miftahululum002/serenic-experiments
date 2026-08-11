"""Analisis kapasitas worker data parsing: backlog + RPS + latency.

Menggabungkan monitor queue (CSV dari monitor_queue.py /
monitor_queue_capacity.py) dan stats locust (CSV *_stats_history.csv dari
--csv-full-history) untuk melaporkan:

  - titik jenuh (backlog tumbuh monoton)
  - RPS request & user count saat jenuh
  - latency (median, avg, p95, p99) saat jenuh
  - throughput pemrosesan worker (job/detik)
  - peak RPS keseluruhan selama test

Contoh:
    python analyze_capacity_full.py --monitor results/queue_monitor.csv \
        --locust results/locust_stats_history.csv --output results/kapasitas.txt
"""

import argparse
import csv
import os
from collections import defaultdict
from datetime import datetime


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_monitor(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append({
                "ts": parse_ts(row["ts"]),
                "pending": int(row["pending"]),
                "finished": int(row["finished"]),
                "failed": int(row["failed"]),
                "busy": int(row["workers_busy"]),
            })
    return rows


def read_locust_history(path: str) -> list[dict]:
    """Baca *_stats_history.csv, ambil baris Total per timestamp."""
    groups: dict[datetime, dict] = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        names = reader.fieldnames or []
        for row in reader:
            ts_raw = row.get("Timestamp")
            if not ts_raw:
                continue
            try:
                t = parse_ts(ts_raw)
            except ValueError:
                continue

            name = row.get("Name", "")
            if "Name" in names and name != "Total":
                continue

            def fval(*keys) -> float:
                for k in keys:
                    if row.get(k):
                        try:
                            return float(row[k])
                        except ValueError:
                            continue
                return 0.0

            g = groups.setdefault(t, {
                "ts": t, "rps": 0.0, "users": 0, "avg": 0.0,
                "med": 0.0, "p95": 0.0, "p99": 0.0,
            })
            g["rps"] += fval("Requests/s", "Request Count per Second")
            g["users"] = max(g["users"], int(fval("User Count")))
            g["avg"] = max(g["avg"], fval("Total Average Response Time",
                                          "Average Response Time"))
            g["med"] = max(g["med"], fval("Total Median Response Time",
                                          "Median Response Time"))
            g["p95"] = max(g["p95"], fval("95%", "Total 95%"))
            g["p99"] = max(g["p99"], fval("99%", "Total 99%"))

    return [groups[t] for t in sorted(groups)]


def nearest(rows: list[dict], target: datetime) -> dict | None:
    if not rows:
        return None
    return min(rows, key=lambda r: abs((r["ts"] - target).total_seconds()))


def fmt_point(label: str, p: dict | None) -> str:
    if p is None:
        return f"{label:<34}: (tidak ada data locust)"
    return (f"{label:<34}: RPS={p['rps']:7.2f} | users={p['users']:>5} | "
            f"latency avg={p['avg']:7.1f} p95={p['p95']:7.1f} p99={p['p99']:7.1f} "
            f"med={p['med']:7.1f} ms")


def main():
    parser = argparse.ArgumentParser(
        description="Analisis kapasitas: backlog + RPS + latency"
    )
    parser.add_argument("--monitor", required=True, help="CSV dari monitor queue")
    parser.add_argument("--locust", required=True,
                        help="CSV *_stats_history.csv dari locust")
    parser.add_argument("--window", type=int, default=3,
                        help="Sample backlog naik berturut-turut utk anggap jenuh")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    mrows = read_monitor(args.monitor)
    lrows = read_locust_history(args.locust)
    if not mrows:
        print("Monitor CSV kosong")
        return
    if not lrows:
        print("Locust stats_history CSV kosong / format tidak dikenal")
        return

    start, end = mrows[0], mrows[-1]
    duration = (end["ts"] - start["ts"]).total_seconds()
    pend = [r["pending"] for r in mrows]

    growth_runs = []
    run = []
    for i in range(1, len(mrows)):
        if mrows[i]["pending"] > mrows[i - 1]["pending"]:
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
            saturated_at = mrows[growth_run[0]]["ts"]
            break

    finished_delta = end["finished"] - start["finished"]
    failed_delta = end["failed"] - start["failed"]
    processed = finished_delta + failed_delta
    if processed <= 0 and pend[0] > 0:
        processed = pend[0] - pend[-1]

    peak = max(lrows, key=lambda r: r["rps"])
    at_sat = nearest(lrows, saturated_at) if saturated_at else None

    print("=" * 78)
    print("Hasil Analisis Kapasitas Server Data Parsing (Backlog + RPS + Latency)")
    print("=" * 78)
    print(f"Durasi monitor      : {start['ts']} -> {end['ts']}")
    print(f"Backlog awal/akhir  : {start['pending']} / {end['pending']} "
          f"(puncak {max(pend)})")
    print(f"Job diproses        : {processed} ({duration:.0f} detik)")

    if processed > 0 and duration > 0:
        rate = processed / duration
        print(f"Throughput worker   : {rate:.2f} job/detik "
              f"= {rate*3600:.0f} job/jam = {rate*86400:.0f} job/hari")

    if saturated_at:
        print(f"\n[JENUH] Backlog tumbuh monoton sejak {saturated_at}")
        print("  -> laju masuk (API) melebihi kapasitas proses worker")
    else:
        print("\n[OK] Backlog tidak tumbuh monoton -> belum terdeteksi jenuh")

    print("\nRPS & latency (dari locust stats_history):")
    print(f"  {'Peak RPS (seluruh test)':<34}: RPS={peak['rps']:7.2f} "
          f"@ {peak['ts']} | latency avg={peak['avg']:7.1f} "
          f"p95={peak['p95']:7.1f} p99={peak['p99']:7.1f} ms")
    print(f"  {fmt_point('Saat jenuh', at_sat)}")

    lines = [
        "=" * 78,
        "Hasil Analisis Kapasitas Server Data Parsing (Backlog + RPS + Latency)",
        "=" * 78,
        f"backlog_start={start['pending']}",
        f"backlog_end={end['pending']}",
        f"backlog_max={max(pend)}",
        f"jobs_processed={processed}",
        f"throughput_jobs_per_sec={rate if processed > 0 and duration > 0 else 0:.2f}",
        f"saturated_at={saturated_at}",
        f"peak_rps={peak['rps']:.2f}",
        f"peak_rps_at={peak['ts']}",
        f"latency_at_peak_ms_avg={peak['avg']:.1f}",
        f"latency_at_peak_ms_p95={peak['p95']:.1f}",
        f"latency_at_peak_ms_p99={peak['p99']:.1f}",
        f"rps_at_saturation={at_sat['rps']:.2f}" if at_sat else "rps_at_saturation=",
        f"latency_at_saturation_ms_avg={at_sat['avg']:.1f}" if at_sat else "latency_at_saturation_ms_avg=",
        f"latency_at_saturation_ms_p95={at_sat['p95']:.1f}" if at_sat else "latency_at_saturation_ms_p95=",
        f"latency_at_saturation_ms_p99={at_sat['p99']:.1f}" if at_sat else "latency_at_saturation_ms_p99=",
        f"users_at_saturation={at_sat['users']}" if at_sat else "users_at_saturation=",
    ]
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\nDetail disimpan: {args.output}")


if __name__ == "__main__":
    main()
