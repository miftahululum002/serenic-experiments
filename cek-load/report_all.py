"""Kondisi server menyeluruh: SEMUA queue RQ sekaligus, bukan satu queue.

`report.py` menjawab "apakah pool queue X sudah mentok". Script ini menjawab
"bagaimana kondisi server secara keseluruhan": seluruh queue yang terdaftar,
seluruh armada worker, CPU/RAM mesin, dan Redis — semuanya diukur pada jendela
waktu yang sama sehingga angka antar queue bisa dibandingkan langsung.

Contoh:
    python report_all.py                       # 5 menit, semua queue
    python report_all.py --minutes 10 --interval 5
    python report_all.py --all                 # ikut tampilkan queue yang benar-benar diam
    python report_all.py --ssh vm-worker       # CPU/RAM dibaca dari VM lain

Dijalankan langsung di VM worker, CPU/RAM ikut terukur otomatis; dari laptop
pakai `--ssh <target>` (atau `HOST_SSH` di .env).

Script ini murni MEMBACA Redis: pembacaan registry dilakukan lewat `zrange`
mentah, bukan `get_job_ids()` bawaan RQ yang diam-diam menjalankan cleanup
(memindahkan job kedaluwarsa ke failed registry).
"""

import argparse
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime

from rq import Queue, Worker
from rq.registry import StartedJobRegistry

import collect
import host
from collect import parse_ts
from config import HOST_SSH, HOST_SSH_CMD, REDIS_HOST, REDIS_PORT, get_redis
from report import WIB, dur, num, render_host, table, tanggal

# Ambang detak dibedakan busy vs idle: worker sibuk berdetak tiap
# ~job_monitoring_interval (30 detik), sedangkan worker idle menggantung di BLPOP
# selama worker_ttl (420 detik) dan baru berdetak setelahnya — memakai satu ambang
# untuk keduanya akan menuduh worker idle yang sebenarnya sehat.
STALE_BUSY_S = 120
STALE_IDLE_S = 540
STUCK_RATIO = 0.90          # umur job ≥ sekian × timeout = nyaris/sudah kehabisan waktu
LONG_RUN_S = 900            # job berjalan lebih lama dari ini layak dilihat manusia
QUEUE_TICK_SHOW = 6         # queue teraktif yang namanya dicetak tiap tick

# RQ 2.x menyimpan entri started registry sebagai "job_id:execution_id".
_PARSE_JID = getattr(StartedJobRegistry, "parse_job_id", None)


def _jid(entry) -> str:
    s = entry.decode() if isinstance(entry, bytes) else entry
    return _PARSE_JID(s) if _PARSE_JID else s


def _txt(v) -> str:
    return v.decode() if isinstance(v, bytes) else (v or "")


# --------------------------------------------------------------------------
# Pembacaan Redis (dibatch — satu round-trip per tick, bukan per queue)
# --------------------------------------------------------------------------

def queue_handles(conn, qnames) -> list:
    """Kunci Redis tiap queue, diambil sekali di awal lewat objek RQ.

    Nama kunci registry berbeda antar versi RQ (`rq:wip:` vs `rq:started:`),
    jadi kuncinya ditanyakan ke objeknya, tidak ditulis manual.
    """
    out = []
    for qn in qnames:
        q = Queue(qn, connection=conn)
        out.append({
            "queue": qn,
            "list": q.key,
            "wip": q.started_job_registry.key,
            "deferred": q.deferred_job_registry.key,
            "failed": q.failed_job_registry.key,
            "finished": q.finished_job_registry.key,
            "scheduled": q.scheduled_job_registry.key,
        })
    return out


def scan_queues(conn, handles) -> dict:
    """Isi seluruh queue dalam satu pipeline — hemat dan konsisten waktunya."""
    pipe = conn.pipeline(transaction=False)
    for h in handles:
        pipe.llen(h["list"])
        pipe.zrange(h["wip"], 0, -1)
        pipe.zcard(h["deferred"])
        pipe.zcard(h["failed"])
        pipe.zcard(h["finished"])
        pipe.zcard(h["scheduled"])
    res = pipe.execute()

    out = {}
    for i, h in enumerate(handles):
        pending, wip, deferred, failed, finished, scheduled = res[i * 6:(i + 1) * 6]
        out[h["queue"]] = {
            "pending": pending,
            "started_ids": [_jid(x) for x in wip],
            "started": len(wip),
            "deferred": deferred,
            "failed": failed,
            "finished": finished,
            "scheduled": scheduled,
        }
    return out


def fleet(conn) -> dict:
    """Seluruh worker beserta status, queue yang dilayani, dan detaknya.

    Dibaca dari hash mentah (bukan `Worker.all()`) supaya cukup dua round-trip
    berapa pun jumlah workernya, dan supaya worker yang datanya sudah hilang
    tetap kelihatan sebagai entri zombi.
    """
    now = collect.now_utc()
    keys = conn.smembers(Worker.redis_workers_keys)
    pipe = conn.pipeline(transaction=False)
    for k in keys:
        pipe.hgetall(k)
    rows = pipe.execute()

    workers, zombies = [], []
    for k, h in zip(keys, rows):
        name = _txt(k).split("rq:worker:", 1)[-1]
        if not h:
            # Terdaftar di `rq:workers` tapi hash-nya sudah kedaluwarsa: worker
            # mati tanpa sempat membersihkan diri (kill -9 / OOM / VM restart).
            zombies.append(name)
            continue
        g = lambda f: _txt(h.get(f.encode()))  # noqa: E731
        hb = parse_ts(g("last_heartbeat"))
        birth = parse_ts(g("birth"))
        workers.append({
            "name": name,
            "state": g("state") or "?",
            "queues": [q for q in g("queues").split(",") if q],
            "current_job": g("current_job") or None,
            "hostname": g("hostname") or "?",
            "pid": g("pid"),
            "heartbeat_age_s": (now - hb).total_seconds() if hb else None,
            "uptime_s": (now - birth).total_seconds() if birth else None,
            "successful": int(g("successful_job_count") or 0),
            "failed": int(g("failed_job_count") or 0),
            "working_time_s": float(g("total_working_time") or 0),
            "current_job_s": float(g("current_job_working_time") or 0),
        })

    pools = defaultdict(Counter)
    states = Counter()
    for w in workers:
        states[w["state"]] += 1
        for qn in w["queues"]:
            pools[qn][w["state"]] += 1
            pools[qn]["total"] += 1
    stale = [w for w in workers
             if w["heartbeat_age_s"] is not None
             and w["heartbeat_age_s"] > (STALE_BUSY_S if w["state"] == "busy"
                                         else STALE_IDLE_S)]
    return {
        "workers": workers,
        "per_queue": {qn: dict(c) for qn, c in pools.items()},
        "states": dict(states),
        "total": len(workers),
        "zombies": zombies,
        "stale": stale,
    }


def job_times(conn, jids) -> dict:
    """started_at/ended_at banyak job sekaligus (satu round-trip)."""
    jids = list(jids)
    if not jids:
        return {}
    pipe = conn.pipeline(transaction=False)
    for j in jids:
        pipe.hmget(f"rq:job:{j}", "started_at", "ended_at")
    return {j: (parse_ts(s), parse_ts(e))
            for j, (s, e) in zip(jids, pipe.execute())}


def running_jobs(conn, fl) -> list:
    """Job yang sedang dikerjakan tiap worker busy, beserta umur & timeout-nya."""
    now = collect.now_utc()
    busy = [w for w in fl["workers"] if w["current_job"]]
    if not busy:
        return []
    pipe = conn.pipeline(transaction=False)
    for w in busy:
        pipe.hmget(f"rq:job:{w['current_job']}", "origin", "description",
                   "started_at", "timeout", "status")
    out = []
    for w, row in zip(busy, pipe.execute()):
        origin, desc, started, timeout, status = (_txt(x) for x in row)
        st = parse_ts(started)
        age = (now - st).total_seconds() if st else w["current_job_s"] or None
        tmo = float(timeout) if timeout else None
        out.append({
            "queue": origin or (w["queues"][0] if w["queues"] else "?"),
            "job": w["current_job"],
            "description": desc,
            "status": status,
            "age_s": age,
            "timeout_s": tmo,
            "worker": w["name"],
            "hostname": w["hostname"],
            "over_timeout": bool(tmo and age and age >= tmo * STUCK_RATIO),
        })
    out.sort(key=lambda r: (r["age_s"] is None, -(r["age_s"] or 0)))
    return out


# --------------------------------------------------------------------------
# Pengukuran berjalan — semua queue serentak
# --------------------------------------------------------------------------

def measure_all(conn, handles, minutes=5.0, interval=5.0, on_tick=None) -> dict:
    """Sampling berkala seluruh queue sekaligus.

    Throughput per queue dihitung dari ID yang hilang dari started registry
    (satu ID hilang = satu job selesai) — sama seperti `report.py`, tapi
    dijalankan serentak untuk semua queue supaya bisa dibandingkan.
    """
    t0 = time.time()
    deadline = t0 + minutes * 60
    names = [h["queue"] for h in handles]

    st = {qn: {"inflight": {}, "seen": set(), "durs": [], "completed": 0,
               "slots": [], "idle_min": None, "ever_idle": False,
               "pending_first": None, "pending_last": 0}
          for qn in names}
    ticks = []

    while True:
        counts = scan_queues(conn, handles)
        fl = fleet(conn)
        tnow = time.time()

        # Job yang selesai sejak tick sebelumnya, dikumpulkan lintas queue dulu
        # supaya pembacaan hash job cukup satu round-trip.
        finished_now = []
        for qn in names:
            s, c = st[qn], counts[qn]
            ids = set(c["started_ids"])
            for jid in ids - s["seen"]:
                s["seen"].add(jid)
                s["inflight"][jid] = tnow
            for jid in list(s["inflight"]):
                if jid not in ids:
                    finished_now.append((qn, jid, s["inflight"].pop(jid)))
        times = job_times(conn, [j for _, j, _ in finished_now])
        for qn, jid, seen_at in finished_now:
            started, ended = times.get(jid, (None, None))
            d = ((ended - started).total_seconds()
                 if started and ended else tnow - seen_at)
            st[qn]["durs"].append(d)
            st[qn]["completed"] += 1

        tick = {"t": tnow - t0, "ts": collect.now_utc(), "queues": {},
                "fleet": fl["states"], "workers": fl["total"]}
        for qn in names:
            s, c = st[qn], counts[qn]
            pool = fl["per_queue"].get(qn, {})
            idle = pool.get("idle", 0)
            if s["pending_first"] is None:
                s["pending_first"] = c["pending"]
            s["pending_last"] = c["pending"]
            s["slots"].append(c["started"])
            s["idle_min"] = idle if s["idle_min"] is None else min(s["idle_min"], idle)
            s["ever_idle"] = s["ever_idle"] or idle > 0
            tick["queues"][qn] = {
                "pending": c["pending"], "started": c["started"],
                "busy": pool.get("busy", 0), "idle": idle,
                "total": pool.get("total", 0), "completed": s["completed"],
            }
        tick["pending_total"] = sum(v["pending"] for v in tick["queues"].values())
        tick["completed_total"] = sum(v["completed"] for v in tick["queues"].values())
        ticks.append(tick)
        if on_tick:
            on_tick(tick)

        if time.time() >= deadline:
            break
        time.sleep(interval)

    span = time.time() - t0
    counts = scan_queues(conn, handles)
    fl = fleet(conn)

    out = []
    for qn in names:
        s, c = st[qn], counts[qn]
        pool = fl["per_queue"].get(qn, {})
        thr = s["completed"] / span if span else 0.0
        growth = (c["pending"] - s["pending_first"]) / span if span else 0.0
        durs = sorted(d for d in s["durs"] if d)
        slots_avg = statistics.fmean(s["slots"]) if s["slots"] else 0.0
        r = {
            "queue": qn,
            "pending_first": s["pending_first"],
            "pending": c["pending"],
            "deferred": c["deferred"],
            "failed": c["failed"],
            "finished": c["finished"],
            "scheduled": c["scheduled"],
            "workers": pool.get("total", 0),
            "busy": pool.get("busy", 0),
            "idle": pool.get("idle", 0),
            "idle_min": s["idle_min"] or 0,
            "ever_idle": s["ever_idle"],
            "completed": s["completed"],
            "throughput_per_s": thr,
            "throughput_per_h": thr * 3600,
            "growth_per_s": growth,
            "arrival_per_s": thr + growth,
            "slots_avg": slots_avg,
            "slots_max": max(s["slots"]) if s["slots"] else 0,
            "eta_drain_s": (c["pending"] / thr if thr > 0 and c["pending"] else None),
            "dur_median": statistics.median(durs) if durs else None,
            "dur_max": max(durs) if durs else None,
            "theoretical_per_h": (slots_avg / statistics.fmean(durs) * 3600
                                  if durs else None),
        }
        r["status"] = status_of(r)
        out.append(r)

    return {"span_s": span, "ticks": ticks, "queues": out, "fleet": fl}


def status_of(q) -> str:
    """Label kondisi satu queue, dibaca dari kombinasi backlog + worker."""
    if q["pending"] > 0 and q["workers"] == 0:
        return "MACET"          # ada job, tidak ada satu pun consumer
    if q["pending"] > 0 and not q["ever_idle"] and q["workers"] > 0:
        return "MENTOK"         # semua worker selalu sibuk, antrean tetap ada
    if q["pending"] > 0:
        return "SIBUK"
    if q["busy"] > 0:
        return "JALAN"
    if q["workers"] > 0:
        return "SANTAI"
    return "DIAM"


# --------------------------------------------------------------------------
# Penarikan kesimpulan
# --------------------------------------------------------------------------

def analyze(data) -> dict:
    qs = data["queues"]
    fl = data["fleet"]
    health = data["health"]

    by = lambda s: [q for q in qs if q["status"] == s]  # noqa: E731
    running = data["running"]

    total_pending = sum(q["pending"] for q in qs)
    first_pending = sum(q["pending_first"] or 0 for q in qs)
    growth = ((total_pending - first_pending) / data["span_s"]
              if data["span_s"] else 0.0)

    hv = None
    if data.get("host"):
        hv = host.verdict(data["host"]["cpu"], data["host"]["snapshot"])

    return {
        "host_verdict": hv,
        "total_pending": total_pending,
        "total_growth_per_s": growth,
        "trend": ("NAIK" if growth > 0.01 else
                  ("TURUN" if growth < -0.01 else "FLAT")),
        "total_completed": sum(q["completed"] for q in qs),
        "total_throughput_per_h": sum(q["throughput_per_h"] for q in qs),
        "macet": by("MACET"),
        "mentok": by("MENTOK"),
        "sibuk": by("SIBUK"),
        "aktif": [q for q in qs if q["completed"] or q["pending"] or q["workers"]],
        "idle_workers": sum(q["idle"] for q in qs if q["pending"] == 0),
        "stuck_jobs": [r for r in running if r["over_timeout"]],
        "long_jobs": [r for r in running
                      if (r["age_s"] or 0) >= LONG_RUN_S and not r["over_timeout"]],
        "stale_workers": fl["stale"],
        "zombie_workers": fl["zombies"],
        "redis_ok": (int(health.get("evicted_keys", 0)) == 0
                     and int(health.get("rejected_connections", 0)) == 0),
        "recent_failures": [f for f in data["failures"] if f["recent"] > 0],
        "timeout_failures": [f for f in data["failures"] if f["recent_timeout"] > 0],
    }


def verdict_line(a) -> str:
    """Satu kalimat: server ini sedang baik-baik saja atau tidak."""
    hv = a["host_verdict"]
    if a["macet"]:
        return (f"**PERLU TINDAKAN** — {len(a['macet'])} queue punya job menumpuk "
                f"tanpa satu pun worker yang melayaninya.")
    if a["stuck_jobs"]:
        return (f"**PERLU TINDAKAN** — {len(a['stuck_jobs'])} job berjalan sudah "
                f"menyentuh batas timeout-nya (kemungkinan menggantung).")
    if hv and (hv["mem_tight"] or hv["swapping"]):
        return f"**MESIN SESAK** — {hv['reason']}"
    if hv and hv["cpu_bound"] and a["mentok"]:
        return (f"**SERVER MENTOK** — CPU mesin sudah jenuh dan "
                f"{len(a['mentok'])} queue tidak pernah punya worker idle.")
    if a["mentok"]:
        return (f"**POOL MENTOK, MESIN BELUM** — {len(a['mentok'])} queue kehabisan "
                f"worker, tetapi mesinnya "
                f"{'masih longgar' if hv else 'belum diukur'}.")
    if a["trend"] == "NAIK" and a["total_pending"]:
        return ("**BEBAN MASUK MELEBIHI KAPASITAS** — total backlog seluruh queue "
                "bertambah selama pengamatan.")
    if a["total_pending"] == 0:
        return "**SEHAT** — tidak ada backlog di queue mana pun."
    return "**NORMAL** — ada antrean, tetapi worker masih sanggup mengejarnya."


# --------------------------------------------------------------------------
# Penyusunan laporan
# --------------------------------------------------------------------------

def render(data, a, show_all=False) -> str:
    started = data["started_at"].astimezone(WIB)
    ended = data["ended_at"].astimezone(WIB)
    fl = data["fleet"]
    P = []

    P.append(f"""# Kondisi Server — Seluruh Queue (Redis/RQ)

> Dokumen ini **dibuat otomatis** oleh `report_all.py`. Jangan diedit manual —
> jalankan ulang generatornya untuk memperbarui angka.

**Waktu observasi:** {tanggal(started)}, {started.strftime('%H:%M')}–{ended.strftime('%H:%M')} WIB
({dur(data['span_s'])} sampling live)
**Sumber data:** Redis `{REDIS_HOST}:{REDIS_PORT}` — 100% dari telemetri RQ
**Cakupan:** {len(data['queues'])} queue{' (dipilih lewat --queues)' if data.get('filtered') else ' terdaftar'}, {fl['total']} worker aktif
""")

    # --- 1. Ringkasan ---
    P.append("## 1. Ringkasan\n\n" + verdict_line(a) + "\n")
    rows = [
        ["Total backlog", f"**{num(a['total_pending'])} job** menunggu di seluruh queue"],
        ["Tren backlog", f"**{a['trend']}**" + (
            "" if a["trend"] == "FLAT" else
            f" ({num(abs(a['total_growth_per_s']) * 3600)} job/jam "
            f"{'bertambah' if a['total_growth_per_s'] > 0 else 'berkurang'})")],
        ["Throughput seluruh server", f"**{num(a['total_throughput_per_h'])} job/jam** "
                                      f"({num(a['total_completed'])} job selesai saat observasi)"],
        ["Armada worker", f"{fl['total']} worker — "
                          + ", ".join(f"{v} {k}" for k, v in sorted(fl["states"].items()))],
        ["Queue bermasalah", (f"{len(a['macet'])} MACET, {len(a['mentok'])} MENTOK, "
                              f"{len(a['sibuk'])} SIBUK")],
    ]
    if a["host_verdict"]:
        hv = a["host_verdict"]
        cpu = data["host"]["cpu"]
        cm = cpu.get("mem") or {}
        rows.append(["Mesin", f"CPU **{num(cpu['busy_avg'], 0)}%** rata-rata"
                              + (f", RAM **{num(cm['used_pct_avg'], 0)}%** "
                                 f"(puncak {num(cm['used_pct_max'], 0)}%)" if cm else "")
                              + f" — {hv['reason']}"])
    P.append("\n" + table(["Metrik", "Nilai"], rows))

    peringatan = []
    if a["macet"]:
        peringatan.append(
            "**Queue tanpa worker:** "
            + ", ".join(f"`{q['queue']}` ({num(q['pending'])} job)" for q in a["macet"])
            + " — job di sini tidak akan pernah diproses sampai ada worker dijalankan.")
    if a["stuck_jobs"]:
        peringatan.append(
            f"**{len(a['stuck_jobs'])} job menyentuh batas timeout** — lihat bagian 4.")
    if a["zombie_workers"]:
        peringatan.append(
            f"**{len(a['zombie_workers'])} entri worker zombi** di `rq:workers` "
            f"(terdaftar tapi datanya sudah hilang) — worker mati tanpa sempat "
            f"membersihkan diri, biasanya kena OOM-kill atau VM restart.")
    if a["stale_workers"]:
        peringatan.append(
            f"**{len(a['stale_workers'])} worker tidak berdetak** melebihi batas wajar "
            f"({num(STALE_BUSY_S, 0)} detik saat busy, {num(STALE_IDLE_S, 0)} detik "
            f"saat idle) — prosesnya kemungkinan hang atau terbekukan.")
    if not a["redis_ok"]:
        peringatan.append("**Redis menolak koneksi / membuang key** — lihat bagian 6.")
    if peringatan:
        P.append("\n**Yang perlu diperhatikan:**\n\n"
                 + "\n".join(f"- {x}" for x in peringatan) + "\n")

    # --- 2. Semua queue ---
    P.append("## 2. Semua Queue\n")
    P.append("\nDiurutkan dari backlog terbesar. `Slot` = job yang benar-benar sedang "
             "berjalan (rata-rata selama observasi).\n")
    shown = [q for q in data["queues"]
             if show_all or q["pending"] or q["workers"] or q["completed"]]
    rows = []
    for q in sorted(shown, key=lambda x: (-x["pending"], -x["throughput_per_h"])):
        rows.append([
            f"`{q['queue']}`", q["status"], num(q["pending"]),
            f"{num(q['workers'])} ({num(q['busy'])}/{num(q['idle'])})",
            num(q["slots_avg"], 1), num(q["completed"]),
            num(q["throughput_per_h"]),
            dur(q["dur_median"]), dur(q["eta_drain_s"]),
        ])
    P.append("\n" + table(
        ["Queue", "Status", "Pending", "Worker (busy/idle)", "Slot", "Selesai",
         "Job/jam", "Durasi median", "ETA habis"],
        rows, ["---", "---", "---:", "---:", "---:", "---:", "---:", "---:", "---:"]))
    if not show_all:
        hidden = len(data["queues"]) - len(shown)
        if hidden:
            P.append(f"\n*{hidden} queue lain kosong total (tanpa job & tanpa worker) "
                     f"— tampilkan dengan `--all`.*\n")

    other = [q for q in data["queues"]
             if q["deferred"] or q["scheduled"] or q["failed"]]
    if other:
        rows = [[f"`{q['queue']}`", num(q["deferred"]), num(q["scheduled"]),
                 num(q["failed"]), num(q["finished"])]
                for q in sorted(other, key=lambda x: -x["failed"])]
        P.append("\n**Registry lain.** `deferred` = menunggu job lain selesai, "
                 "`scheduled` = dijadwalkan ke masa depan (keduanya normal, bukan macet).\n\n"
                 + table(["Queue", "Deferred", "Scheduled", "Failed", "Finished"],
                         rows, ["---", "---:", "---:", "---:", "---:"]))

    # --- 3. Armada worker ---
    P.append("\n## 3. Armada Worker\n")
    grup = Counter()
    for w in fl["workers"]:
        grup[",".join(w["queues"]) or "(tanpa queue)"] += 1
    rows = []
    for names, n in grup.most_common():
        pool = Counter()
        for w in fl["workers"]:
            if (",".join(w["queues"]) or "(tanpa queue)") == names:
                pool[w["state"]] += 1
        rows.append([f"`{names}`", num(n),
                     ", ".join(f"{v} {k}" for k, v in sorted(pool.items()))])
    P.append("\nWorker terikat pada daftar queue-nya sendiri — worker idle di satu "
             "grup **tidak bisa** membantu queue di grup lain.\n\n"
             + table(["Melayani queue", "Jumlah", "Status"], rows,
                     ["---", "---:", "---"]))

    hosts = Counter(w["hostname"] for w in fl["workers"])
    if hosts and max(hosts.values()) > 1:
        rows = [[h, num(n)] for h, n in hosts.most_common(15)]
        P.append("\n**Sebaran per mesin.** Angka CPU/RAM di bagian 5 hanya mewakili "
                 "mesin tempat script ini dijalankan.\n\n"
                 + table(["Hostname", "Worker"], rows, ["---", "---:"]))
        if len(hosts) > 15:
            P.append(f"\n*…dan {len(hosts) - 15} mesin lain.*\n")
    elif len(hosts) > 1:
        # Satu proses per hostname = tiap worker berjalan di container sendiri;
        # daftarnya tidak informatif, cukup jumlahnya.
        P.append(f"\nSetiap worker berjalan di hostname-nya sendiri "
                 f"({len(hosts)} hostname untuk {fl['total']} worker) — pola khas "
                 f"satu container per worker. Angka CPU/RAM di bagian 5 hanya "
                 f"mewakili mesin tempat script ini dijalankan.\n")

    if fl["stale"]:
        rows = [[w["name"][:28], w["hostname"], w["state"],
                 dur(w["heartbeat_age_s"]), dur(w["uptime_s"])]
                for w in sorted(fl["stale"],
                                key=lambda x: -(x["heartbeat_age_s"] or 0))[:15]]
        P.append("\n**Worker tanpa detak.**\n\n"
                 + table(["Worker", "Mesin", "Status", "Detak terakhir", "Umur"],
                         rows, ["---", "---", "---", "---:", "---:"]))

    # --- 4. Job berjalan ---
    P.append("\n## 4. Job yang Sedang Berjalan\n")
    running = data["running"]
    if not running:
        P.append("\nTidak ada job yang sedang dikerjakan saat pengambilan data.\n")
    else:
        rows = [[f"`{r['queue']}`", dur(r["age_s"]),
                 dur(r["timeout_s"]) if r["timeout_s"] else "—",
                 "⚠️ ya" if r["over_timeout"] else "tidak",
                 r["hostname"], (r["description"] or r["job"])[:60]]
                for r in running[:15]]
        P.append(f"\n{num(len(running))} job sedang diproses. "
                 f"{len(running[:15])} terlama:\n\n"
                 + table(["Queue", "Sudah berjalan", "Timeout", "Nyaris habis",
                          "Mesin", "Deskripsi"],
                         rows, ["---", "---:", "---:", "---", "---", "---"]))
    if a["stuck_jobs"]:
        P.append(f"\n⚠️ **{len(a['stuck_jobs'])} job sudah berjalan ≥ "
                 f"{num(STUCK_RATIO * 100, 0)}% dari timeout-nya.** Kalau angka ini "
                 f"tidak berubah pada pengukuran berikutnya, job tersebut menggantung "
                 f"dan slot worker-nya terkunci sampai timeout memaksa berhenti.\n")
    elif a["long_jobs"]:
        P.append(f"\n{len(a['long_jobs'])} job sudah berjalan lebih dari "
                 f"{dur(LONG_RUN_S)} tetapi masih di dalam batas timeout — wajar untuk "
                 f"job besar, tetapi slot-nya memang tidak bisa dipakai job lain.\n")

    # --- 5. Mesin ---
    if a["host_verdict"]:
        # render_host() dipakai ulang dari report.py — judulnya sudah "## 5.",
        # jadi urutan bab di sini disamakan supaya penomorannya tetap benar.
        P.append("\n" + render_host(data, a))
    else:
        P.append(f"""
## 5. Kondisi Mesin (CPU & RAM)

**Tidak terukur** — script dijalankan di luar VM worker, jadi `/proc` mesin tidak
terbaca. Tanpa angka ini, "server mentok atau tidak" hanya bisa dijawab dari sisi
antrean, bukan dari sisi kapasitas mesin.

**Cara melengkapinya:** jalankan `report_all.py` langsung di VM worker, atau
tambahkan `--ssh <target-vm>` supaya `/proc` VM dibaca dari jauh.
""")

    # --- 6. Redis & kegagalan ---
    h = data["health"]
    P.append(f"""
## 6. Redis & Kegagalan

**Redis {'sehat' if a['redis_ok'] else 'PATUT DICURIGAI'}.** Memori """
             f"{h.get('used_memory_human')} (maxmemory {h.get('maxmemory_human')}), "
             f"{h.get('instantaneous_ops_per_sec')} ops/detik, "
             f"{h.get('connected_clients')} klien, "
             f"**{h.get('evicted_keys')} evicted keys**, "
             f"**{h.get('rejected_connections')} rejected connections**, "
             f"uptime {h.get('uptime_in_days')} hari.\n")

    if data["failures"]:
        rows = [[f"`{f['queue']}`", num(f["count"]),
                 f"{num(f['newest_h'], 1)} jam lalu" if f["newest_h"] is not None else "—",
                 num(f["recent"]), num(f["recent_timeout"]), num(f["recent_fast_fail"])]
                for f in sorted(data["failures"], key=lambda x: -x["count"])]
        P.append("\n**Job gagal per queue.** Kolom `< 1 jam` dipecah karena hanya "
                 "kegagalan **timeout** yang menandakan server kewalahan; *fast-fail* "
                 f"(< {num(collect.FAST_FAIL_S, 0)} detik) adalah job yang ditolak "
                 "validasi — bug data, bukan kapasitas.\n\n"
                 + table(["Queue", "Total gagal", "Terbaru", "< 1 jam", "…timeout",
                          "…fast-fail"],
                         rows, ["---", "---:", "---:", "---:", "---:", "---:"]))
        if a["timeout_failures"]:
            names = ", ".join(f"`{f['queue']}` ({f['recent_timeout']}x)"
                              for f in a["timeout_failures"])
            P.append(f"\n⚠️ **Ada job kehabisan waktu dalam 1 jam terakhir:** {names} — "
                     f"ini indikasi nyata server kewalahan.\n")
        elif not a["recent_failures"]:
            P.append("\nTidak ada kegagalan baru selama 1 jam terakhir — kegagalan yang "
                     "tercatat semuanya lama.\n")
    else:
        P.append("\nTidak ada job gagal di queue mana pun.\n")

    # --- 7. Rekomendasi ---
    P.append("\n## 7. Tindakan yang Disarankan\n")
    recs = []
    for q in a["macet"]:
        recs.append(f"**Jalankan worker untuk `{q['queue']}`** — {num(q['pending'])} job "
                    f"menggantung tanpa consumer.")
    if a["stuck_jobs"]:
        qn = Counter(r["queue"] for r in a["stuck_jobs"])
        recs.append("**Periksa job yang menggantung** di "
                    + ", ".join(f"`{k}` ({v} job)" for k, v in qn.most_common())
                    + " — jalankan ulang script ini; kalau umur job-nya terus naik "
                      "tanpa selesai, worker-nya perlu di-restart.")
    if a["zombie_workers"] or a["stale_workers"]:
        bagian = []
        if a["zombie_workers"]:
            bagian.append(f"{len(a['zombie_workers'])} entri zombi")
        if a["stale_workers"]:
            bagian.append(f"{len(a['stale_workers'])} worker tanpa detak")
        recs.append(f"**Bersihkan/restart worker mati** — " + " dan ".join(bagian)
                    + ". Selama masih terdaftar, angka kapasitas pool terlihat lebih "
                      "besar daripada kenyataannya.")

    hv = a["host_verdict"]
    if hv is None:
        recs.append("**Ukur CPU/RAM mesin** — jalankan script ini di VM worker (atau "
                    "pakai `--ssh <target-vm>`). Tanpa itu, keputusan menambah worker "
                    "hanya tebakan.")
    elif hv["cpu_bound"] or hv["mem_tight"] or hv["swapping"]:
        recs.append(f"**Tambah kapasitas mesin** (perbesar instance atau tambah VM). "
                    f"{hv['reason']} Menambah worker di mesin yang sama tidak akan "
                    f"menaikkan throughput.")
    elif a["mentok"]:
        names = ", ".join(f"`{q['queue']}`" for q in a["mentok"][:3])
        recs.append(f"**Mesin masih longgar** ({hv['reason']}) sementara {names} tidak "
                    f"pernah punya worker idle — menambah worker di queue itu layak "
                    f"dicoba bertahap sambil memantau CPU.")
        if a["idle_workers"]:
            recs.append(f"**Ada {num(a['idle_workers'])} worker menganggur** di queue "
                        f"yang backlog-nya 0. Worker idle tidak bisa membantu queue "
                        f"lain — pertimbangkan menggeser jatahnya ke queue yang mentok.")
    if a["trend"] == "NAIK":
        recs.append(f"**Backlog total sedang tumbuh** "
                    f"(+{num(a['total_growth_per_s'] * 3600)} job/jam). Kalau laju masuk "
                    f"bertahan, antrean akan terus memanjang — perlu kapasitas tambahan "
                    f"atau pengereman di sisi produsen job.")
    if not recs:
        recs.append("**Tidak ada tindakan mendesak** — seluruh queue terlayani, mesin "
                    "dan Redis dalam batas wajar. Cukup pantau berkala.")
    recs.append(f"**Pasang alarm** bila total backlog > "
                f"{num(max(500, a['total_pending'] * 2))} job, ada queue berstatus "
                f"MACET, atau ada worker busy yang tidak berdetak > "
                f"{num(STALE_BUSY_S, 0)} detik.")
    P.append("\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(recs, 1)) + "\n")

    ssh_arg = ""
    if data.get("host") and data["host"]["snapshot"].get("remote"):
        ssh_arg = f" --ssh {data['host']['snapshot'].get('source')}"
    P.append(f"""## 8. Cara Reproduksi

```bash
cd experiments/cek-load
./.venv/bin/python report_all.py --minutes {num(data['span_s'] / 60, 0)}{ssh_arg}
```

Untuk mendalami satu queue tertentu, pakai `report.py --queue <nama>`.
""")
    return "\n".join(P)


# --------------------------------------------------------------------------
# Tampilan terminal
# --------------------------------------------------------------------------

def print_table(headers, rows, widths, aligns):
    line = " ".join(h[:w].ljust(w) if a == "<" else h[:w].rjust(w)
                    for h, w, a in zip(headers, widths, aligns))
    print(line)
    print("-" * len(line))
    for r in rows:
        print(" ".join(str(c)[:w].ljust(w) if a == "<" else str(c)[:w].rjust(w)
                       for c, w, a in zip(r, widths, aligns)))


def print_summary(data, a):
    print(f"\n=== SEMUA QUEUE ({dur(data['span_s'])} observasi) ===")
    rows = []
    for q in sorted(data["queues"], key=lambda x: (-x["pending"], -x["throughput_per_h"])):
        if not (q["pending"] or q["workers"] or q["completed"]):
            continue
        rows.append([q["queue"], q["status"], q["pending"],
                     f"{q['workers']}({q['busy']}/{q['idle']})",
                     q["completed"], f"{q['throughput_per_h']:.0f}",
                     dur(q["dur_median"]), dur(q["eta_drain_s"])])
    print_table(["queue", "status", "pending", "worker", "selesai", "job/jam",
                 "durasi med", "eta habis"],
                rows, [53, 7, 8, 11, 8, 8, 12, 12],
                ["<", "<", ">", ">", ">", ">", ">", ">"])
    print()
    print(verdict_line(a).replace("**", ""))


# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--minutes", type=float, default=5.0, help="durasi sampling live")
    p.add_argument("--interval", type=float, default=5.0, help="jeda antar sampel")
    p.add_argument("--all", action="store_true",
                   help="tampilkan juga queue yang kosong total")
    p.add_argument("--queues", default=None,
                   help="batasi ke queue tertentu (pisahkan dengan koma)")
    p.add_argument("--no-host", action="store_true",
                   help="jangan ukur CPU/RAM mesin walau tersedia")
    p.add_argument("--ssh", default=HOST_SSH,
                   help="baca CPU/RAM dari VM lain lewat SSH (default: HOST_SSH "
                        "di .env). Tidak perlu bila script dijalankan di VM itu.")
    p.add_argument("--ssh-cmd", default=HOST_SSH_CMD,
                   help="pembungkus khusus, mis. 'gcloud compute ssh vm --zone z "
                        "--command'")
    p.add_argument("--output", default=None, help="berkas laporan Markdown")
    p.add_argument("--json", default=None, help="tulis juga data mentah ke JSON")
    args = p.parse_args()

    if not args.no_host and (args.ssh or args.ssh_cmd):
        host.use_ssh(target=args.ssh or None, command=args.ssh_cmd or None)

    out = args.output or (
        f"reports/SERVER_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    conn = get_redis()
    conn.ping()
    started_at = collect.now_utc()

    print(f"[1/5] Daftar queue & kondisi Redis…", flush=True)
    health = collect.redis_health(conn)
    names = collect.queue_names(conn)
    if args.queues:
        pilih = {q.strip() for q in args.queues.split(",") if q.strip()}
        names = [q for q in names if q in pilih]
        if not names:
            raise SystemExit(f"Tidak ada queue yang cocok: {sorted(pilih)}")
    handles = queue_handles(conn, names)
    fl0 = fleet(conn)
    print(f"      {len(names)} queue, {fl0['total']} worker "
          f"({', '.join(f'{v} {k}' for k, v in sorted(fl0['states'].items()))})",
          flush=True)

    print("[2/5] Metrik mesin…", flush=True)
    host_on = not args.no_host and host.available()
    if host_on:
        mem0 = host.meminfo()
        print(f"      AKTIF ({host.source_label()}) — {host.cpu_count()} core, "
              f"RAM {mem0.get('total_mb', 0):.0f} MB "
              f"({mem0.get('used_pct', 0):.0f}% terpakai)", flush=True)
    else:
        print("      tidak tersedia (jalankan di VM worker, atau pakai --ssh)",
              flush=True)

    print(f"[3/5] Pengukuran live {args.minutes} menit "
          f"(sampling {args.interval}s, semua queue)…", flush=True)
    print(f"      Baris kedua tiap tick = {QUEUE_TICK_SHOW} queue teraktif: "
          f"nama: <pending>p/<busy worker>b/<selesai kumulatif>s", flush=True)
    sampler = host.HostSampler() if host_on else None

    def on_tick(t):
        cpu = sampler.tick() if sampler else None
        extra = ""
        if cpu:
            mm = cpu.get("mem") or {}
            extra = (f" cpu={cpu['busy']:.0f}%"
                     + (f" ram={mm['used_pct']:.0f}%" if mm else ""))
        aktif = [(qn, v) for qn, v in t["queues"].items()
                 if v["pending"] or v["busy"]]
        aktif.sort(key=lambda kv: (-kv[1]["pending"], -kv[1]["busy"], kv[0]))
        print(f"      [{t['t']:5.0f}s] pending={t['pending_total']:>7} "
              f"queue_aktif={len(aktif):>3} busy={t['fleet'].get('busy', 0):>4} "
              f"idle={t['fleet'].get('idle', 0):>4} "
              f"selesai={t['completed_total']:>5}{extra}", flush=True)
        if aktif:
            # Nama queue-nya ikut dicetak: tanpa ini "queue_aktif=3" tidak
            # memberi tahu queue mana yang sedang menumpuk.
            sisa = len(aktif) - QUEUE_TICK_SHOW
            ringkas = " | ".join(
                f"{qn}: {v['pending']}p/{v['busy']}b/{v['completed']}s"
                for qn, v in aktif[:QUEUE_TICK_SHOW])
            print(f"              {ringkas}"
                  + (f" | +{sisa} queue lain" if sisa > 0 else ""), flush=True)

    live = measure_all(conn, handles, args.minutes, args.interval, on_tick)

    host_data = None
    if host_on:
        summ = sampler.summary()
        if summ:
            host_data = {"snapshot": host.snapshot(), "cpu": summ,
                         "top": host.top_processes(3.0),
                         "mem_top": host.top_memory(6)}

    print("[4/5] Job yang sedang berjalan…", flush=True)
    running = running_jobs(conn, live["fleet"])
    if args.queues:
        # Armada worker tetap dilaporkan utuh (itu kondisi server), tapi daftar
        # job berjalan dibatasi ke queue yang diminta supaya tidak membingungkan.
        running = [r for r in running if r["queue"] in set(names)]

    print("[5/5] Ringkasan kegagalan semua queue…", flush=True)
    failures = collect.failure_summary(conn, names)

    data = {
        "started_at": started_at,
        "ended_at": collect.now_utc(),
        "span_s": live["span_s"],
        "filtered": bool(args.queues),
        "health": health,
        "queues": live["queues"],
        "ticks": live["ticks"],
        "fleet": live["fleet"],
        "running": running,
        "failures": failures,
        "host": host_data,
    }
    a = analyze(data)

    with open(out, "w") as f:
        f.write(render(data, a, show_all=args.all))

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w") as f:
            json.dump({
                "started_at": data["started_at"].isoformat(),
                "ended_at": data["ended_at"].isoformat(),
                "span_s": data["span_s"],
                "health": data["health"],
                "queues": data["queues"],
                "fleet": {k: v for k, v in data["fleet"].items() if k != "workers"},
                "workers": data["fleet"]["workers"],
                "running": data["running"],
                "failures": data["failures"],
                "host": (host_data or {}).get("cpu"),
            }, f, indent=2, default=str)
        print(f"Data mentah: {args.json}")

    print_summary(data, a)
    print(f"\nLaporan ditulis: {out}")


if __name__ == "__main__":
    main()
