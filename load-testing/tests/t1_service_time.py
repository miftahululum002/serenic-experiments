#!/usr/bin/env python3
"""T1 — Karakterisasi waktu layanan worker parsing (sweep ukuran batch).

INI TEST PALING PENTING. Semua angka kapasitas lain diturunkan dari sini.

Cara kerja: kirim SATU request berisi N encounter, tunggu job parsing-nya
selesai, ukur durasinya dari registry RQ. Ulangi untuk beberapa nilai N.

Yang dicari:
  - biaya marjinal per encounter (detik/encounter) -> kemiringan regresi
  - overhead tetap per job (detik)                 -> intercept regresi
  - apakah biayanya linear terhadap N              -> R^2

Dari sini kapasitas satu replika worker = 3600 / (detik per encounter)
encounter per jam. Kalikan jumlah replika, bagi dengan target utilisasi 0.7.

    python tests/t1_service_time.py --sizes 1,5,10,25,50 --repeats 2
"""
import argparse
import asyncio
import json

from _common import (CohortCursor, banner, check_isolation, clear_line, downstream_depth,
                     load_context, preflight, run_paths, save_summary, status_line)

from config import settings
from lib.csvlog import CsvLog, fmt_dur, linreg
from lib.driver import make_client, post_json
from lib.payload import build_update_request

FIELDS = ("size", "rep", "weight", "payload_kb", "http_status", "http_latency_s", "job_id",
          "job_status", "wait_s", "service_s", "total_s", "s_per_encounter")

def _status(n: int, rep: int, msg: str, elapsed: float) -> None:
    status_line(f"{n:>4} {rep:>4} ", msg, elapsed)


def _clear() -> None:
    clear_line()


async def run(args) -> None:
    template, cohort, probe = load_context()
    cursor = CohortCursor(cohort)
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    banner("T1 — WAKTU LAYANAN WORKER PARSING", template, {
        "Ukuran": sizes, "Ulangan": args.repeats, "Bobot": f"{args.weight}x",
        "Kohort": f"{len(cohort)} encounter",
    })
    preflight(probe)

    csv_path, _, summary_path = run_paths("t1_service_time")
    log = CsvLog(csv_path, FIELDS)
    rows: list[dict] = []
    down_before = downstream_depth(probe)

    print(f"\n{'N':>4} {'REP':>4} {'PAYLOAD':>9} {'HTTP':>7} {'TUNGGU':>8} {'PROSES':>9} "
          f"{'TOTAL':>9} {'DET/ENC':>8}")
    print("-" * 68)

    async with make_client(timeout_s=args.http_timeout) as client:
        for n in sizes:
            for rep in range(args.repeats):
                identities = cursor.take(n)
                payload = build_update_request(template, identities, args.weight,
                                               force_ingest_completed=not args.no_force)
                body = json.dumps(payload).encode()

                # Foto job id yang sudah ada, supaya job baru bisa dikenali.
                before_ids = probe.known_ids()

                res = await post_json(client, settings.url_update, payload, settings.headers,
                                      settings.auth, encounters=n, body=body)
                if not res.ok:
                    print(f"{n:>4} {rep:>4}   HTTP {res.status_code}: {res.error[:60]}")
                    log.write(size=n, rep=rep, weight=args.weight, payload_kb=round(len(body) / 1024, 1),
                              http_status=res.status_code, http_latency_s=round(res.latency_s, 3),
                              job_id="", job_status="not_enqueued", wait_s="", service_s="",
                              total_s="", s_per_encounter="")
                    continue

                job_id = probe.wait_for_new_job(
                    before_ids, timeout=args.enqueue_timeout,
                    on_tick=lambda el, s: _status(
                        n, rep, f"menunggu job muncul  antrean={s.depth} jalan={s.started}", el),
                )
                if not job_id:
                    _clear()
                    after = probe.snapshot()
                    print(f"{n:>4} {rep:>4}   HTTP 200 tapi tidak ada job baru di antrean "
                          f"'{settings.parsing_queue}' dalam {fmt_dur(args.enqueue_timeout)}.")
                    print(f"          antrean={after.depth} jalan={after.started} "
                          f"selesai={after.finished} gagal={after.failed} "
                          f"worker={after.workers_busy}/{after.workers_total}")
                    print("          Kemungkinan: IS_CODEX_API_V2_ADD_TO_DB=false di API target, "
                          "atau PARSING_QUEUE salah,\n"
                          "          atau Redis yang dibaca harness bukan Redis yang dipakai API.")
                    continue

                timing = probe.wait_until_done(
                    job_id, timeout=args.job_timeout,
                    on_tick=lambda el, j, s: _status(
                        n, rep,
                        f"job {job_id[:8]} status={j.status if j else '?'}  "
                        f"worker sibuk={s.workers_busy}/{s.workers_total}", el),
                )
                _clear()
                if not timing or not timing.service_seconds:
                    print(f"{n:>4} {rep:>4}   job {job_id[:8]} belum selesai dalam "
                          f"{fmt_dur(args.job_timeout)} (status={timing.status if timing else 'hash hilang'})")
                    if timing and timing.status == "finished" and not timing.ended_at:
                        print("          status finished tapi ended_at kosong — job dieksekusi "
                              "worker versi RQ berbeda?")
                    continue

                spe = timing.service_seconds / n
                row = dict(size=n, rep=rep, weight=args.weight, payload_kb=round(len(body) / 1024, 1),
                           http_status=res.status_code, http_latency_s=round(res.latency_s, 3),
                           job_id=job_id, job_status=timing.status,
                           wait_s=round(timing.wait_seconds or 0, 2),
                           service_s=round(timing.service_seconds, 2),
                           total_s=round(timing.total_seconds or 0, 2),
                           s_per_encounter=round(spe, 3))
                log.write(**row)
                rows.append(row)

                flag = "  GAGAL" if timing.status == "failed" else ""
                print(f"{n:>4} {rep:>4} {len(body)/1024:>8.0f}K {res.latency_s:>6.1f}s "
                      f"{fmt_dur(timing.wait_seconds or 0):>8} {fmt_dur(timing.service_seconds):>9} "
                      f"{fmt_dur(timing.total_seconds or 0):>9} {spe:>8.2f}{flag}")

                # Batasi tunggu pengosongan: kalau ada job lain yang tertinggal
                # di antrean, jangan sampai menggantung sampai --job-timeout.
                if not probe.wait_until_drained(
                    timeout=args.drain_timeout, quiet_for=3,
                    on_tick=lambda el, s: _status(
                        n, rep, f"menunggu antrean kosong  antrean={s.depth} jalan={s.started}", el),
                ):
                    _clear()
                    print(f"          antrean belum kosong setelah {fmt_dur(args.drain_timeout)} — "
                          "ada pekerjaan lain di antrean ini, hasil berikutnya bisa tercampur.")
                _clear()

    log.close()
    check_isolation(probe, down_before)

    ok = [r for r in rows if r["job_status"] == "finished"]
    if len(ok) < 2:
        print("\nData terlalu sedikit untuk regresi.")
        return

    xs = [float(r["size"]) for r in ok]
    ys = [float(r["service_s"]) for r in ok]
    slope, intercept, r2 = linreg(xs, ys)

    per_enc = slope if slope == slope and slope > 0 else sum(ys) / sum(xs)
    per_replica_hour = 3600.0 / per_enc if per_enc > 0 else float("nan")

    print("\n" + "=" * 78)
    print("HASIL")
    print("=" * 78)
    print(f"  Model  : durasi_job = {intercept:.1f}s + {slope:.2f}s x jumlah_encounter   (R^2 = {r2:.3f})")
    print(f"  Biaya marjinal per encounter : {per_enc:.2f} detik")
    print(f"  Overhead tetap per job       : {intercept:.1f} detik")
    replicas = settings.worker_replicas
    fleet_hour = per_replica_hour * replicas
    fleet_epm = fleet_hour / 60.0

    print(f"\n  Kapasitas 1 replika          : {per_replica_hour:,.0f} encounter/jam "
          f"({per_replica_hour/60:,.1f}/menit)")
    print(f"  Kapasitas armada ({replicas} replika) : {fleet_hour:,.0f} encounter/jam "
          f"({fleet_epm:,.1f}/menit)   <- batas teoretis")
    print(f"  Pada utilisasi aman 70%      : {fleet_hour*0.7:,.0f} encounter/jam "
          f"({fleet_epm*0.7:,.1f}/menit)")
    print("\n  T1 mengirim satu request pada satu waktu, jadi angka per-replika di atas murni "
          f"\n  waktu layanan tanpa kontensi. Kapasitas armada adalah ekstrapolasi linear x{replicas} "
          "\n  — T3 yang membuktikan apakah penskalaannya benar-benar linear.")

    steps = [round(fleet_epm * f) for f in (0.4, 0.7, 1.0, 1.4)]
    print(f"\n  Langkah T3 yang disarankan   : --steps {','.join(str(s) for s in steps)}")
    print(f"  python tests/t3_saturation.py --steps {','.join(str(s) for s in steps)} "
          f"--encounters 10 --step-duration 600")

    if r2 == r2 and r2 < 0.9:
        print("\n  R^2 rendah — biaya tidak linear terhadap N. Kemungkinan ada efek batch "
              "(ingestor commit tiap 5 encounter) atau variasi isi payload. "
              "Perbanyak --repeats sebelum menyimpulkan.")
    if intercept > 0 and per_enc > 0 and intercept / per_enc > 5:
        print(f"\n  Overhead tetap setara ~{intercept/per_enc:.0f} encounter. Request kecil sangat "
              "tidak efisien — sarankan pengirim menggabungkan encounter per request.")

    save_summary(summary_path, {
        "test": "t1_service_time", "sizes": sizes, "repeats": args.repeats, "weight": args.weight,
        "samples": len(ok), "slope_s_per_encounter": slope, "intercept_s": intercept, "r2": r2,
        "capacity_encounters_per_hour_per_replica": per_replica_hour,
        "worker_replicas": replicas,
        "capacity_encounters_per_hour_fleet": fleet_hour,
        "suggested_t3_steps": steps,
        "template": str(settings.template_payload), "rows": rows,
    })
    print(f"Detail   : {csv_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="1,5,10,25,50", help="jumlah encounter per request, dipisah koma")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--weight", type=int, default=1,
                    help="pengali isi tiap data source; untuk uji sensitivitas ukuran payload")
    ap.add_argument("--no-force", action="store_true",
                    help="jangan set force_ingest_completed (encounter stale jadi billing-only)")
    ap.add_argument("--http-timeout", type=float, default=900)
    ap.add_argument("--enqueue-timeout", type=float, default=120)
    ap.add_argument("--job-timeout", type=float, default=3600)
    ap.add_argument("--drain-timeout", type=float, default=300,
                    help="batas tunggu antrean kosong antar-iterasi")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
