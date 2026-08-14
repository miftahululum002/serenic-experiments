"""Snapshot live semua queue RQ + worker: apakah server sudah mentok kapasitas?

Sekali jalan: dump kondisi seluruh queue, registry, dan worker saat ini.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone

from rq import Queue, Worker

from config import REDIS_HOST, REDIS_PORT, get_redis


def main():
    conn = get_redis()
    conn.ping()

    print(f"=== SNAPSHOT {datetime.now(timezone.utc).isoformat()} ===")
    print(f"redis {REDIS_HOST}:{REDIS_PORT}\n")

    info = conn.info()
    print("--- REDIS SERVER ---")
    for k in ("redis_version", "connected_clients", "used_memory_human",
              "maxmemory_human", "mem_fragmentation_ratio", "blocked_clients",
              "instantaneous_ops_per_sec", "rejected_connections",
              "evicted_keys", "keyspace_hits", "keyspace_misses", "uptime_in_days"):
        if k in info:
            print(f"  {k:32} = {info[k]}")
    print()

    # --- QUEUES ---
    # `rq:queues` adalah sumber resmi daftar queue — key list `rq:queue:<nama>`
    # dihapus RQ saat queue kosong, jadi scan key saja tidak lengkap.
    qnames = sorted(
        k.decode().split("rq:queue:", 1)[1] for k in conn.smembers("rq:queues")
    )
    print(f"--- QUEUES ({len(qnames)}) ---")
    hdr = f"{'queue':<45} {'pending':>8} {'started':>8} {'deferred':>9} {'failed':>8} {'finished':>9} {'sched':>7}"
    print(hdr)
    print("-" * len(hdr))
    totals = Counter()
    for qn in qnames:
        q = Queue(qn, connection=conn)
        row = {
            "pending": q.count,
            "started": q.started_job_registry.count,
            "deferred": q.deferred_job_registry.count,
            "failed": q.failed_job_registry.count,
            "finished": q.finished_job_registry.count,
            "sched": q.scheduled_job_registry.count,
        }
        for k, v in row.items():
            totals[k] += v
        print(f"{qn:<45} {row['pending']:>8} {row['started']:>8} "
              f"{row['deferred']:>9} {row['failed']:>8} {row['finished']:>9} "
              f"{row['sched']:>7}")
    print("-" * len(hdr))
    print(f"{'TOTAL':<45} {totals['pending']:>8} {totals['started']:>8} "
          f"{totals['deferred']:>9} {totals['failed']:>8} {totals['finished']:>9} "
          f"{totals['sched']:>7}")
    print()

    # --- WORKERS ---
    workers = Worker.all(connection=conn)
    print(f"--- WORKERS ({len(workers)}) ---")
    by_state = Counter()
    by_queue_state = defaultdict(Counter)
    hosts = Counter()
    for w in workers:
        st = w.get_state()
        by_state[st] += 1
        hosts[w.hostname.decode() if isinstance(w.hostname, bytes) else w.hostname] += 1
        for qn in w.queue_names():
            by_queue_state[qn][st] += 1
    print(f"  state: {dict(by_state)}")
    print(f"  hosts: {dict(hosts)}")
    print()
    print(f"  {'queue yang dilayani':<45} {'busy':>6} {'idle':>6} {'total':>6}")
    for qn in sorted(by_queue_state):
        c = by_queue_state[qn]
        print(f"  {qn:<45} {c['busy']:>6} {c['idle']:>6} {sum(c.values()):>6}")
    print()

    # --- BUSY WORKER DETAIL: sudah berapa lama job berjalan? ---
    # Baca hash job mentah: payload di-pickle dengan modul aplikasi yang tidak
    # tersedia di sini, jadi job.func_name akan melempar DeserializationError.
    print("--- JOB SEDANG BERJALAN (busy workers) ---")
    now = datetime.now(timezone.utc)
    running = []
    for w in workers:
        if w.get_state() != "busy":
            continue
        jid = conn.hget(w.key, b"current_job")
        if jid is None:
            running.append((None, "?", "job id tidak terbaca"))
            continue
        jid = jid.decode()
        h = conn.hgetall(f"rq:job:{jid}")
        g = lambda f: (h.get(f.encode()) or b"").decode()  # noqa: E731
        started = g("started_at")
        age = None
        if started:
            age = (now - datetime.fromisoformat(started).replace(
                tzinfo=timezone.utc)).total_seconds()
        running.append((age, g("origin"), g("description")[:90] or jid))
    running.sort(key=lambda r: (r[0] is None, -(r[0] or 0)))
    if not running:
        print("  (tidak ada worker busy)")
    for age, origin, desc in running[:40]:
        age_s = f"{age:9.1f}s" if age is not None else "        ?"
        print(f"  {age_s}  {origin:<40} {desc}")
    print()


if __name__ == "__main__":
    main()
