#!/usr/bin/env python3
"""T2 — Batas atas sisi API (ingress), tanpa melihat worker.

Tujuannya menjawab: apakah API pernah jadi bottleneck, atau selalu worker?

Ini bukan sekadar "berapa RPS bisa dijawab". Sebelum enqueue, endpoint
/encounters/update masih melakukan kerja sinkron yang berat di proses API:
menyimpan payload ke disk, validasi pydantic, dan
`parser.parse_update_encounters_request(api_model)`. Dengan uvicorn --workers 2,
payload besar bisa membuat request lain antre di API walaupun worker parsing
menganggur.

Idealnya dijalankan ke api-sandbox (IS_CODEX_API_V2_ADD_TO_DB=false) supaya
tidak menumpuk antrean, atau ke staging dengan worker dimatikan.

    python tests/t2_ingress.py --rates 6,12,24,48 --duration 120 --encounters 5
"""
import argparse
import asyncio
import json

from _common import CohortCursor, banner, load_context, run_paths, save_summary

from config import settings
from lib.csvlog import CsvLog
from lib.driver import (arrival_offsets, make_client, post_json, run_open_loop, summarize,
                        warn_if_client_bound)
from lib.payload import build_update_request

FIELDS = ("rate_rpm", "idx", "submit_ts", "latency_s", "status", "ok", "payload_kb", "schedule_lag_s")


async def run(args) -> None:
    template, cohort, _ = load_context(require_redis=False)
    cursor = CohortCursor(cohort)
    rates = [float(r) for r in args.rates.split(",") if r.strip()]

    banner("T2 — BATAS ATAS SISI API (INGRESS)", template, {
        "Laju": f"{rates} request/menit", "Durasi": f"{args.duration}s per langkah",
        "Encounter": f"{args.encounters} per request",
    })
    if settings.parsing_queue:
        print("Ingat: kalau target ini IS_CODEX_API_V2_ADD_TO_DB=true, tiap request tetap "
              "menumpuk job di antrean parsing.\n")

    csv_path, _, summary_path = run_paths("t2_ingress")
    log = CsvLog(csv_path, FIELDS)
    steps = []

    print(f"{'LAJU':>8} {'KIRIM':>6} {'OK':>5} {'GAGAL':>6} {'p50':>8} {'p95':>8} {'MAKS':>8} {'INFLIGHT':>9}")
    print("-" * 66)

    async with make_client(timeout_s=args.http_timeout) as client:
        for rate in rates:
            # Payload disiapkan lebih dulu supaya serialisasi tidak masuk jalur waktu kirim.
            offsets = arrival_offsets(rate, args.duration, poisson=args.poisson)
            bodies = []
            for _ in offsets:
                p = build_update_request(template, cursor.take(args.encounters), args.weight)
                bodies.append(json.dumps(p).encode())

            async def fire(i: int, target_ts: float, _bodies=bodies):
                return await post_json(client, settings.url_update, {}, settings.headers, settings.auth,
                                       idx=i, scheduled_ts=target_ts, encounters=args.encounters,
                                       body=_bodies[i])

            stats = await run_open_loop(offsets=offsets, fire=fire)
            s = summarize(stats, args.duration)

            for r in stats.results:
                log.write(rate_rpm=rate, idx=r.idx, submit_ts=round(r.submit_ts, 3),
                          latency_s=round(r.latency_s, 3), status=r.status_code, ok=int(r.ok),
                          payload_kb=round(r.request_bytes / 1024, 1),
                          schedule_lag_s=round(r.schedule_lag_s, 3))

            print(f"{rate:>8.0f} {s['requests']:>6} {s['ok']:>5} {s['failed']:>6} "
                  f"{s['http_p50_s']:>7.2f}s {s['http_p95_s']:>7.2f}s {s['http_max_s']:>7.2f}s "
                  f"{s['max_inflight']:>9}")

            warn = warn_if_client_bound(stats)
            if warn:
                print(f"  {warn}")

            steps.append({"rate_rpm": rate, **s, "client_bound": bool(warn)})

            if args.stop_on_error and s["failed"] > 0:
                print("\n  Berhenti: sudah muncul kegagalan.")
                break
            if args.cooldown:
                await asyncio.sleep(args.cooldown)

    log.close()

    clean = [s for s in steps if s["failed"] == 0 and not s["client_bound"]]
    best = max((s["rate_rpm"] for s in clean), default=None)

    print("\n" + "=" * 78)
    print("HASIL")
    print("=" * 78)
    if best is None:
        print("  Tidak ada langkah yang bersih. Semua laju sudah menghasilkan error, atau "
              "load generator-nya yang jadi bottleneck.")
    else:
        base = steps[0]["http_p95_s"]
        top = next(s for s in steps if s["rate_rpm"] == best)
        print(f"  Laju bersih tertinggi : {best:.0f} request/menit "
              f"({best * args.encounters:.0f} encounter/menit masuk)")
        print(f"  p95 di titik itu      : {top['http_p95_s']:.2f}s (dari {base:.2f}s di laju terendah)")
        if base > 0 and top["http_p95_s"] > base * 3:
            print("  p95 sudah naik >3x — API mulai antre di dirinya sendiri, bukan cuma enqueue.")
    print("\n  Bandingkan angka ini dengan kapasitas worker dari T1. Kalau ingress jauh lebih "
          "besar (biasanya begitu), abaikan sisi API dan fokus ke worker.")

    save_summary(summary_path, {"test": "t2_ingress", "encounters_per_request": args.encounters,
                                "duration_s": args.duration, "steps": steps})
    print(f"Detail   : {csv_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", default="6,12,24,48", help="request/menit per langkah, dipisah koma")
    ap.add_argument("--duration", type=float, default=120, help="detik per langkah")
    ap.add_argument("--encounters", type=int, default=5, help="encounter per request")
    ap.add_argument("--weight", type=int, default=1)
    ap.add_argument("--poisson", action="store_true", help="inter-arrival eksponensial, bukan jarak tetap")
    ap.add_argument("--cooldown", type=float, default=15, help="jeda antar langkah (detik)")
    ap.add_argument("--stop-on-error", action="store_true")
    ap.add_argument("--http-timeout", type=float, default=300)
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
