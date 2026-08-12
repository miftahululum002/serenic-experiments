#!/usr/bin/env python3
"""T3 — Titik jenuh worker parsing (step test, open-loop).

Ini yang menjawab pertanyaan "server sanggup berapa" dengan jujur.

Definisi kapasitas yang dipakai:

    lambda_max = laju kedatangan tertinggi yang membuat kedalaman antrean
                 TETAP DATAR selama satu langkah penuh (dQ/dt ~ 0).

Di atas lambda_max, API tetap membalas 200 dan latency HTTP tetap kecil, tapi
antrean tumbuh linear dan waktu tunggu user meledak. Itu sebabnya test ini
menilai dari kemiringan antrean, bukan dari status HTTP.

Beban dikirim open-loop: request tetap berangkat sesuai jadwal walaupun yang
sebelumnya belum selesai.

    python tests/t3_saturation.py --steps 60,120,240,480 --encounters 10 --step-duration 600
"""
import argparse
import asyncio
import json

from _common import (CohortCursor, JobIdRecorder, banner, check_isolation, downstream_depth,
                     load_context, measure_baseline, preflight, run_paths, save_summary)

from config import settings
from lib.csvlog import CsvLog, fmt_dur
from lib.driver import (arrival_offsets, make_client, post_json, run_open_loop, summarize,
                        warn_if_client_bound)
from lib.payload import build_update_request
from lib.sampler import QueueSampler

FIELDS = ("step_epm", "rate_rpm", "idx", "submit_ts", "latency_s", "status", "ok", "payload_kb",
          "schedule_lag_s")


async def run(args) -> None:
    template, cohort, probe = load_context()
    cursor = CohortCursor(cohort)
    steps_epm = [float(s) for s in args.steps.split(",") if s.strip()]

    banner("T3 — TITIK JENUH WORKER PARSING", template, {
        "Langkah": f"{steps_epm} encounter/menit",
        "Durasi": f"{args.step_duration}s per langkah",
        "Encounter": f"{args.encounters} per request",
        "Kedatangan": "Poisson" if args.poisson else "jarak tetap",
        "Ambang": f"kemiringan antrean <= {args.slope_threshold} job/menit dianggap stabil",
    })
    preflight(probe)
    baseline = measure_baseline(probe, args.baseline) if args.baseline > 0 else {}

    csv_path, queue_csv, summary_path = run_paths("t3_saturation")
    jobids_path = csv_path.with_name(csv_path.stem + "_jobids.txt")
    recorder = JobIdRecorder(probe, jobids_path)
    print(f"Job id test dicatat ke {jobids_path.name} (untuk tools/abort.py)\n")
    log = CsvLog(csv_path, FIELDS)
    sampler = QueueSampler(probe, queue_csv, args.sample_interval,
                           downstream=[settings.analysis_coordinator_queue, settings.analysis_queue])
    down_before = downstream_depth(probe)
    results = []
    unstable_streak = 0

    sampler.start()
    print(f"\n{'ENC/MNT':>8} {'REQ/MNT':>8} {'KIRIM':>6} {'GAGAL':>6} {'SELESAI':>8} "
          f"{'ANTREAN':>8} {'d ANTREAN':>10} {'SIBUK':>7} {'LAG':>9} {'STATUS':>10}")
    print("-" * 96)

    try:
        async with make_client(timeout_s=args.http_timeout) as client:
            for epm in steps_epm:
                phase = f"step_{epm:g}"
                rate_rpm = epm / args.encounters
                offsets = arrival_offsets(rate_rpm, args.step_duration, poisson=args.poisson)
                if not offsets:
                    continue

                # Serialisasi semua payload dulu; kalau dilakukan sambil jalan,
                # CPU client bisa membuat request telat dari jadwal.
                bodies = [json.dumps(build_update_request(
                    template, cursor.take(args.encounters), args.weight)).encode() for _ in offsets]
                prepared_mb = sum(len(b) for b in bodies) / 1e6
                if prepared_mb > 1500:
                    print(f"  PERINGATAN: langkah ini menyiapkan {prepared_mb:,.0f} MB payload di memori. "
                          "Perpendek --step-duration atau perkecil --encounters.")

                depth_before = probe.depth()
                sampler.mark(phase)

                async def fire(i: int, target_ts: float, _b=bodies):
                    return await post_json(client, settings.url_update, {}, settings.headers,
                                           settings.auth, idx=i, scheduled_ts=target_ts,
                                           encounters=args.encounters, body=_b[i])

                stats = await run_open_loop(offsets=offsets, fire=fire)
                s = summarize(stats, args.step_duration)
                recorder.capture()

                for r in stats.results:
                    log.write(step_epm=epm, rate_rpm=round(rate_rpm, 3), idx=r.idx,
                              submit_ts=round(r.submit_ts, 3), latency_s=round(r.latency_s, 3),
                              status=r.status_code, ok=int(r.ok),
                              payload_kb=round(r.request_bytes / 1024, 1),
                              schedule_lag_s=round(r.schedule_lag_s, 3))

                slope, r2, n_samples = sampler.slope_for_phase(phase)
                completed = sampler.completed_during(phase)
                avg_busy = sampler.avg_busy_for_phase(phase)
                snap = probe.snapshot()
                stable = slope == slope and slope <= args.slope_threshold

                warn = warn_if_client_bound(stats)
                verdict = "STABIL" if stable else "MENUMPUK"
                if warn:
                    verdict = "CLIENT?"

                print(f"{epm:>8.0f} {rate_rpm:>8.1f} {s['requests']:>6} {s['failed']:>6} "
                      f"{completed:>8} {snap.depth:>8} {slope:>+9.2f}/m "
                      f"{avg_busy:>4.1f}/{settings.worker_replicas:<2} "
                      f"{fmt_dur(snap.oldest_wait_seconds) if snap.oldest_wait_seconds else '-':>9} "
                      f"{verdict:>10}")
                if warn:
                    print(f"  {warn}")

                results.append({
                    "step_epm": epm, "rate_rpm": rate_rpm, "requests": s["requests"],
                    "failed": s["failed"], "http_p95_s": s["http_p95_s"],
                    "jobs_completed": completed, "depth_before": depth_before,
                    "depth_after": snap.depth, "queue_slope_per_min": slope, "slope_r2": r2,
                    "samples": n_samples, "oldest_wait_s": snap.oldest_wait_seconds,
                    "avg_workers_busy": avg_busy, "worker_replicas": settings.worker_replicas,
                    "stable": bool(stable), "client_bound": bool(warn),
                    "encounter_throughput_epm": completed * args.encounters / (args.step_duration / 60.0),
                })

                if not stable:
                    unstable_streak += 1
                    if unstable_streak >= args.stop_after_unstable:
                        print(f"\n  Berhenti: {unstable_streak} langkah berturut-turut menumpuk.")
                        break
                else:
                    unstable_streak = 0

        # Fase pengosongan: berapa lama sisa antrean habis setelah beban berhenti.
        sampler.mark("drain")
        print(f"\nMengosongkan antrean (maks {fmt_dur(args.drain_timeout)})...")
        import time as _t
        t_drain = _t.time()
        drained = probe.wait_until_drained(timeout=args.drain_timeout, quiet_for=10)
        drain_s = _t.time() - t_drain
        print(f"  {'selesai' if drained else 'TIMEOUT, masih ada sisa'} dalam {fmt_dur(drain_s)}")

    finally:
        sampler.stop()
        log.close()

    check_isolation(probe, down_before)

    stable_steps = [r for r in results if r["stable"] and not r["client_bound"] and r["failed"] == 0]
    lam = max((r["step_epm"] for r in stable_steps), default=None)

    print("\n" + "=" * 78)
    print("HASIL")
    print("=" * 78)
    if lam is None:
        print("  Tidak ada langkah yang stabil. Antrean sudah menumpuk sejak laju terendah.")
        print(f"  Turunkan titik awal, misalnya --steps {steps_epm[0]/4:g},{steps_epm[0]/2:g},{steps_epm[0]:g}")
    else:
        top = next(r for r in stable_steps if r["step_epm"] == lam)
        first_unstable = next((r["step_epm"] for r in results if not r["stable"]), None)
        print(f"  lambda_max (kapasitas berkelanjutan) : {lam:,.0f} encounter/menit")
        print(f"                                        = {lam*60:,.0f} encounter/jam")
        print(f"  Throughput terukur di titik itu      : {top['encounter_throughput_epm']:,.1f} encounter/menit")
        if first_unstable:
            print(f"  Mulai menumpuk di                    : {first_unstable:,.0f} encounter/menit")
        if baseline:
            bg = baseline["background_jobs_per_min"]
            print(f"\n  CATATAN: angka di atas adalah beban TEST yang bisa DITAMBAHKAN di atas")
            print(f"  beban asli yang sudah jalan ({bg:.2f} job/menit, {baseline['avg_busy']:.1f}/"
                  f"{baseline['replicas']} worker terpakai).")
            print(f"  Kapasitas total sistem = beban asli + {lam:,.0f} encounter/menit.")
            print("  Kalau ingin angka kapasitas absolut, ulangi di jam paling sepi ketika "
                  "beban asli mendekati nol.")

        print(f"\n  Rekomendasi beban operasional (70%)  : {lam*0.7:,.0f} encounter/menit "
              f"({lam*0.7*60:,.0f}/jam)")
        print("  Di atas 70% waktu tunggu naik jauh lebih cepat daripada beban — sifat antrean, "
              "bukan bug.")

    # --- Diagnosis: apakah jumlah replika yang membatasi? ---------------------
    replicas = settings.worker_replicas
    limit_step = next((r for r in results if not r["stable"]), results[-1] if results else None)

    print("\n" + "-" * 78)
    print("DIAGNOSIS BOTTLENECK")
    print("-" * 78)
    if limit_step is None:
        print("  Tidak ada data langkah.")
    else:
        busy = limit_step["avg_workers_busy"]
        print(f"  Saat {limit_step['step_epm']:,.0f} encounter/menit "
              f"({'menumpuk' if not limit_step['stable'] else 'langkah tertinggi'}), "
              f"rata-rata {busy:.1f} dari {replicas} worker sibuk.")
        if busy >= 0.85 * replicas:
            print(f"\n  -> TERBATAS JUMLAH WORKER. Keempat replika benar-benar terpakai.")
            print(f"     Menambah replika kemungkinan besar menaikkan kapasitas. "
                  f"Perkiraan dengan {replicas * 2} replika: ~{(lam or 0) * 2:,.0f} encounter/menit "
                  "(harus dibuktikan ulang, penskalaan jarang benar-benar linear).")
        else:
            print(f"\n  -> BUKAN TERBATAS JUMLAH WORKER. Antrean menumpuk padahal worker cuma "
                  f"{busy:.1f}/{replicas} sibuk.")
            print("     Worker sedang menunggu sumber daya bersama. Tersangka, urut dari yang "
                  "paling sering:")
            print("       1. connection pool Postgres — 4 worker + API berebut koneksi yang sama")
            print("       2. advisory lock per encounter — cek ada encounter yang sama di beberapa "
                  "request")
            print("       3. commit per 5 encounter di ingestor — kontensi tulis di DB")
            print("       4. disk shared_data — tiap request disimpan ke file")
            print("     Menambah replika di kondisi ini justru bisa memperburuk kontensi.")

    if args.theoretical_epm and lam:
        eff = 100.0 * lam / args.theoretical_epm
        print(f"\n  Efisiensi penskalaan: {lam:,.0f} / {args.theoretical_epm:,.0f} = {eff:.0f}% "
              f"dari ekstrapolasi linear T1 x{replicas} replika")
        if eff < 70:
            print("  Di bawah 70% — ada kontensi nyata antar replika. Jalankan T3 ulang pada "
                  "1 dan 2 replika untuk memetakan di mana penskalaannya patah.")

    print(f"\n  Mengubah jumlah replika:")
    print(f"    docker compose -f docker-compose.app.prod.yml up -d \\")
    print(f"      --scale emr-integration-data-parsing-worker_PROD=N")
    print("  Service ini tidak memakai container_name, jadi --scale bisa langsung dipakai.")
    print("  Jangan lupa perbarui WORKER_REPLICAS di .env setelahnya.")

    save_summary(summary_path, {
        "test": "t3_saturation", "encounters_per_request": args.encounters,
        "step_duration_s": args.step_duration, "slope_threshold": args.slope_threshold,
        "worker_replicas": replicas, "theoretical_epm": args.theoretical_epm,
        "baseline": baseline, "job_ids_file": str(jobids_path),
        "lambda_max_encounters_per_min": lam, "steps": results,
    })
    print(f"Detail   : {csv_path}")
    print(f"Antrean  : {queue_csv}")
    print(f"Grafik   : python analyze/report.py {queue_csv}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="30,60,120,240",
                    help="encounter/menit per langkah. Turunkan dari hasil T1: "
                         "mulai ~40%% kapasitas teoretis, naik 2x.")
    ap.add_argument("--encounters", type=int, default=10, help="encounter per request")
    ap.add_argument("--step-duration", type=float, default=600,
                    help="detik per langkah. Minimal 5x durasi satu job, kalau tidak kemiringannya "
                         "cuma derau.")
    ap.add_argument("--weight", type=int, default=1)
    ap.add_argument("--poisson", action="store_true")
    ap.add_argument("--slope-threshold", type=float, default=0.5,
                    help="job/menit; di bawah ini antrean dianggap datar")
    ap.add_argument("--stop-after-unstable", type=int, default=1)
    ap.add_argument("--theoretical-epm", type=float, default=0,
                    help="kapasitas armada hasil ekstrapolasi T1, untuk menghitung efisiensi penskalaan")
    ap.add_argument("--baseline", type=float, default=120,
                    help="detik pengukuran beban latar sebelum test. Wajib di produksi; "
                         "set 0 untuk melewatinya di lingkungan yang benar-benar kosong.")
    ap.add_argument("--sample-interval", type=float, default=2.0)
    ap.add_argument("--http-timeout", type=float, default=300)
    ap.add_argument("--drain-timeout", type=float, default=3600)
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
