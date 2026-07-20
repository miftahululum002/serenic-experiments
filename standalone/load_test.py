"""
Load Testing: Standalone Coding API
====================================
Mengukur:
  1. Konkuensi: Berapa banyak user yang request dalam satu menit (throughput)
  2. Latensi: Waktu dari POST request sampai mendapatkan hasil via GET

Cara pakai:
  # Set API_KEY di .env lalu:
  python load_test.py --concurrency 10 --duration 60

  # Atau dengan flag --token:
  python load_test.py --concurrency 10 --token YOUR_API_KEY --duration 60
"""

import asyncio
import aiohttp
import argparse
import json
import os
import sys
import time
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


# ══════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════

BASE_URL = "https://api.serenic.ai/codex/standalone/v2"
POST_URL = f"{BASE_URL}/coding"
GET_URL_TEMPLATE = f"{BASE_URL}/coding/jobs/{{job_id}}"

DEFAULT_POLL_INTERVAL = 2  # seconds
DEFAULT_MAX_POLL_TIME = 1200  # 10 minutes max wait per job
DEFAULT_TIMEOUT = 30  # HTTP timeout in seconds


@dataclass
class RequestResult:
    job_id: Optional[str] = None
    post_sent_at: float = 0.0
    post_response_at: float = 0.0
    post_status: int = 0
    post_error: Optional[str] = None
    get_final_at: float = 0.0
    get_final_status: int = 0
    get_final_error: Optional[str] = None
    job_status: Optional[str] = None
    end_to_end_seconds: float = 0.0
    post_latency_seconds: float = 0.0
    total_polls: int = 0
    success: bool = False
    payload: Optional[dict] = None
    post_response: Optional[dict] = None
    get_final_response: Optional[dict] = None


@dataclass
class TestConfig:
    concurrency: int = 5
    duration_seconds: int = 60
    token: str = ""
    payload_path: str = ""
    poll_interval: float = DEFAULT_POLL_INTERVAL
    max_poll_time: float = DEFAULT_MAX_POLL_TIME
    http_timeout: float = DEFAULT_TIMEOUT
    ramp_up_seconds: float = 0.0
    output_dir: str = ""


# ══════════════════════════════════════════════════════════════
# Sample Payload
# ══════════════════════════════════════════════════════════════

SAMPLE_PAYLOAD = {
    "diagnosis_texts": [
        "Kanker esofagus",
        "Anemia pada penyakit kronis",
        "Diabetes mellitus tipe 2",
    ],
    "procedure_texts": ["Kolonoskopi dengan biopsi"],
    "gender": "Laki-laki",
    "birth_date": "1970-05-12T00:00:00Z",
    "admission_type": "inpatient",
    "admission_dttm": "2026-02-20T08:00:00Z",
    "discharge_dttm": "2026-02-27T10:00:00Z",
    "bpjs_class": 2,
    "birth_weight": 2000,
}


def load_payload(path: str) -> dict:
    if path and os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return SAMPLE_PAYLOAD


# ══════════════════════════════════════════════════════════════
# Core Testing Logic
# ══════════════════════════════════════════════════════════════


async def post_job(
    session: aiohttp.ClientSession,
    payload: dict,
    result: RequestResult,
    timeout: float,
):
    """Send POST request and record timing."""
    result.post_sent_at = time.monotonic()
    result.post_sent_time = datetime.now(timezone.utc).isoformat()
    result.payload = payload.copy()
    try:
        headers = {
            "apiKey": result._token,
            "Content-Type": "application/json",
        }
        async with session.post(
            POST_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            result.post_response_at = time.monotonic()
            result.post_response_time = datetime.now(timezone.utc).isoformat()
            result.post_status = resp.status
            body = await resp.json()
            result.post_response = body
            if resp.status in (200, 201):
                result.job_id = (
                    body.get("job_id")
                    or body.get("id")
                    or body.get("data", {}).get("job_id")
                    or body.get("data", {}).get("id")
                )
            else:
                result.post_error = f"HTTP {resp.status}: {json.dumps(body)[:200]}"
    except Exception as e:
        result.post_response_at = time.monotonic()
        result.post_error = str(e)[:200]

    result.post_latency_seconds = result.post_response_at - result.post_sent_at


async def poll_job(
    session: aiohttp.ClientSession,
    result: RequestResult,
    interval: float,
    max_time: float,
    timeout: float,
):
    """Poll GET endpoint until job completes or timeout."""
    if not result.job_id:
        return

    url = GET_URL_TEMPLATE.format(job_id=result.job_id)
    headers = {"apiKey": result._token}
    start = time.monotonic()

    while True:
        elapsed = time.monotonic() - start
        if elapsed > max_time:
            result.get_final_error = f"Poll timeout after {max_time}s"
            break

        result.total_polls += 1
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                result.get_final_status = resp.status
                body = await resp.json()
                status_val = (
                    body.get("status") or body.get("data", {}).get("status") or ""
                )
                result.job_status = status_val

                if "FINISHED" in str(status_val).upper():
                    result.get_final_at = time.monotonic()
                    result.get_final_time = datetime.now(timezone.utc).isoformat()
                    result.end_to_end_seconds = (
                        result.get_final_at - result.post_sent_at
                    )
                    result.get_final_response = body
                    result.success = True
                    return
                elif "FAILED" in str(status_val).upper():
                    result.get_final_at = time.monotonic()
                    result.get_final_time = datetime.now(timezone.utc).isoformat()
                    result.end_to_end_seconds = (
                        result.get_final_at - result.post_sent_at
                    )
                    result.get_final_response = body
                    result.get_final_error = f"Job FAILED: {json.dumps(body)[:200]}"
                    return
        except Exception as e:
            result.get_final_error = str(e)[:200]

        await asyncio.sleep(interval)

    result.get_final_at = time.monotonic()
    result.end_to_end_seconds = result.get_final_at - result.post_sent_at


async def run_single_user(
    user_id: int,
    config: TestConfig,
    payload: dict,
    results: list,
):
    """Run a single user: POST + poll GET."""
    result = RequestResult()
    result._token = config.token  # inject token

    async with aiohttp.ClientSession() as session:
        # POST
        await post_job(session, payload, result, config.http_timeout)

        if result.post_error or not result.job_id:
            result.end_to_end_seconds = result.post_latency_seconds
            results.append(result)
            return

        # Poll GET
        await poll_job(
            session,
            result,
            config.poll_interval,
            config.max_poll_time,
            config.http_timeout,
        )

    results.append(result)


async def run_load_test(config: TestConfig) -> list[RequestResult]:
    """Execute the load test with concurrent users."""
    payload = load_payload(config.payload_path)
    results: list[RequestResult] = []

    print(f"\n{'='*60}")
    print(f"  LOAD TEST: Standalone Coding API")
    print(f"{'='*60}")
    print(f"  Concurrency   : {config.concurrency} users")
    print(f"  Duration      : {config.duration_seconds}s (ramp-up)")
    print(f"  Poll interval : {config.poll_interval}s")
    print(f"  Max poll time : {config.max_poll_time}s")
    print(f"  HTTP timeout  : {config.http_timeout}s")
    print(f"  Payload       : {config.payload_path or 'sample'}")
    print(f"  Started at    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    start_time = time.monotonic()

    # Create tasks with optional ramp-up
    tasks = []
    for i in range(config.concurrency):
        # Ramp-up: stagger requests if configured
        delay = 0
        if config.ramp_up_seconds > 0:
            delay = (config.ramp_up_seconds / config.concurrency) * i

        if delay > 0:
            task = asyncio.create_task(
                _delayed_task(delay, i, config, payload, results)
            )
        else:
            task = asyncio.create_task(run_single_user(i, config, payload, results))
        tasks.append(task)

    # Wait for all tasks with progress reporting
    done_count = 0
    pending = set(tasks)

    while pending:
        done, pending = await asyncio.wait(pending, timeout=1.0)
        done_count += len(done)
        elapsed = time.monotonic() - start_time
        sys.stdout.write(
            f"\r  Progress: {done_count}/{config.concurrency} jobs submitted ({elapsed:.1f}s)"
        )
        sys.stdout.flush()

    # Wait for all polling to complete
    total_jobs = len(results)
    print(
        f"\n\n  All {total_jobs} jobs submitted. Waiting for polling to complete...\n"
    )

    # Wait until all results have end_to_end_seconds > 0 or error
    max_wait = config.max_poll_time + 30
    wait_start = time.monotonic()
    while (time.monotonic() - wait_start) < max_wait:
        completed = sum(
            1
            for r in results
            if r.end_to_end_seconds > 0 or r.post_error or r.get_final_error
        )
        if completed >= total_jobs:
            break
        await asyncio.sleep(1)
        elapsed = time.monotonic() - wait_start
        sys.stdout.write(
            f"\r  Waiting: {completed}/{total_jobs} completed ({elapsed:.1f}s)"
        )
        sys.stdout.flush()

    total_time = time.monotonic() - start_time
    print(f"\n\n  Test completed in {total_time:.1f}s\n")

    return results


async def _delayed_task(delay, user_id, config, payload, results):
    await asyncio.sleep(delay)
    await run_single_user(user_id, config, payload, results)


# ══════════════════════════════════════════════════════════════
# Analysis & Report
# ══════════════════════════════════════════════════════════════


def analyze_results(results: list[RequestResult], config: TestConfig) -> str:
    """Generate analysis report in markdown."""
    lines = []

    def w(text=""):
        lines.append(text)

    total = len(results)
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    post_errors = [r for r in results if r.post_error]
    poll_timeouts = [
        r
        for r in results
        if r.get_final_error and "timeout" in str(r.get_final_error).lower()
    ]
    job_failed = [
        r for r in results if r.get_final_error and "FAILED" in str(r.get_final_error)
    ]

    post_latencies = [
        r.post_latency_seconds for r in results if r.post_latency_seconds > 0
    ]
    e2e_latencies = [r.end_to_end_seconds for r in results if r.end_to_end_seconds > 0]
    poll_counts = [r.total_polls for r in results]

    # Throughput calculation
    if e2e_latencies:
        test_duration = max(e2e_latencies)  # total wall time
        throughput_per_min = (
            (len(successful) / test_duration * 60) if test_duration > 0 else 0
        )
    else:
        throughput_per_min = 0

    w("# Load Test Report: Standalone Coding API")
    w("")
    w(f"> **Tanggal:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
    w(
        f"> **Konfigurasi:** {config.concurrency} concurrent users, poll interval {config.poll_interval}s, max wait {config.max_poll_time}s"
    )
    w("")

    # ── Summary ──
    w("## 1. Ringkasan")
    w("")
    w("| Metrik | Nilai |")
    w("|--------|-------|")
    w(f"| Total requests | {total} |")
    w(f"| Berhasil (FINISHED) | {len(successful)} ({len(successful)/total*100:.1f}%) |")
    w(f"| Gagal (FAILED) | {len(job_failed)} ({len(job_failed)/total*100:.1f}%) |")
    w(f"| POST error | {len(post_errors)} ({len(post_errors)/total*100:.1f}%) |")
    w(f"| Poll timeout | {len(poll_timeouts)} ({len(poll_timeouts)/total*100:.1f}%) |")
    w(f"| Throughput | {throughput_per_min:.1f} jobs/menit |")
    w("")

    # ── POST Latency ──
    w("## 2. Latensi POST Request")
    w("")
    if post_latencies:
        pl = post_latencies
        w("| Metrik | Nilai |")
        w("|--------|-------|")
        w(f"| Mean | {statistics.mean(pl):.3f}s |")
        w(f"| Median (P50) | {statistics.median(pl):.3f}s |")
        w(f"| Min | {min(pl):.3f}s |")
        w(f"| Max | {max(pl):.3f}s |")
        if len(pl) >= 4:
            sorted_pl = sorted(pl)
            p90_idx = int(len(sorted_pl) * 0.9)
            p95_idx = int(len(sorted_pl) * 0.95)
            p99_idx = int(len(sorted_pl) * 0.99)
            w(f"| P90 | {sorted_pl[p90_idx]:.3f}s |")
            w(f"| P95 | {sorted_pl[min(p95_idx, len(sorted_pl)-1)]:.3f}s |")
            w(f"| P99 | {sorted_pl[min(p99_idx, len(sorted_pl)-1)]:.3f}s |")
        w(f"| Std Dev | {statistics.stdev(pl):.3f}s |" if len(pl) > 1 else "")
        w(f"| Total POST sent | {len(pl)} |")
    else:
        w("> Tidak ada data POST latency yang valid.")
    w("")

    # ── End-to-End Latency ──
    w("## 3. Latensi End-to-End (POST → GET Result)")
    w("")
    w("Waktu dari mengirim POST request hingga job selesai (FINISHED) di GET.")
    w("")
    if e2e_latencies:
        el = e2e_latencies
        w("| Metrik | Nilai |")
        w("|--------|-------|")
        w(f"| Mean | {statistics.mean(el):.1f}s ({statistics.mean(el)/60:.1f} menit) |")
        w(
            f"| Median (P50) | {statistics.median(el):.1f}s ({statistics.median(el)/60:.1f} menit) |"
        )
        w(f"| Min | {min(el):.1f}s |")
        w(f"| Max | {max(el):.1f}s ({max(el)/60:.1f} menit) |")
        if len(el) >= 4:
            sorted_el = sorted(el)
            p90_idx = int(len(sorted_el) * 0.9)
            p95_idx = int(len(sorted_el) * 0.95)
            p99_idx = int(len(sorted_el) * 0.99)
            w(
                f"| P90 | {sorted_el[p90_idx]:.1f}s ({sorted_el[p90_idx]/60:.1f} menit) |"
            )
            w(f"| P95 | {sorted_el[min(p95_idx, len(sorted_el)-1)]:.1f}s |")
            w(f"| P99 | {sorted_el[min(p99_idx, len(sorted_el)-1)]:.1f}s |")
        w(f"| Std Dev | {statistics.stdev(el):.1f}s |" if len(el) > 1 else "")
        w("")

        # Latency buckets
        w("### Bucket Latensi End-to-End")
        w("")
        bins = [0, 30, 60, 120, 300, 600, float("inf")]
        labels = ["<30s", "30-60s", "1-2m", "2-5m", "5-10m", ">10m"]
        bucket_counts = {}
        for label in labels:
            bucket_counts[label] = 0
        for lat in el:
            for i, b in enumerate(bins[:-1]):
                if b <= lat < bins[i + 1]:
                    bucket_counts[labels[i]] += 1
                    break

        w("| Bucket | Jumlah | Persentase | Grafik |")
        w("|--------|--------|------------|--------|")
        for label in labels:
            cnt = bucket_counts[label]
            if cnt > 0 or True:  # show all buckets
                pct = cnt / len(el) * 100 if el else 0
                bar = "█" * max(1, int(pct / 3)) if cnt > 0 else ""
                w(f"| {label} | {cnt} | {pct:.1f}% | {bar} |")
        w("")
    else:
        w("> Tidak ada data end-to-end latency yang valid.")
    w("")

    # ── Concurrency / Throughput ──
    w("## 4. Analisis Konkuensi (Throughput)")
    w("")
    w("### Throughput per Menit")
    w("")

    # Group successful completions by minute
    if successful:
        min_timestamp = min(r.post_sent_at for r in results)
        minute_buckets = {}
        for r in successful:
            minute = int((r.get_final_at - min_timestamp) / 60)
            minute_buckets[minute] = minute_buckets.get(minute, 0) + 1

        w("| Menit ke- | Jobs Selesai | Kumulatif | Grafik |")
        w("|-----------|-------------|-----------|--------|")
        cumulative = 0
        for m in sorted(minute_buckets.keys()):
            cnt = minute_buckets[m]
            cumulative += cnt
            bar = "█" * max(1, cnt)
            w(f"| {m+1} | {cnt} | {cumulative} | {bar} |")
        w("")

    # ── Per-User Detail ──
    w("### Detail Per User (Top 10 Slowest)")
    w("")
    w("| # | POST Status | Job Status | POST Latency | E2E Latency | Polls | Error |")
    w("|---|-------------|------------|-------------|-------------|-------|-------|")
    sorted_by_e2e = sorted(
        results,
        key=lambda r: r.end_to_end_seconds if r.end_to_end_seconds > 0 else 9999,
        reverse=True,
    )
    for i, r in enumerate(sorted_by_e2e[:10]):
        status = r.job_status or "N/A"
        e2e = f"{r.end_to_end_seconds:.1f}s" if r.end_to_end_seconds > 0 else "N/A"
        err = r.post_error or r.get_final_error or ""
        err_short = err[:40] + "..." if len(err) > 40 else err
        w(
            f"| {i+1} | {r.post_status} | {status} | {r.post_latency_seconds:.3f}s | {e2e} | {r.total_polls} | {err_short} |"
        )
    w("")

    # ── Error Analysis ──
    if post_errors or job_failed or poll_timeouts:
        w("## 5. Analisis Error")
        w("")
        if post_errors:
            w(f"### POST Errors ({len(post_errors)})")
            w("")
            err_types = {}
            for r in post_errors:
                err = r.post_error or "unknown"
                err_types[err] = err_types.get(err, 0) + 1
            w("| Error | Jumlah |")
            w("|-------|--------|")
            for err, cnt in sorted(err_types.items(), key=lambda x: -x[1]):
                w(f"| {err[:80]} | {cnt} |")
            w("")
        if job_failed:
            w(f"### Job FAILURES ({len(job_failed)})")
            w("")
            w("| Job ID | POST Latency | Error |")
            w("|--------|-------------|-------|")
            for r in job_failed[:20]:
                w(
                    f"| {r.job_id or 'N/A'} | {r.post_latency_seconds:.3f}s | {str(r.get_final_error)[:60]} |"
                )
            w("")
        if poll_timeouts:
            w(f"### Poll Timeouts ({len(poll_timeouts)})")
            w("")

    # ── Kesimpulan ──
    w("## 6. Kesimpulan")
    w("")
    w(
        f"- **{config.concurrency}** user concurrent mengirim request ke Standalone Coding API."
    )
    if successful:
        w(
            f"- **{len(successful)}** dari **{total}** job berhasil selesai ({len(successful)/total*100:.1f}%)."
        )
        w(f"- Rata-rata waktu POST: **{statistics.mean(post_latencies):.3f}s**")
        w(
            f"- Rata-rata waktu end-to-end: **{statistics.mean(e2e_latencies):.1f}s** ({statistics.mean(e2e_latencies)/60:.1f} menit)"
        )
        w(
            f"- Median waktu end-to-end: **{statistics.median(e2e_latencies):.1f}s** ({statistics.median(e2e_latencies)/60:.1f} menit)"
        )
        w(f"- Throughput: **{throughput_per_min:.1f} jobs/menit**")
    else:
        w("- Tidak ada job yang berhasil selesai.")
    w("")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# CSV Export
# ══════════════════════════════════════════════════════════════


def export_csv(results: list[RequestResult], output_path: str):
    """Export results to CSV for further analysis."""
    import csv

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "job_id",
                "post_sent_time",
                "post_status",
                "post_response_time",
                "post_latency_seconds",
                "post_latency_minutes",
                "post_error",
                "job_status",
                "get_final_time",
                "end_to_end_seconds",
                "end_to_end_minutes",
                "total_polls",
                "get_final_error",
                "success",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.job_id or "",
                    getattr(r, "post_sent_time", ""),
                    r.post_status,
                    getattr(r, "post_response_time", ""),
                    f"{r.post_latency_seconds:.4f}",
                    (
                        f"{r.post_latency_seconds / 60:.2f}"
                        if r.post_latency_seconds > 0
                        else ""
                    ),
                    r.post_error or "",
                    r.job_status or "",
                    getattr(r, "get_final_time", ""),
                    f"{r.end_to_end_seconds:.4f}" if r.end_to_end_seconds > 0 else "",
                    (
                        f"{r.end_to_end_seconds / 60:.2f}"
                        if r.end_to_end_seconds > 0
                        else ""
                    ),
                    r.total_polls,
                    r.get_final_error or "",
                    r.success,
                ]
            )


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════


def parse_args():
    parser = argparse.ArgumentParser(
        description="Load Testing: Standalone Coding API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic: set API_KEY in .env, 5 concurrent users
  python load_test.py --concurrency 5

  # Heavy load: 50 concurrent users, 5 min max poll
  python load_test.py --concurrency 50 --max-poll-time 300

  # With custom payload
  python load_test.py --concurrency 10 --payload my_payload.json

  # Explicit token
  python load_test.py --concurrency 10 --token YOUR_API_KEY
        """,
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent users (default: 5)",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=int,
        default=60,
        help="Duration for ramp-up in seconds (default: 60)",
    )
    parser.add_argument(
        "-t",
        "--token",
        type=str,
        default="",
        help="API key (or set API_KEY in .env file)",
    )
    parser.add_argument(
        "-p",
        "--payload",
        type=str,
        default="",
        help="Path to JSON payload file (uses sample if not provided)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {DEFAULT_POLL_INTERVAL})",
    )
    parser.add_argument(
        "--max-poll-time",
        type=float,
        default=DEFAULT_MAX_POLL_TIME,
        help=f"Max poll time per job in seconds (default: {DEFAULT_MAX_POLL_TIME})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--ramp-up",
        type=float,
        default=0,
        help="Ramp-up time in seconds (stagger requests)",
    )
    parser.add_argument(
        "--output-dir", type=str, default="", help="Output directory for reports"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    token = args.token or os.environ.get("API_KEY", "")
    if not token:
        print("ERROR: API key required. Use --token or set API_KEY in .env file.")
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "load_test_results"
    )
    os.makedirs(output_dir, exist_ok=True)

    config = TestConfig(
        concurrency=args.concurrency,
        duration_seconds=args.duration,
        token=token,
        payload_path=args.payload,
        poll_interval=args.poll_interval,
        max_poll_time=args.max_poll_time,
        http_timeout=args.timeout,
        ramp_up_seconds=args.ramp_up,
        output_dir=output_dir,
    )

    # Run test
    results = asyncio.run(run_load_test(config))

    # Generate report
    report = analyze_results(results, config)
    date_str = datetime.now().strftime("%Y%m%d")
    time_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    date_dir = os.path.join(output_dir, date_str)
    os.makedirs(date_dir, exist_ok=True)

    filename_base = f"load_test_{args.concurrency}_{time_str}"
    report_path = os.path.join(date_dir, f"{filename_base}.md")
    csv_path = os.path.join(date_dir, f"{filename_base}.csv")
    json_path = os.path.join(date_dir, f"{filename_base}.json")

    with open(report_path, "w") as f:
        f.write(report)

    export_csv(results, csv_path)

    # Export raw JSON
    raw_data = []
    for r in results:
        raw_data.append(
            {
                "job_id": r.job_id,
                "payload": r.payload,
                "post_sent_time": getattr(r, "post_sent_time", None),
                "post_status": r.post_status,
                "post_response_time": getattr(r, "post_response_time", None),
                "post_response": r.post_response,
                "post_latency_seconds": r.post_latency_seconds,
                "post_latency_minutes": (
                    round(r.post_latency_seconds / 60, 2)
                    if r.post_latency_seconds > 0
                    else None
                ),
                "post_error": r.post_error,
                "job_status": r.job_status,
                "get_final_time": getattr(r, "get_final_time", None),
                "get_final_response": r.get_final_response,
                "end_to_end_seconds": r.end_to_end_seconds,
                "end_to_end_minutes": (
                    round(r.end_to_end_seconds / 60, 2)
                    if r.end_to_end_seconds > 0
                    else None
                ),
                "total_polls": r.total_polls,
                "get_final_error": r.get_final_error,
                "success": r.success,
            }
        )
    with open(json_path, "w") as f:
        json.dump(raw_data, f, indent=2)

    print(report)
    print(f"\n{'='*60}")
    print(f"  Report    : {report_path}")
    print(f"  CSV       : {csv_path}")
    print(f"  JSON      : {json_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
