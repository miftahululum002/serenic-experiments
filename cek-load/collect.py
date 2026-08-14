"""Pengumpul data mentah — satu sumber kebenaran untuk semua script di sini.

Modul ini hanya MEMBACA Redis dan mengembalikan dict/list; tidak mencetak
apa pun dan tidak mengambil kesimpulan. Penyajian ditangani CLI masing-masing
(`snapshot.py`, `monitor.py`, ...), penarikan kesimpulan oleh `report.py`.
"""

import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from rq import Queue, Worker

REGISTRIES = ("pending", "started", "deferred", "failed", "finished", "scheduled")


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(raw):
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Kondisi sesaat
# --------------------------------------------------------------------------

def redis_health(conn) -> dict:
    info = conn.info()
    keys = ("redis_version", "connected_clients", "used_memory_human",
            "maxmemory_human", "mem_fragmentation_ratio", "blocked_clients",
            "instantaneous_ops_per_sec", "rejected_connections", "evicted_keys",
            "keyspace_hits", "keyspace_misses", "uptime_in_days")
    return {k: info[k] for k in keys if k in info}


def queue_names(conn) -> list:
    """Daftar queue resmi dari set `rq:queues`.

    Key list `rq:queue:<nama>` dihapus RQ saat queue kosong, jadi scan key saja
    akan melewatkan queue yang sedang kosong.
    """
    return sorted(
        k.decode().split("rq:queue:", 1)[1] for k in conn.smembers("rq:queues")
    )


def queue_stats(conn, qnames=None) -> list:
    out = []
    for qn in qnames or queue_names(conn):
        q = Queue(qn, connection=conn)
        out.append({
            "queue": qn,
            "pending": q.count,
            "started": q.started_job_registry.count,
            "deferred": q.deferred_job_registry.count,
            "failed": q.failed_job_registry.count,
            "finished": q.finished_job_registry.count,
            "scheduled": q.scheduled_job_registry.count,
        })
    return out


def worker_pools(conn) -> dict:
    """Peta queue -> {busy, idle, total}. Tiap pool worker terikat queue-nya."""
    pools = defaultdict(Counter)
    total = Counter()
    for w in Worker.all(connection=conn):
        st = w.get_state()
        total[st] += 1
        for qn in w.queue_names():
            pools[qn][st] += 1
            pools[qn]["total"] += 1
    return {
        "per_queue": {qn: dict(c) for qn, c in pools.items()},
        "fleet": dict(total),
        "fleet_total": sum(total.values()),
    }


def job_fields(conn, jid) -> dict:
    """Baca hash job mentah.

    Sengaja tidak lewat Job.fetch(): payload di-pickle dengan modul backend
    yang tidak tersedia di sini, sehingga deserialisasi otomatis akan gagal.
    """
    h = conn.hgetall(f"rq:job:{jid}")
    if not h:
        return {}
    g = lambda f: (h.get(f.encode()) or b"").decode()  # noqa: E731
    return {
        "id": jid,
        "status": g("status"),
        "origin": g("origin"),
        "description": g("description"),
        "enqueued_at": parse_ts(g("enqueued_at")),
        "started_at": parse_ts(g("started_at")),
        "ended_at": parse_ts(g("ended_at")),
        "timeout": float(g("timeout") or 0) or None,
    }


def running_jobs(conn, qname=None) -> list:
    """Job yang sedang dikerjakan worker, beserta umurnya."""
    now = now_utc()
    out = []
    for w in Worker.all(connection=conn):
        if w.get_state() != "busy":
            continue
        if qname and qname not in w.queue_names():
            continue
        jid = conn.hget(w.key, b"current_job")
        if not jid:
            continue
        f = job_fields(conn, jid.decode())
        if not f:
            continue
        f["age"] = (now - f["started_at"]).total_seconds() if f["started_at"] else None
        out.append(f)
    out.sort(key=lambda r: (r["age"] is None, -(r["age"] or 0)))
    return out


def queue_waits(conn, qname, n=5) -> dict:
    """Berapa lama job terdepan & terbelakang sudah mengantre (detik)."""
    q = Queue(qname, connection=conn)
    now = now_utc()

    def waits(ids):
        out = []
        for jid in ids:
            f = job_fields(conn, jid)
            if f.get("enqueued_at"):
                out.append((now - f["enqueued_at"]).total_seconds())
        return out

    return {"head": waits(q.get_job_ids(0, n - 1)),
            "tail": waits(q.get_job_ids(-n, -1))}


# --------------------------------------------------------------------------
# Pengukuran berjalan
# --------------------------------------------------------------------------

def measure_live(conn, qname, minutes=6.0, interval=3.0, on_sample=None) -> dict:
    """Sampling berkala: tren backlog + throughput + durasi job sekaligus.

    Throughput dihitung dari pergantian job ID di `started_job_registry`: setiap
    ID yang hilang dari registry = satu job selesai. Ini lebih akurat daripada
    delta registry `finished` (kena TTL) maupun delta `pending` (terkontaminasi
    job yang baru masuk).
    """
    q = Queue(qname, connection=conn)
    t0 = time.time()
    deadline = t0 + minutes * 60

    inflight = {jid: t0 for jid in q.started_job_registry.get_job_ids()}
    seen = set(inflight)
    completed = []      # durasi detik
    samples = []        # tren backlog
    concurrency = []

    p_first = q.count
    prev_pending = p_first
    prev_t = t0

    while True:
        ids = set(q.started_job_registry.get_job_ids())
        concurrency.append(len(ids))
        tnow = time.time()

        for jid in ids - seen:
            seen.add(jid)
            inflight[jid] = tnow
        for jid in list(inflight):
            if jid in ids:
                continue
            f = job_fields(conn, jid)
            if f.get("started_at") and f.get("ended_at"):
                completed.append((f["ended_at"] - f["started_at"]).total_seconds())
            else:
                completed.append(tnow - inflight[jid])
            del inflight[jid]

        pending = q.count
        pools = worker_pools(conn)["per_queue"].get(qname, {})
        dt = tnow - prev_t
        sample = {
            "t": tnow - t0,
            "ts": now_utc(),
            "pending": pending,
            "d_pending": pending - prev_pending,
            "drain_per_s": -(pending - prev_pending) / dt if dt > 0 else 0.0,
            "busy": pools.get("busy", 0),
            "idle": pools.get("idle", 0),
            "active_slots": len(ids),
            "completed_total": len(completed),
            "failed": q.failed_job_registry.count,
        }
        samples.append(sample)
        if on_sample:
            on_sample(sample)
        prev_pending, prev_t = pending, tnow

        if time.time() >= deadline:
            break
        time.sleep(interval)

    span = time.time() - t0
    p_last = q.count
    n = len(completed)
    throughput = n / span if span else 0.0
    growth = (p_last - p_first) / span if span else 0.0
    durs = sorted(d for d in completed if d)
    avg_slots = statistics.fmean(concurrency) if concurrency else 0

    res = {
        "queue": qname,
        "span_s": span,
        "samples": samples,
        "completed": n,
        "throughput_per_s": throughput,
        "throughput_per_h": throughput * 3600,
        "pending_first": p_first,
        "pending_last": p_last,
        "backlog_growth_per_s": growth,
        "arrival_per_s": throughput + growth,
        "slots_min": min(concurrency) if concurrency else 0,
        "slots_max": max(concurrency) if concurrency else 0,
        "slots_avg": avg_slots,
        "durations": durs,
        "eta_drain_s": p_last / throughput if throughput > 0 else None,
    }
    if durs:
        res.update({
            "dur_min": durs[0],
            "dur_median": statistics.median(durs),
            "dur_avg": statistics.fmean(durs),
            "dur_p90": durs[int(len(durs) * 0.9)] if len(durs) >= 5 else durs[-1],
            "dur_max": durs[-1],
            "theoretical_per_h": avg_slots / statistics.fmean(durs) * 3600,
        })
    return res


def dequeue_fate(conn, qname, wait=120.0) -> dict:
    """Cek job yang keluar antrean: benar diproses, atau dibuang tanpa proses?"""
    q = Queue(qname, connection=conn)
    before = q.get_job_ids()
    time.sleep(wait)
    after = set(q.get_job_ids())

    fates = Counter()
    for jid in (j for j in before if j not in after):
        f = job_fields(conn, jid)
        if not f:
            fates["hash hilang (dibuang, tidak diproses)"] += 1
        else:
            fates[f"status={f['status']}"] += 1

    before_set = set(before)
    return {
        "queue": qname,
        "wait_s": wait,
        "left": sum(fates.values()),
        "entered": len([j for j in after if j not in before_set]),
        "fates": dict(fates),
        "len_before": len(before),
        "len_after": len(after),
        "processed": sum(v for k, v in fates.items() if k.startswith("status=")),
    }


FAST_FAIL_S = 1.0   # gagal secepat ini = ditolak validasi, bukan kehabisan sumber daya


def failure_summary(conn, qnames=None, recent_h=1.0) -> list:
    """Ringkasan job gagal per queue: jumlah, umur, durasi, dan sebab kegagalan.

    Kegagalan "baru" dipisah menjadi fast-fail (ditolak validasi dalam hitungan
    milidetik) dan timeout (job kehabisan waktu). Hanya yang kedua yang menjadi
    indikasi server kewalahan — tanpa pemisahan ini, satu error validasi bisa
    salah dibaca sebagai server tumbang.
    """
    now = now_utc()
    out = []
    for qn in qnames or queue_names(conn):
        q = Queue(qn, connection=conn)
        ids = q.failed_job_registry.get_job_ids()
        if not ids:
            continue
        ages, durs, timeouts = [], [], set()
        hit_timeout = recent = recent_fast = recent_timeout = 0
        for jid in ids:
            f = job_fields(conn, jid)
            if not f:
                continue
            end = f["ended_at"] or f["enqueued_at"]
            age_h = (now - end).total_seconds() / 3600 if end else None
            if age_h is not None:
                ages.append(age_h)

            d = None
            if f["started_at"] and f["ended_at"]:
                d = (f["ended_at"] - f["started_at"]).total_seconds()
                durs.append(d)
                if f["timeout"]:
                    timeouts.add(f["timeout"])
                    if d >= f["timeout"] * 0.95:
                        hit_timeout += 1

            if age_h is not None and age_h < recent_h:
                recent += 1
                if d is not None and f["timeout"] and d >= f["timeout"] * 0.95:
                    recent_timeout += 1
                elif d is not None and d < FAST_FAIL_S:
                    recent_fast += 1
        out.append({
            "queue": qn,
            "count": len(ids),
            "newest_h": min(ages) if ages else None,
            "oldest_h": max(ages) if ages else None,
            "dur_median": statistics.median(durs) if durs else None,
            "dur_max": max(durs) if durs else None,
            "timeout_settings": sorted(timeouts),
            "hit_timeout": hit_timeout,
            "recent": recent,
            "recent_fast_fail": recent_fast,
            "recent_timeout": recent_timeout,
        })
    return out
