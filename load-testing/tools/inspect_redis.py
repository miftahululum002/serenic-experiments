#!/usr/bin/env python3
"""Verifikasi koneksi Redis, temukan nama antrean RQ, dan deteksi Redis berbagi.

Jalankan ini PERTAMA KALI sebelum test apa pun, untuk tiap profil. Nama antrean
di server berasal dari env var sehingga tidak bisa ditebak dari kode.

    PROFILE=dev  python tools/inspect_redis.py
    PROFILE=prod python tools/inspect_redis.py

Bandingkan keduanya: kalau daftar antrean dan isinya sama persis, kedua
lingkungan berbagi Redis dan "test di staging" sebenarnya menyentuh produksi.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis  # noqa: E402

import config  # noqa: E402
from config import settings  # noqa: E402
from lib.rq_probe import parse_ts  # noqa: E402


def discover_queues(r: redis.Redis) -> tuple[set[str], set[str]]:
    """Kembalikan (antrean terdaftar, antrean yang punya worker).

    Sumber utamanya set `rq:queues`, bukan pemindaian `rq:queue:*`. Sebabnya
    `rq:queue:<name>` adalah LIST — kalau antreannya kosong, key-nya tidak ada
    di Redis sama sekali. Antrean idle karena itu tidak terlihat oleh scan,
    dan itu keadaan NORMAL, bukan salah konfigurasi.
    """
    registered = {
        (k.decode() if isinstance(k, bytes) else k).removeprefix("rq:queue:")
        for k in (r.smembers("rq:queues") or set())
    }
    registered |= {k.decode().removeprefix("rq:queue:") for k in r.scan_iter("rq:queue:*", count=1000)}

    with_workers: set[str] = set()
    for w in r.smembers("rq:workers") or set():
        key = w.decode() if isinstance(w, bytes) else w
        if not key.startswith("rq:worker:"):
            key = f"rq:worker:{key}"
        qn = r.hget(key, "queues")
        if qn:
            with_workers |= {q.strip() for q in qn.decode().split(",") if q.strip()}
    return registered, with_workers


def worker_counts(r: redis.Redis) -> Counter:
    counts: Counter = Counter()
    for w in r.smembers("rq:workers") or set():
        key = w.decode() if isinstance(w, bytes) else w
        if not key.startswith("rq:worker:"):
            key = f"rq:worker:{key}"
        qn = r.hget(key, "queues")
        if qn:
            for q in qn.decode().split(","):
                if q.strip():
                    counts[q.strip()] += 1
    return counts


def origin_breakdown(r: redis.Redis, sample: int) -> tuple[Counter, int]:
    """Tally field `origin` dari job hash — bukti paling langsung soal siapa saja
    yang memakai Redis ini."""
    keys = []
    for k in r.scan_iter("rq:job:*", count=1000):
        keys.append(k)
        if len(keys) >= sample:
            break
    counts: Counter = Counter()
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        pipe = r.pipeline()
        for k in chunk:
            pipe.hget(k, "origin")
        for v in pipe.execute():
            counts[v.decode() if v else "(tanpa origin)"] += 1
    return counts, len(keys)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=3000, help="jumlah job hash yang disampel")
    args = ap.parse_args()

    if not settings.redis_url:
        sys.exit("REDIS_URL belum di-set (profil aktif: "
                 f"{config.ACTIVE_PROFILE or 'hanya .env'})")

    r = redis.from_url(settings.redis_url)
    try:
        r.ping()
    except Exception as e:
        sys.exit(f"Tidak bisa connect ke Redis: {e}\nKalau Redis tidak publik, buat SSH tunnel dulu.")

    asal = f" (dari {config.PROFILE_SOURCE})" if config.PROFILE_SOURCE else ""
    print(f"Profil    : {config.ACTIVE_PROFILE or '(hanya .env)'}{asal}  "
          f"{settings.credential_fingerprint}")
    print(f"Redis     : {settings.redis_url}\n")

    registered, with_workers = discover_queues(r)
    counts = worker_counts(r)
    all_queues = sorted(registered | with_workers)

    if not all_queues:
        print("Tidak ada antrean RQ sama sekali di Redis ini — cek nomor DB di akhir URL.")
        return

    print(f"{'ANTREAN':<52} {'WORKER':>6} {'NUNGGU':>7} {'JALAN':>6} {'SELESAI':>8} {'GAGAL':>6}")
    print("-" * 90)
    for q in all_queues:
        mark = "" if q in with_workers else "  (tanpa worker)"
        print(f"{q:<52} {counts.get(q, 0):>6} {r.llen(f'rq:queue:{q}'):>7} "
              f"{r.zcard(f'rq:wip:{q}'):>6} {r.zcard(f'rq:finished:{q}'):>8} "
              f"{r.zcard(f'rq:failed:{q}'):>6}{mark}")
    print("\nAntrean kosong tidak punya key di Redis — itu normal untuk sistem yang idle.")

    # --- konfigurasi yang dipakai harness ---
    print("\nKONFIGURASI PROFIL INI")
    print("-" * 90)
    for label, name in (("PARSING_QUEUE", settings.parsing_queue),
                        ("ANALYSIS_COORDINATOR_QUEUE", settings.analysis_coordinator_queue),
                        ("ANALYSIS_QUEUE", settings.analysis_queue)):
        if not name:
            print(f"  {label:<28} (belum di-set)")
        elif name in all_queues:
            print(f"  {label:<28} {name}  OK")
        else:
            print(f"  {label:<28} {name}  <-- TIDAK ADA di Redis ini, cek ejaannya")

    if settings.parsing_queue:
        actual = counts.get(settings.parsing_queue, 0)
        status = "OK" if actual == settings.worker_replicas else "<-- TIDAK COCOK"
        print(f"  {'WORKER_REPLICAS':<28} {settings.worker_replicas}, terdaftar {actual}  {status}")
        if actual != settings.worker_replicas:
            print(f"      Perbarui WORKER_REPLICAS={actual} di .env.{config.ACTIVE_PROFILE},")
            print("      TAPI pastikan dulu tidak ada registrasi worker basi:")
            print("        docker compose -f <compose> ps | grep parsing")

    # --- deteksi Redis berbagi ---
    print(f"\nASAL JOB (sampel {args.sample} job hash)")
    print("-" * 90)
    origins, scanned = origin_breakdown(r, args.sample)
    if not scanned:
        print("  (tidak ada job hash tersimpan)")
    else:
        for origin, n in origins.most_common(15):
            print(f"  {n:>7}  {origin}")

        known = set(all_queues)
        asing = {o: n for o, n in origins.items() if o not in known and o != "(tanpa origin)"}
        if asing:
            print("\n  PERHATIAN: ada job dari antrean yang TIDAK terdaftar di Redis ini:")
            for o, n in sorted(asing.items(), key=lambda x: -x[1])[:10]:
                print(f"    {n:>7}  {o}")
            print("\n  Dua kemungkinan, dan bedanya penting:")
            print("    1. Sisa lama — job dari sebelum nama antrean diberi sufiks lingkungan.")
            print("       Cek tanggal created_at-nya; kalau semua lama, ini tidak berbahaya.")
            print("    2. Redis dipakai bersama lingkungan lain. Kalau ini benar, 'test di")
            print("       staging' akan menyentuh data lingkungan itu juga.")
            print("  Bandingkan keluaran profil dev dan prod untuk memastikan.")

    # --- job terakhir di antrean parsing ---
    if settings.parsing_queue and settings.parsing_queue in all_queues:
        from lib.rq_probe import RQProbe

        probe = RQProbe(settings.redis_url, settings.parsing_queue)
        print(f"\nJOB TERAKHIR DI {settings.parsing_queue}")
        print("-" * 90)
        rows = []
        for reg in ("finished", "failed"):
            for jid in probe.registry_ids(reg, limit=200)[-10:]:
                j = probe.job(jid)
                if j:
                    rows.append(j)
        rows.sort(key=lambda j: j.ended_at or j.created_at or parse_ts("1970-01-01T00:00:00Z"))
        if not rows:
            print("  (registry finished/failed kosong — job sukses dihapus setelah result_ttl "
                  "habis, default 500 detik)")
        else:
            print(f"  {'JOB':<14} {'STATUS':<10} {'SELESAI PADA':<22} {'ANTRE':>8} {'PROSES':>9}")
            for j in rows[-10:]:
                print(f"  {j.job_id[:12]:<14} {j.status:<10} "
                      f"{(j.ended_at.strftime('%Y-%m-%d %H:%M:%S') if j.ended_at else '-'):<22} "
                      f"{(f'{j.wait_seconds:.1f}s' if j.wait_seconds is not None else '-'):>8} "
                      f"{(f'{j.service_seconds:.1f}s' if j.service_seconds is not None else '-'):>9}")
            print("\n  Kalau job test Anda ADA di sini tapi t1 melaporkan tidak selesai,")
            print("  kirimkan keluaran t1-nya — berarti masalahnya di pencocokan job, bukan di worker.")

    # --- contoh job, untuk memverifikasi format timestamp ---
    sample_key = next(r.scan_iter("rq:job:*", count=50), None)
    if sample_key:
        print(f"\nCONTOH JOB ({sample_key.decode()})")
        print("-" * 90)
        fields = ("status", "origin", "created_at", "enqueued_at", "started_at", "ended_at")
        for f, v in zip(fields, r.hmget(sample_key, fields)):
            val = v.decode() if v else "-"
            extra = ""
            if f.endswith("_at") and v:
                extra = "" if parse_ts(v) else "  <-- GAGAL DIPARSE, cek lib/rq_probe._TS_FORMATS"
            print(f"  {f:<12} = {val}{extra}")


if __name__ == "__main__":
    main()
