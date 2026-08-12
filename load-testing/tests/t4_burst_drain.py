#!/usr/bin/env python3
"""T4 — Uji ledakan beban dan waktu pengosongan.

Menjawab pertanyaan operasional yang sebenarnya, bukan pertanyaan teknis:

    "Kalau jam 08.00 masuk 500 encounter sekaligus, selesai jam berapa?"

Berbeda dengan T3 yang mencari batas berkelanjutan, T4 mengukur perilaku
sistem saat menerima antrean besar sekaligus: laju pengosongan, waktu tunggu
encounter terakhir, dan apakah ada job yang gagal/terlantar.

    python tests/t4_burst_drain.py --total 500 --encounters 25
"""
import argparse
import asyncio
import json
import time

from _common import (CohortCursor, banner, check_isolation, clear_line, downstream_depth,
                     load_context, preflight, run_paths, save_summary, status_line)

from config import settings
from lib.csvlog import CsvLog, fmt_dur, percentile
from lib.driver import make_client, post_json, run_open_loop
from lib.payload import build_update_request
from lib.sampler import QueueSampler

FIELDS = ("idx", "submit_ts", "latency_s", "status", "ok", "payload_kb", "job_id", "job_status",
          "wait_s", "service_s", "total_s")


async def run(args) -> None:
    template, cohort, probe = load_context()
    cursor = CohortCursor(cohort)

    n_requests = (args.total + args.encounters - 1) // args.encounters
    banner("T4 — LEDAKAN BEBAN & WAKTU PENGOSONGAN", template, {
        "Total": f"{args.total} encounter",
        "Request": f"{n_requests} x {args.encounters} encounter",
        "Replika": settings.worker_replicas,
        "Pengiriman": f"disebar {args.spread}s" if args.spread else "sekaligus",
    })
    if n_requests < settings.worker_replicas:
        print(f"PERINGATAN: cuma {n_requests} job untuk {settings.worker_replicas} replika — "
              f"{settings.worker_replicas - n_requests} replika akan menganggur dan waktu "
              "pengosongan jadi lebih lama dari seharusnya.\n"
              f"            Perkecil --encounters supaya jumlah request minimal "
              f"{settings.worker_replicas}.\n")
    preflight(probe)

    csv_path, queue_csv, summary_path = run_paths("t4_burst_drain")
    log = CsvLog(csv_path, FIELDS)
    sampler = QueueSampler(probe, queue_csv, args.sample_interval,
                           downstream=[settings.analysis_coordinator_queue, settings.analysis_queue])
    down_before = downstream_depth(probe)

    bodies = [json.dumps(build_update_request(
        template, cursor.take(args.encounters), args.weight)).encode() for _ in range(n_requests)]
    total_mb = sum(len(b) for b in bodies) / 1e6
    print(f"Payload disiapkan: {n_requests} request, total {total_mb:.1f} MB\n")

    before_ids = probe.known_ids()
    jobids_path = csv_path.with_name(csv_path.stem + "_jobids.txt")
    sampler.start()
    sampler.mark("burst")
    t0 = time.time()

    try:
        async with make_client(timeout_s=args.http_timeout) as client:
            gap = (args.spread / n_requests) if args.spread else 0.0
            offsets = [i * gap for i in range(n_requests)]

            async def fire(i: int, target_ts: float):
                return await post_json(client, settings.url_update, {}, settings.headers,
                                       settings.auth, idx=i, scheduled_ts=target_ts,
                                       encounters=args.encounters, body=bodies[i])

            stats = await run_open_loop(offsets=offsets, fire=fire)

        submit_done = time.time()
        ok = [r for r in stats.results if r.ok]
        print(f"Terkirim: {len(ok)}/{n_requests} request dalam {fmt_dur(submit_done - t0)}")
        if len(ok) < n_requests:
            for r in stats.results:
                if not r.ok:
                    print(f"  GAGAL #{r.idx}: HTTP {r.status_code} {r.error[:80]}")

        # Kumpulkan job id yang tercipta dari burst ini.
        sampler.mark("drain")
        job_ids = sorted(probe.known_ids() - before_ids)
        jobids_path.write_text("".join(f"{j}\n" for j in job_ids))
        print(f"\n{len(job_ids)} job tercatat di {jobids_path.name} "
              "(pakai tools/abort.py kalau perlu dibatalkan)")
        print(f"Menunggu antrean kosong (maks {fmt_dur(args.drain_timeout)})...")

        # Timing dipanen SAMBIL mengosong, bukan sesudahnya. Ledakan besar
        # butuh lebih dari 500 detik, dan RQ menghapus hash job sukses setelah
        # result_ttl 500 detik — job yang selesai di awal akan hilang datanya
        # kalau baru dibaca setelah semuanya kelar. Diam-diam, dan persentilnya
        # jadi bias ke job yang selesai belakangan.
        collected, vanished, unfinished = probe.collect_job_timings(
            job_ids, timeout=args.drain_timeout,
            on_tick=lambda el, n, tot, s: status_line(
                "  ", f"job selesai {n}/{tot}  antrean={s.depth} "
                      f"worker sibuk={s.workers_busy}/{s.workers_total}", el),
        )
        clear_line()
        drained = probe.wait_until_drained(timeout=120, quiet_for=10)
        t_drained = time.time()

        if vanished:
            print(f"  {len(vanished)} job hash-nya kedaluwarsa sebelum sempat dibaca.")
        if unfinished:
            print(f"  {len(unfinished)} job belum selesai dalam "
                  f"{fmt_dur(args.drain_timeout)}.")

    finally:
        sampler.stop()

    timings = list(collected.values())
    finished = [t for t in timings if t.status == "finished" and t.service_seconds is not None]
    failed = [t for t in timings if t.status == "failed"]

    by_idx = {r.idx: r for r in stats.results}
    for i in range(n_requests):
        r = by_idx.get(i)
        if not r:
            continue
        log.write(idx=i, submit_ts=round(r.submit_ts, 3), latency_s=round(r.latency_s, 3),
                  status=r.status_code, ok=int(r.ok), payload_kb=round(r.request_bytes / 1024, 1),
                  job_id="", job_status="", wait_s="", service_s="", total_s="")
    for t in timings:
        log.write(idx="", submit_ts="", latency_s="", status="", ok="", payload_kb="",
                  job_id=t.job_id, job_status=t.status,
                  wait_s=round(t.wait_seconds, 2) if t.wait_seconds is not None else "",
                  service_s=round(t.service_seconds, 2) if t.service_seconds is not None else "",
                  total_s=round(t.total_seconds, 2) if t.total_seconds is not None else "")
    log.close()
    check_isolation(probe, down_before)

    drain_s = t_drained - t0
    encounters_done = len(finished) * args.encounters

    print("\n" + "=" * 78)
    print("HASIL")
    print("=" * 78)
    print(f"  Job tercipta        : {len(timings)}  (selesai {len(finished)}, gagal {len(failed)})")
    print(f"  Waktu pengosongan   : {fmt_dur(drain_s)}" + ("" if drained else "  <-- TIMEOUT, belum habis"))
    if drain_s > 0 and encounters_done:
        print(f"  Laju pengosongan    : {encounters_done / (drain_s/60):,.1f} encounter/menit "
              f"({encounters_done / (drain_s/3600):,.0f}/jam)")
    if finished:
        waits = [t.wait_seconds for t in finished if t.wait_seconds is not None]
        totals = [t.total_seconds for t in finished if t.total_seconds is not None]
        print(f"\n  Waktu tunggu antre  : p50 {fmt_dur(percentile(waits,50))}  "
              f"p95 {fmt_dur(percentile(waits,95))}  maks {fmt_dur(max(waits))}")
        print(f"  End-to-end per job  : p50 {fmt_dur(percentile(totals,50))}  "
              f"p95 {fmt_dur(percentile(totals,95))}  maks {fmt_dur(max(totals))}")
        print("\n  'End-to-end' = enqueued -> ended. Ini yang dirasakan pengguna, "
              "bukan latency HTTP yang di bawah ini.")
    http_lat = [r.latency_s for r in ok]
    if http_lat:
        print(f"  Latency HTTP        : p50 {percentile(http_lat,50):.2f}s  "
              f"p95 {percentile(http_lat,95):.2f}s   <-- hanya biaya terima+enqueue")
    if failed:
        print(f"\n  {len(failed)} job GAGAL. Periksa log worker; job gagal tidak otomatis diulang "
              "oleh RQ tanpa konfigurasi retry.")

    if args.slo_minutes and finished:
        totals = [t.total_seconds for t in finished if t.total_seconds is not None]
        within = sum(1 for x in totals if x <= args.slo_minutes * 60)
        pct = 100.0 * within / len(totals)
        print(f"\n  SLO {args.slo_minutes} menit: {pct:.1f}% job memenuhi ({within}/{len(totals)})")
        if pct < 95:
            factor = percentile(totals, 95) / (args.slo_minutes * 60)
            needed = max(settings.worker_replicas + 1,
                         int(-(-settings.worker_replicas * factor // 1)))
            print(f"  Di bawah 95%. p95 melewati SLO {factor:.1f}x, jadi perkiraan kasar butuh "
                  f"~{needed} replika (sekarang {settings.worker_replicas}).")
            print("  Angka ini mengasumsikan penskalaan linear — buktikan dulu lewat diagnosis "
                  "bottleneck di T3 sebelum menaikkan replika.")

    save_summary(summary_path, {
        "test": "t4_burst_drain", "total_encounters": args.total,
        "encounters_per_request": args.encounters, "requests": n_requests,
        "payload_mb": total_mb, "drained": drained, "drain_seconds": drain_s,
        "jobs_finished": len(finished), "jobs_failed": len(failed),
        "encounters_completed": encounters_done,
        "drain_rate_epm": encounters_done / (drain_s / 60) if drain_s > 0 else None,
    })
    print(f"Detail   : {csv_path}")
    print(f"Antrean  : {queue_csv}")
    print(f"Grafik   : python analyze/report.py {queue_csv}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=500, help="total encounter dalam ledakan")
    ap.add_argument("--encounters", type=int, default=25, help="encounter per request")
    ap.add_argument("--weight", type=int, default=1)
    ap.add_argument("--spread", type=float, default=0,
                    help="sebar pengiriman selama N detik (0 = kirim sekaligus)")
    ap.add_argument("--slo-minutes", type=float, default=0,
                    help="target SLO end-to-end, misal 30 untuk 'selesai dalam 30 menit'")
    ap.add_argument("--sample-interval", type=float, default=2.0)
    ap.add_argument("--http-timeout", type=float, default=900)
    ap.add_argument("--drain-timeout", type=float, default=7200)
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
