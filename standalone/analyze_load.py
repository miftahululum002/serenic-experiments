"""
Analisis Load Test: Users & Throughput
========================================
Membaca hasil load test dari file JSON dan menampilkan:
  - Jumlah user (concurrent)
  - Request per second (throughput)
  - Detail per user

Cara pakai:
  .venv/bin/python analyze_load.py load_test_results/20260720/load_test_10_20260720_103000.json
  
  # Atau analisis semua file dalam direktori:
  .venv/bin/python analyze_load.py load_test_results/20260720/
"""

import json
import os
import sys
import csv
import statistics
from datetime import datetime
from pathlib import Path


OUTPUT_DIR = Path(__file__).parent / "analyze_load_results"


def percentile(data, p):
    if not data:
        return 0
    sorted_data = sorted(data)
    k = int(len(sorted_data) * p / 100)
    return sorted_data[k]


def analyze_file(json_path: str) -> dict:
    with open(json_path) as f:
        data = json.load(f)

    total = len(data)
    successful = [r for r in data if r.get("success")]
    failed = [r for r in data if not r.get("success")]
    post_errors = [r for r in data if r.get("post_error")]

    # Waktu nyata (dari timestamp ISO)
    sent_times = []
    final_times = []
    for r in data:
        t = r.get("post_sent_time")
        if t:
            sent_times.append(datetime.fromisoformat(t))
        t = r.get("get_final_time")
        if t:
            final_times.append(datetime.fromisoformat(t))

    all_times = sent_times + final_times
    if all_times:
        wall_seconds = (max(all_times) - min(all_times)).total_seconds()
    else:
        wall_seconds = 0

    # Latency data
    post_latencies = [
        r.get("post_latency_seconds", 0)
        for r in data
        if r.get("post_latency_seconds", 0) > 0
    ]
    e2e_times = [
        r.get("end_to_end_seconds", 0)
        for r in data
        if r.get("end_to_end_seconds", 0) > 0
    ]
    poll_counts = [r.get("total_polls", 0) for r in data]

    request_duration_seconds = (
        wall_seconds if wall_seconds > 0 else (max(e2e_times) if e2e_times else 1)
    )

    def lat_stats(vals):
        if not vals:
            return {}
        return {
            "mean": round(statistics.mean(vals), 3),
            "median": round(statistics.median(vals), 3),
            "min": round(min(vals), 3),
            "max": round(max(vals), 3),
            "p90": round(percentile(vals, 90), 3),
            "p95": round(percentile(vals, 95), 3),
            "p99": round(percentile(vals, 99), 3),
        }

    return {
        "file": json_path,
        "total_users": total,
        "successful": len(successful),
        "failed": len(failed),
        "post_errors": len(post_errors),
        "total_polls": sum(poll_counts),
        "avg_polls_per_user": (
            round(sum(poll_counts) / len(poll_counts), 1) if poll_counts else 0
        ),
        "wall_seconds": round(request_duration_seconds, 1),
        "wall_minutes": round(request_duration_seconds / 60, 1),
        "requests_per_second": (
            round(len(data) / request_duration_seconds, 2)
            if request_duration_seconds > 0
            else 0
        ),
        "successful_per_second": (
            round(len(successful) / request_duration_seconds, 2)
            if request_duration_seconds > 0
            else 0
        ),
        "throughput_per_minute": (
            round(len(successful) / request_duration_seconds * 60, 1)
            if request_duration_seconds > 0
            else 0
        ),
        "post": lat_stats(post_latencies),
        "e2e": lat_stats(e2e_times),
    }


def build_markdown(all_stats: list[dict]) -> str:
    lines = []

    def w(text=""):
        lines.append(text)

    w("# Analisis Load Test")
    w(f"> **Dianalisis pada:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
    w("")

    for s in all_stats:
        w(f"## {Path(s['file']).name}")
        w("")
        w("### Ringkasan")
        w("")
        w("| Metrik | Nilai |")
        w("|--------|-------|")
        w(f"| Total users | {s['total_users']} |")
        w(f"| Successful | {s['successful']} |")
        w(f"| Failed | {s['failed']} |")
        w(f"| POST errors | {s['post_errors']} |")
        w(f"| Total polls | {s['total_polls']} ({s['avg_polls_per_user']}/user) |")
        w(f"| Wall duration | {s['wall_seconds']}s ({s['wall_minutes']} mnt) |")
        w(f"| Requests/sec | {s['requests_per_second']} |")
        w(f"| Success/sec | {s['successful_per_second']} |")
        w(f"| Throughput | {s['throughput_per_minute']} jobs/mnt |")
        w("")

        w("### Latensi POST")
        w("")
        if s["post"]:
            p = s["post"]
            w("| Metrik | Detik | Menit |")
            w("|--------|-------|-------|")
            w(
                f"| Mean | {p['mean']} | {p['mean']/60:.2f} |"
            )
            w(
                f"| Median (P50) | {p['median']} | {p['median']/60:.2f} |"
            )
            w(f"| Min | {p['min']} | {p['min']/60:.2f} |")
            w(f"| Max | {p['max']} | {p['max']/60:.2f} |")
            w(f"| P90 | {p['p90']} | {p['p90']/60:.2f} |")
            w(f"| P95 | {p['p95']} | {p['p95']/60:.2f} |")
            w(f"| P99 | {p['p99']} | {p['p99']/60:.2f} |")
        else:
            w("> Tidak ada data.")
        w("")

        w("### Latensi End-to-End (POST → GET Result)")
        w("")
        if s["e2e"]:
            e = s["e2e"]
            w("| Metrik | Detik | Menit |")
            w("|--------|-------|-------|")
            w(
                f"| Mean | {e['mean']} | {e['mean']/60:.2f} |"
            )
            w(
                f"| Median (P50) | {e['median']} | {e['median']/60:.2f} |"
            )
            w(f"| Min | {e['min']} | {e['min']/60:.2f} |")
            w(f"| Max | {e['max']} | {e['max']/60:.2f} |")
            w(f"| P90 | {e['p90']} | {e['p90']/60:.2f} |")
            w(f"| P95 | {e['p95']} | {e['p95']/60:.2f} |")
            w(f"| P99 | {e['p99']} | {e['p99']/60:.2f} |")
        else:
            w("> Tidak ada data.")
        w("")

    # Perbandingan jika multi file
    if len(all_stats) > 1:
        w("## Perbandingan")
        w("")
        w(
            "| File | Users | Succ | Fail | Req/s | Succ/s | TP/mnt | POST(s) | E2E(s) |"
        )
        w(
            "|------|-------|------|------|-------|--------|--------|---------|--------|"
        )
        for s in all_stats:
            fname = Path(s["file"]).name
            post_mean = s["post"].get("mean", "-") if s["post"] else "-"
            e2e_mean = s["e2e"].get("mean", "-") if s["e2e"] else "-"
            w(
                f"| {fname} | {s['total_users']} | {s['successful']} | {s['failed']} | {s['requests_per_second']} | {s['successful_per_second']} | {s['throughput_per_minute']} | {post_mean} | {e2e_mean} |"
            )
        w("")

    return "\n".join(lines)


def build_csv_rows(all_stats: list[dict]) -> list[list]:
    rows = []
    rows.append(
        [
            "file",
            "total_users",
            "successful",
            "failed",
            "post_errors",
            "total_polls",
            "avg_polls_per_user",
            "wall_seconds",
            "wall_minutes",
            "requests_per_second",
            "successful_per_second",
            "throughput_per_minute",
            "post_mean",
            "post_median",
            "post_min",
            "post_max",
            "post_p90",
            "post_p95",
            "post_p99",
            "e2e_mean",
            "e2e_median",
            "e2e_min",
            "e2e_max",
            "e2e_p90",
            "e2e_p95",
            "e2e_p99",
        ]
    )
    for s in all_stats:
        p = s["post"] or {}
        e = s["e2e"] or {}
        rows.append(
            [
                s["file"],
                s["total_users"],
                s["successful"],
                s["failed"],
                s["post_errors"],
                s["total_polls"],
                s["avg_polls_per_user"],
                s["wall_seconds"],
                s["wall_minutes"],
                s["requests_per_second"],
                s["successful_per_second"],
                s["throughput_per_minute"],
                p.get("mean", ""),
                p.get("median", ""),
                p.get("min", ""),
                p.get("max", ""),
                p.get("p90", ""),
                p.get("p95", ""),
                p.get("p99", ""),
                e.get("mean", ""),
                e.get("median", ""),
                e.get("min", ""),
                e.get("max", ""),
                e.get("p90", ""),
                e.get("p95", ""),
                e.get("p99", ""),
            ]
        )
    return rows


def print_console(all_stats: list[dict]):
    for s in all_stats:
        print("\n" + "=" * 55)
        print(f"  Analisis Load Test")
        print("=" * 55)
        print(f"  File           : {s['file']}")
        print(f"  Users (total)  : {s['total_users']}")
        print(f"  Successful     : {s['successful']}")
        print(f"  Failed         : {s['failed']}")
        print(f"  POST errors    : {s['post_errors']}")
        print(f"  Total polls    : {s['total_polls']} ({s['avg_polls_per_user']}/user)")
        print(f"  Wall duration  : {s['wall_seconds']}s ({s['wall_minutes']} mnt)")
        print(f"  ─────────────────────────────────────")
        print(f"  Requests/sec   : {s['requests_per_second']}")
        print(f"  Success/sec    : {s['successful_per_second']}")
        print(f"  Throughput     : {s['throughput_per_minute']} jobs/mnt")
        print(f"  ─────────── POST Latency ───────────")
        if s["post"]:
            p = s["post"]
            print(f"  Mean / Median  : {p['mean']}s / {p['median']}s")
            print(f"  Min / Max      : {p['min']}s / {p['max']}s")
            print(f"  P90 / P95 / P99: {p['p90']}s / {p['p95']}s / {p['p99']}s")
        else:
            print(f"  (tidak ada data)")
        print(f"  ────────── E2E Latency ──────────")
        if s["e2e"]:
            e = s["e2e"]
            print(f"  Mean / Median  : {e['mean']}s / {e['median']}s ({e['mean']/60:.2f}m / {e['median']/60:.2f}m)")
            print(f"  Min / Max      : {e['min']}s / {e['max']}s ({e['min']/60:.2f}m / {e['max']/60:.2f}m)")
            print(f"  P90 / P95 / P99: {e['p90']}s / {e['p95']}s / {e['p99']}s")
        else:
            print(f"  (tidak ada data)")
        print("=" * 55)

    if len(all_stats) > 1:
        print("\n" + "=" * 85)
        print("  Ringkasan Semua File")
        print("=" * 85)
        print(
            f"  {'File':30} {'Users':>6} {'Polls':>6} {'Req/s':>7} {'TP/mnt':>7} {'POST(s)':>8} {'E2E(s)':>8}"
        )
        print(
            f"  {'-'*30} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*8} {'-'*8}"
        )
        for s in all_stats:
            fname = Path(s["file"]).name
            post_mean = s["post"].get("mean", "-") if s["post"] else "-"
            e2e_mean = s["e2e"].get("mean", "-") if s["e2e"] else "-"
            print(
                f"  {fname:30} {s['total_users']:>6} {s['total_polls']:>6} {s['requests_per_second']:>7} {s['throughput_per_minute']:>7} {post_mean:>8} {e2e_mean:>8}"
            )
        print("=" * 85)
    print()


def save_outputs(all_stats: list[dict]):
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%Y%m%d_%H%M%S")

    date_dir = OUTPUT_DIR / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    source_name = (
        Path(all_stats[0]["file"]).parent.name
        if len(all_stats) == 1
        else "batch"
    )
    filename_base = f"analyze_{source_name}_{time_str}"

    md_path = date_dir / f"{filename_base}.md"
    csv_path = date_dir / f"{filename_base}.csv"

    # Markdown
    md_content = build_markdown(all_stats)
    md_path.write_text(md_content)

    # CSV
    csv_rows = build_csv_rows(all_stats)
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(csv_rows)

    return md_path, csv_path


def main():
    if len(sys.argv) < 2:
        print("Gunakan: python analyze_load.py <file.json atau direktori>")
        sys.exit(1)

    path = sys.argv[1]

    if os.path.isfile(path):
        stats = analyze_file(path)
        all_stats = [stats]

    elif os.path.isdir(path):
        json_files = sorted(Path(path).glob("*.json"))
        if not json_files:
            print(f"Tidak ada file .json di {path}")
            sys.exit(1)

        all_stats = []
        for jf in json_files:
            stats = analyze_file(str(jf))
            all_stats.append(stats)

    else:
        print(f"Path tidak ditemukan: {path}")
        sys.exit(1)

    # Console output
    print_console(all_stats)

    # Save files
    md_path, csv_path = save_outputs(all_stats)
    print(f"  Report    : {md_path}")
    print(f"  CSV       : {csv_path}")
    print()


if __name__ == "__main__":
    main()
