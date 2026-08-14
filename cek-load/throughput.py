"""Ukur throughput & durasi job data parsing — akurat, tanpa akses ke server.

Metode:
1. THROUGHPUT: pantau job ID di `started_job_registry`. Setiap job ID baru yang
   muncul = satu slot worker yang baru saja bebas dan menarik job berikutnya.
   Menghitung ID unik baru per detik jauh lebih akurat daripada delta registry
   `finished` (kena TTL) atau delta `pending` (terkontaminasi job yang masuk).
2. DURASI: baca `started_at`/`ended_at` dari hash job yang sudah selesai maupun
   yang sedang berjalan.
3. ARRIVAL: laju job masuk = throughput + pertumbuhan backlog.

Contoh:
    python throughput.py --minutes 6 --interval 3
"""

import argparse
import statistics
import time
from datetime import datetime, timezone

from rq import Queue

from config import DATA_PARSING_AGENT, get_redis


def parse_ts(raw):
    if not raw:
        return None
    return datetime.fromisoformat(raw.decode()).replace(tzinfo=timezone.utc)


def job_times(conn, jid):
    h = conn.hgetall(f"rq:job:{jid}")
    if not h:
        return None
    return {
        "enqueued_at": parse_ts(h.get(b"enqueued_at")),
        "started_at": parse_ts(h.get(b"started_at")),
        "ended_at": parse_ts(h.get(b"ended_at")),
        "status": (h.get(b"status") or b"").decode(),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--queue", default=DATA_PARSING_AGENT)
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--minutes", type=float, default=6.0)
    args = p.parse_args()

    conn = get_redis()
    conn.ping()
    q = Queue(args.queue, connection=conn)

    print(f"Mengukur {args.queue} selama {args.minutes} menit "
          f"(sampling {args.interval}s)\n", flush=True)

    seen_started = set(q.started_job_registry.get_job_ids())
    concurrency_obs = []
    completed = []          # (jid, durasi detik)
    t0 = time.time()
    p0 = q.count
    deadline = t0 + args.minutes * 60
    inflight = {jid: time.time() for jid in seen_started}

    while time.time() < deadline:
        time.sleep(args.interval)
        ids = q.started_job_registry.get_job_ids()
        concurrency_obs.append(len(ids))
        cur = set(ids)

        for jid in cur - seen_started:          # job baru mulai
            seen_started.add(jid)
            inflight[jid] = time.time()

        for jid in list(inflight):              # job yang sudah keluar = selesai
            if jid in cur:
                continue
            t = job_times(conn, jid)
            if t and t["started_at"] and t["ended_at"]:
                completed.append((t["started_at"], (t["ended_at"] - t["started_at"]).total_seconds()))
            else:
                completed.append((None, time.time() - inflight[jid]))
            del inflight[jid]

        el = time.time() - t0
        rate = len(completed) / el if el else 0
        print(f"  [{el:6.0f}s] slot_aktif={len(cur)} selesai={len(completed):>4} "
              f"laju={rate:6.3f} job/s  pending={q.count}", flush=True)

    span = time.time() - t0
    p1 = q.count
    n = len(completed)
    thr = n / span
    growth = (p1 - p0) / span
    durs = sorted(d for _, d in completed if d)

    print(f"\n=== HASIL ({span:.0f} detik) ===")
    print(f"Job selesai        : {n}")
    print(f"THROUGHPUT         : {thr:.4f} job/s = {thr*60:.2f} job/menit "
          f"= {thr*3600:.0f} job/jam")
    print(f"Backlog            : {p0} -> {p1} ({p1-p0:+d}, {growth:+.4f} job/s)")
    print(f"LAJU MASUK (est.)  : {thr + growth:.4f} job/s = "
          f"{(thr+growth)*3600:.0f} job/jam")
    if concurrency_obs:
        print(f"Slot paralel aktif : min={min(concurrency_obs)} "
              f"max={max(concurrency_obs)} avg={statistics.fmean(concurrency_obs):.2f}")
    if durs:
        print(f"\nDurasi per job (n={len(durs)}):")
        print(f"  min={durs[0]:.1f}s  median={statistics.median(durs):.1f}s  "
              f"avg={statistics.fmean(durs):.1f}s  max={durs[-1]:.1f}s")
        if len(durs) >= 5:
            print(f"  p90={durs[int(len(durs)*0.9)]:.1f}s")
        c = statistics.fmean(concurrency_obs) if concurrency_obs else 1
        print(f"  kapasitas teoritis = {c:.1f} slot / {statistics.fmean(durs):.1f}s "
              f"= {c/statistics.fmean(durs)*3600:.0f} job/jam")

    print("\n--- UMUR JOB YANG SEDANG BERJALAN ---")
    now = datetime.now(timezone.utc)
    for jid in q.started_job_registry.get_job_ids():
        t = job_times(conn, jid)
        if t and t["started_at"]:
            print(f"  {(now - t['started_at']).total_seconds():8.1f}s  {jid}")

    print("\n--- WAKTU TUNGGU DI ANTREAN (job terdepan) ---")
    head = q.get_job_ids(0, 4)
    tail = q.get_job_ids(-5, -1)
    for label, ids in (("terdepan", head), ("terbelakang", tail)):
        for jid in ids:
            t = job_times(conn, jid)
            if t and t["enqueued_at"]:
                wait = (now - t["enqueued_at"]).total_seconds()
                print(f"  {label:<12} menunggu {wait/60:8.1f} menit  ({jid})")

    if thr > 0:
        print(f"\nETA menghabiskan {p1} backlog (tanpa job masuk lagi): "
              f"{p1/thr/3600:.1f} jam")
    if growth > 0:
        print("STATUS: backlog TUMBUH -> laju masuk melebihi kapasitas proses "
              "(server sudah di batas).")


if __name__ == "__main__":
    main()
