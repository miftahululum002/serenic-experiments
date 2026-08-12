#!/usr/bin/env python3
"""T5 — Apakah menambah replika benar-benar menambah throughput?

Satu request = satu job RQ = SATU worker, berapa pun jumlah encounter di
dalamnya. Request berisi 500 encounter tetap dikerjakan satu worker sementara
tiga worker lain menganggur.

Artinya paralelisme sepenuhnya ditentukan oleh JUMLAH REQUEST BERSAMAAN, bukan
oleh ukuran request. Dua akibatnya:

  1. Kalau HIS mengirim satu request besar per jam, menambah replika worker
     tidak menaikkan apa pun. Yang harus diubah adalah cara pengirim memecah
     batch.
  2. Ekstrapolasi "4 replika = 4x kapasitas" adalah hipotesis. Bisa gagal
     karena worker berebut connection pool Postgres, advisory lock encounter,
     atau disk shared_data.

Test ini mengukur keduanya secara langsung: kirim C request bersamaan, ukur
throughput dan waktu layanan per job, lalu bandingkan antar tingkat C.

Sinyal paling diagnostik bukan throughput, tapi WAKTU LAYANAN PER JOB. Kalau
paralelisme sehat, job tunggal tetap selesai dalam waktu yang sama walaupun
ada 4 job berjalan bersamaan. Kalau waktunya membengkak, worker saling
menghambat lewat sumber daya bersama.

    python tests/t5_parallel_scaling.py --levels 1,2,4 --encounters 10
"""
import argparse
import asyncio
import json
import sys
import time

from _common import (CohortCursor, JobIdRecorder, banner, check_isolation, clear_line,
                     downstream_depth, load_context, preflight, run_paths, save_summary,
                     status_line)

from config import settings
from lib.csvlog import CsvLog, fmt_dur
from lib.driver import make_client, post_json
from lib.payload import build_update_request

FIELDS = ("level", "encounters_per_request", "jobs", "encounters_total", "send_s", "vanished",
          "wall_s", "throughput_epm", "mean_service_s", "max_service_s", "mean_wait_s",
          "s_per_encounter", "failed_jobs")


async def run_level(client, probe, template, cursor, c: int, args, recorder) -> dict | None:
    """Kirim c request bersamaan, tunggu semua job-nya selesai."""
    bodies = [json.dumps(build_update_request(
        template, cursor.take(args.encounters), args.weight)).encode() for _ in range(c)]

    tag = f"{c:>8} "
    before = probe.known_ids()
    t_send = time.time()

    # Fase kirim dipantau per-request. Dengan uvicorn --workers 2, endpoint
    # /encounters/update melakukan kerja sinkron yang berat sebelum enqueue
    # (simpan payload ke disk, validasi pydantic, parse_update_encounters_request),
    # jadi request bersamaan bisa antre di API itu sendiri — bukan di worker.
    # Tanpa progres di sini, fase ini tidak bisa dibedakan dari hang.
    acked = 0

    async def _send(i: int):
        nonlocal acked
        r = await post_json(client, settings.url_update, {}, settings.headers, settings.auth,
                            idx=i, encounters=args.encounters, body=bodies[i])
        acked += 1
        return r

    tasks = [asyncio.create_task(_send(i)) for i in range(c)]
    while not all(t.done() for t in tasks):
        status_line(tag, f"menunggu respons API {acked}/{c}", time.time() - t_send)
        await asyncio.sleep(0.5)
    sent = [t.result() for t in tasks]
    send_s = time.time() - t_send
    clear_line()
    bad = [r for r in sent if not r.ok]
    if bad:
        print(f"  {len(bad)}/{c} request GAGAL: HTTP {bad[0].status_code} {bad[0].error[:80]}")
        return None

    # Tunggu semua job muncul di Redis.
    deadline = time.time() + args.enqueue_timeout
    job_ids: set[str] = set()
    while time.time() < deadline:
        job_ids = probe.known_ids() - before
        if len(job_ids) >= c:
            break
        status_line(tag, f"job terlihat {len(job_ids)}/{c}", time.time() - t_send)
        time.sleep(0.25)
    clear_line()
    if len(job_ids) < c:
        print(f"  hanya {len(job_ids)}/{c} job terlihat di antrean dalam "
              f"{fmt_dur(args.enqueue_timeout)} — hasil tingkat ini dilewati.")
        return None
    recorder.capture()

    # Semua job dipantau serentak, bukan satu per satu menurut urutan id.
    # RQ menghapus hash job sukses setelah result_ttl (500 detik); memanen
    # berurutan membuat job yang selesai duluan hilang datanya.
    collected, vanished, unfinished = probe.collect_job_timings(
        job_ids, timeout=args.job_timeout,
        on_tick=lambda el, n, tot, s: status_line(
            tag, f"job selesai {n}/{tot}  antrean={s.depth} "
                 f"worker sibuk={s.workers_busy}/{s.workers_total}", time.time() - t_send),
    )
    clear_line()

    if vanished:
        print(f"  {len(vanished)} job selesai tapi hash-nya sudah kedaluwarsa (result_ttl 500s) "
              "— timingnya hilang.")
    if unfinished:
        print(f"  {len(unfinished)} job belum selesai dalam {fmt_dur(args.job_timeout)}.")

    timings = list(collected.values())
    done = [t for t in timings if t.service_seconds is not None and t.ended_at and t.enqueued_at]
    failed = [t for t in timings if t.status == "failed"]
    if not done:
        print("  tidak ada job yang selesai.")
        return None

    # Wall-clock diukur dari timestamp job, bukan dari sisi klien, supaya
    # variasi jaringan tidak ikut terhitung.
    wall = (max(t.ended_at for t in done) - min(t.enqueued_at for t in done)).total_seconds()
    services = [t.service_seconds for t in done]
    waits = [t.wait_seconds for t in done if t.wait_seconds is not None]
    total_enc = len(done) * args.encounters

    return {
        "level": c,
        "encounters_per_request": args.encounters,
        "jobs": len(done),
        "encounters_total": total_enc,
        "send_s": round(send_s, 2),
        "vanished": len(vanished),
        "wall_s": round(wall, 2),
        "throughput_epm": round(total_enc / (wall / 60), 2) if wall > 0 else 0.0,
        "mean_service_s": round(sum(services) / len(services), 2),
        "max_service_s": round(max(services), 2),
        "mean_wait_s": round(sum(waits) / len(waits), 2) if waits else 0.0,
        "s_per_encounter": round(sum(services) / len(services) / args.encounters, 3),
        "failed_jobs": len(failed),
    }


async def run(args) -> None:
    template, cohort, probe = load_context()
    cursor = CohortCursor(cohort)
    replicas = settings.worker_replicas
    # Diurutkan menaik: acuan speedup harus tingkat terkecil. Kalau tidak,
    # baris pertama yang kebetulan tingkat besar dipakai sebagai acuan dan
    # seluruh perbandingannya jadi tidak bermakna.
    levels = (sorted({int(x) for x in args.levels.split(",") if x.strip()})
              if args.levels else sorted({1, max(1, replicas // 2), replicas, replicas * 2}))

    needed = sum(levels) * args.encounters
    banner("T5 — PENSKALAAN PARALEL WORKER", template, {
        "Tingkat": f"{levels} request bersamaan",
        "Encounter": f"{args.encounters} per request",
        "Replika": replicas,
        "Butuh": f"{needed} encounter dari kohort ({len(cohort)} tersedia)",
    })

    # Encounter TIDAK boleh dipakai ulang di dalam satu tingkat: ingestor
    # memasang advisory lock per encounter, jadi dua request bersamaan yang
    # memuat encounter sama akan saling menunggu. Yang terukur jadi kontensi
    # lock, bukan penskalaan worker — dan hasilnya terlihat wajar padahal salah.
    if needed > len(cohort):
        sys.exit(
            f"\nKohort kurang: butuh {needed} encounter, tersedia {len(cohort)}.\n"
            "Encounter yang dipakai ulang di dalam satu tingkat akan saling mengunci\n"
            "(advisory lock per encounter di ingestor) dan hasil penskalaannya palsu.\n\n"
            f"  python tools/seed_encounters.py --count {needed}\n\n"
            f"Atau perkecil cakupan, mis. --levels {','.join(str(x) for x in levels[:3])} "
            f"--encounters {max(1, args.encounters // 2)}"
        )

    if max(levels) > replicas:
        over = [x for x in levels if x > replicas]
        print(f"CATATAN: tingkat {over} melebihi {replicas} replika. Kelebihannya mengantre,\n"
              f"         jadi tingkat itu mengukur SATURASI, bukan penskalaan. Throughput\n"
              f"         akan mendatar di sekitar kapasitas {replicas} worker — itu memang\n"
              f"         jawabannya, bukan kegagalan test.\n")

    preflight(probe)

    csv_path, _, summary_path = run_paths("t5_parallel_scaling")
    recorder = JobIdRecorder(probe, csv_path.with_name(csv_path.stem + "_jobids.txt"))
    log = CsvLog(csv_path, FIELDS)
    down_before = downstream_depth(probe)
    rows = []

    print(f"\n{'PARALEL':>8} {'JOB':>5} {'ENC':>6} {'KIRIM':>8} {'WALL':>9} {'ENC/MNT':>9} "
          f"{'LAYANAN/JOB':>12} {'DET/ENC':>8} {'ANTRE':>8}")
    print("-" * 85)

    async with make_client(timeout_s=args.http_timeout) as client:
        for c in levels:
            row = await run_level(client, probe, template, cursor, c, args, recorder)
            if row:
                log.write(**row)
                rows.append(row)
                print(f"{c:>8} {row['jobs']:>5} {row['encounters_total']:>6} "
                      f"{fmt_dur(row['send_s']):>8} "
                      f"{fmt_dur(row['wall_s']):>9} {row['throughput_epm']:>9.1f} "
                      f"{fmt_dur(row['mean_service_s']):>12} {row['s_per_encounter']:>8.2f} "
                      f"{fmt_dur(row['mean_wait_s']):>8}")
            probe.wait_until_drained(timeout=args.drain_timeout, quiet_for=3)

    log.close()
    check_isolation(probe, down_before)

    if len(rows) < 2:
        print("\nButuh minimal dua tingkat untuk membandingkan penskalaan.")
        return

    base = rows[0]
    print("\n" + "=" * 78)
    print("PENSKALAAN")
    print("=" * 78)
    print(f"  Acuan: {base['level']} request bersamaan -> {base['throughput_epm']:.1f} encounter/menit, "
          f"layanan {fmt_dur(base['mean_service_s'])}/job\n")
    print(f"  {'PARALEL':>8} {'SPEEDUP':>9} {'IDEAL':>7} {'EFISIENSI':>10} {'LAYANAN/JOB':>12} "
          f"{'PEMBENGKAKAN':>13}")
    print(f"  (ideal dibatasi {replicas} replika — di atas itu hanya mengantre)")
    print("  " + "-" * 66)

    for r in rows:
        speedup = r["throughput_epm"] / base["throughput_epm"] if base["throughput_epm"] else float("nan")
        # Ideal dibatasi jumlah replika: dengan 2 worker, 50 request bersamaan
        # tidak mungkin memberi 50x. Kelebihannya hanya mengantre.
        ideal = min(r["level"], replicas) / min(base["level"], replicas)
        eff = speedup / ideal if ideal else float("nan")
        inflation = r["mean_service_s"] / base["mean_service_s"] if base["mean_service_s"] else float("nan")
        r["speedup"] = round(speedup, 2)
        r["efficiency"] = round(eff, 3)
        r["service_inflation"] = round(inflation, 2)
        print(f"  {r['level']:>8} {speedup:>8.2f}x {ideal:>6.1f}x {eff*100:>9.0f}% "
              f"{fmt_dur(r['mean_service_s']):>12} {inflation:>12.2f}x")

    at_replicas = next((r for r in rows if r["level"] == replicas), rows[-1])
    eff = at_replicas["efficiency"]
    infl = at_replicas["service_inflation"]

    print("\n  DIAGNOSIS")
    print("  " + "-" * 66)
    if eff >= 0.85:
        print(f"  Pada {at_replicas['level']} request bersamaan, efisiensi {eff*100:.0f}% — "
              "penskalaan sehat.")
        print(f"  Ekstrapolasi kapasitas x{replicas} dari T1 bisa dipercaya, dan menambah "
              "replika kemungkinan besar masih menolong.")
    else:
        print(f"  Pada {at_replicas['level']} request bersamaan, efisiensi cuma {eff*100:.0f}% "
              f"dari ideal.")
        if infl > 1.3:
            print(f"  Waktu layanan per job membengkak {infl:.2f}x padahal jumlah encounter per "
                  "job tetap.")
            print("  Itu tanda worker saling menghambat, bukan sekadar antre. Tersangka:")
            print("    1. connection pool Postgres — worker + API berebut koneksi yang sama")
            print("    2. advisory lock encounter — cek tidak ada encounter yang sama antar request")
            print("    3. commit per 5 encounter di ingestor — kontensi tulis")
            print("    4. disk shared_data — tiap request disimpan ke file")
        else:
            print("  Waktu layanan per job stabil, jadi bukan kontensi antar worker. "
                  "Kemungkinan\n  jumlah worker yang benar-benar hidup lebih sedikit dari "
                  f"WORKER_REPLICAS={replicas}, atau ada job lain di antrean.")
        print(f"\n  Menambah replika di atas {at_replicas['level']} kemungkinan tidak sepadan "
              "sebelum penyebabnya dibereskan.")

    # Fase kirim yang lama menandakan API-nya yang jadi rem, bukan worker.
    slow_send = [r for r in rows if r["send_s"] > r["wall_s"] * 0.5]
    if slow_send:
        print("\n  SISI API JADI REM")
        print("  " + "-" * 66)
        for r in slow_send:
            print(f"  Pada {r['level']} request bersamaan, menunggu respons API "
                  f"{fmt_dur(r['send_s'])} dari total {fmt_dur(r['wall_s'])}.")
        print("  api-server jalan dengan uvicorn --workers 2, dan /encounters/update")
        print("  mengerjakan hal berat SECARA SINKRON sebelum enqueue: simpan payload ke")
        print("  disk, validasi pydantic, lalu parse_update_encounters_request.")
        print("  Jadi pada konkurensi tinggi request antre di API, bukan di worker parsing.")
        print("  Ukur batas ini tersendiri dengan T2 (t2_ingress.py).")

    print("\n  CATATAN UNTUK SISI PENGIRIM")
    print("  " + "-" * 66)
    print("  Paralelisme ditentukan jumlah request bersamaan, bukan ukuran request.")
    print(f"  Satu request berisi {args.encounters * replicas} encounter akan dikerjakan SATU "
          f"worker\n  dan memakan ~{fmt_dur(base['mean_service_s'] * replicas)}; "
          f"dipecah jadi {replicas} request, selesai ~{fmt_dur(at_replicas['mean_service_s'])}.")
    print(f"  Sarankan pengirim memecah batch jadi minimal {replicas} request bersamaan.")

    save_summary(summary_path, {
        "test": "t5_parallel_scaling", "worker_replicas": replicas,
        "encounters_per_request": args.encounters, "levels": levels, "rows": rows,
    })
    print(f"Detail   : {csv_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="",
                    help="jumlah request bersamaan, dipisah koma. Kosong = "
                         "diturunkan dari WORKER_REPLICAS (1, N/2, N, 2N)")
    ap.add_argument("--encounters", type=int, default=10, help="encounter per request")
    ap.add_argument("--weight", type=int, default=1)
    ap.add_argument("--http-timeout", type=float, default=900)
    ap.add_argument("--enqueue-timeout", type=float, default=120)
    ap.add_argument("--job-timeout", type=float, default=3600)
    ap.add_argument("--drain-timeout", type=float, default=300)
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
