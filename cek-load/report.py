"""Generate LAPORAN.md otomatis dari pengukuran live.

Menjalankan seluruh rangkaian pemeriksaan (langkah 1-6 di README), menarik
kesimpulan dari angka yang terukur, lalu menulis laporan Markdown.

Contoh:
    python report.py                                  # default 6 menit
    python report.py --minutes 10 --output LAPORAN.md
    python report.py --queue ocr_agent_prod --skip-fate
    python report.py --ssh vm-worker                   # CPU/RAM VM dibaca dari jauh

CPU & RAM ikut terukur otomatis bila dijalankan di VM worker; dari laptop,
pakai `--ssh <target>` (atau `HOST_SSH` di .env).
"""

import argparse
import os
from datetime import datetime, timedelta, timezone

import collect
import host
from config import (DATA_PARSING_AGENT, HOST_SSH, HOST_SSH_CMD, REDIS_HOST,
                    REDIS_PORT, get_redis)
from payload import profile as payload_profile

WIB = timezone(timedelta(hours=7))
SATURATION_RATIO = 0.85     # throughput/teoritis di atas ini = pool dianggap penuh
RECENT_FAILURE_H = 1.0      # kegagalan lebih baru dari ini dianggap "baru"
MAX_SCALE_FACTOR = 3        # batas usulan penambahan worker (kelipatan pool sekarang)

BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]


def tanggal(dt):
    return f"{dt.day} {BULAN[dt.month - 1]} {dt.year}"


# --------------------------------------------------------------------------
# Format
# --------------------------------------------------------------------------

def num(v, dec=0):
    """Angka gaya Indonesia: pemisah ribuan titik, desimal koma."""
    if v is None:
        return "—"
    s = f"{v:,.{dec}f}"
    return s.translate(str.maketrans({",": ".", ".": ","})) if dec else s.replace(",", ".")


def dur(seconds):
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{num(seconds, 1)} detik"
    if seconds < 5400:
        return f"{num(seconds / 60, 0)} menit"
    return f"{num(seconds / 3600, 1)} jam"


def table(headers, rows, align=None):
    align = align or ["---"] * len(headers)
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(align) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Penarikan kesimpulan
# --------------------------------------------------------------------------

def analyze(data) -> dict:
    live, pools = data["live"], data["pools"]["per_queue"]
    target = data["queue"]
    pool = pools.get(target, {})

    ever_idle = any(s["idle"] > 0 for s in live["samples"])
    theoretical = live.get("theoretical_per_h")
    ratio = (live["throughput_per_h"] / theoretical) if theoretical else None
    pool_saturated = (not ever_idle) and (ratio is not None and ratio >= SATURATION_RATIO)

    growth = live["backlog_growth_per_s"]
    trend = "NAIK" if growth > 0.01 else ("TURUN" if growth < -0.01 else "FLAT")

    health = data["health"]
    redis_ok = (int(health.get("evicted_keys", 0)) == 0
                and int(health.get("rejected_connections", 0)) == 0)

    # Ketimpangan alokasi: queue menumpuk vs worker nganggur di queue lain
    backed_up = [q for q in data["queues"] if q["pending"] > 0]
    idle_elsewhere = sum(
        pools.get(q["queue"], {}).get("idle", 0)
        for q in data["queues"] if q["pending"] == 0
    )
    starved = [
        q for q in backed_up
        if pools.get(q["queue"], {}).get("idle", 0) == 0
        and pools.get(q["queue"], {}).get("total", 0) > 0
    ]
    # Job menumpuk tanpa satu pun worker yang melayani = macet permanen.
    orphaned = [q for q in backed_up
                if pools.get(q["queue"], {}).get("total", 0) == 0]

    # Kegagalan baru hanya berarti "server kewalahan" bila job kehabisan waktu.
    # Fast-fail (ditolak validasi dalam milidetik) adalah bug data, bukan kapasitas.
    recent_failures = [f for f in data["failures"] if f["recent"] > 0]
    overload_failures = [f for f in data["failures"] if f["recent_timeout"] > 0]
    recent_fast_only = [f for f in recent_failures
                        if f["recent_timeout"] == 0 and f["recent_fast_fail"] > 0]

    enc = data.get("payload", {}).get("items_per_job") or {}
    enc_per_job = None
    if len(enc) == 1:
        only = next(iter(enc))
        enc_per_job = only if isinstance(only, int) else None

    hv = None
    if data.get("host"):
        hv = host.verdict(data["host"]["cpu"], data["host"]["snapshot"])

    return {
        "host_verdict": hv,
        "pool_saturated": pool_saturated,
        "ever_idle": ever_idle,
        "ratio": ratio,
        "trend": trend,
        "redis_ok": redis_ok,
        "idle_elsewhere": idle_elsewhere,
        "starved": starved,
        "orphaned": orphaned,
        "backed_up": backed_up,
        "recent_failures": recent_failures,
        "overload_failures": overload_failures,
        "recent_fast_only": recent_fast_only,
        "enc_per_job": enc_per_job,
        "pool_size": pool.get("total", 0),
    }


# --------------------------------------------------------------------------
# Penyusunan laporan
# --------------------------------------------------------------------------

def render_host(data, a) -> str:
    """Bab CPU & RAM — terisi bila report.py dijalankan di VM worker (atau --ssh)."""
    h = data["host"]
    snap, cpu, hv = h["snapshot"], h["cpu"], a["host_verdict"]
    m, ld = snap["mem"], snap["load"]
    mem_s = cpu.get("mem") or {}
    asal = (f"lewat SSH ke `{snap.get('source')}`" if snap.get("remote")
            else "langsung di VM")
    P = [f"""## 5. Kondisi Mesin (CPU & RAM)

Diukur {asal} `{snap.get('hostname')}` selama jendela pengamatan yang
sama — jadi angkanya mewakili beban saat worker benar-benar bekerja.
"""]

    rows = [
        ["CPU terpakai (rata-rata)", f"**{num(cpu['busy_avg'], 1)}%** dari "
                                     f"{snap['cores']} core"],
        ["CPU puncak", f"{num(cpu['busy_max'], 1)}% (p90 {num(cpu['busy_p90'], 1)}%)"],
        ["Rincian CPU", f"user {num(cpu['user_avg'], 1)}%, system "
                        f"{num(cpu['system_avg'], 1)}%, iowait "
                        f"{num(cpu['iowait_avg'], 1)}%, steal {num(cpu['steal_avg'], 1)}%"],
        ["Core jenuh (>90%)", f"maks {cpu['cores_over_90_max']} dari {snap['cores']} core"],
        ["Load average", f"{ld.get('1m')} / {ld.get('5m')} / {ld.get('15m')} "
                         f"(**{num(ld.get('per_core_1m', 0), 2)} per core**)"],
        ["RAM total mesin", f"{num(m['total_mb'])} MB"],
    ]
    if mem_s:
        # RAM disampel tiap tick, bukan sekali di akhir: pemakaian bisa sempat
        # memuncak lalu turun lagi begitu job selesai.
        rows += [
            ["RAM terpakai (rata-rata)",
             f"**{num(mem_s['used_avg_mb'])} MB "
             f"({num(mem_s['used_pct_avg'], 0)}%)** dari {num(mem_s['total_mb'])} MB"],
            ["RAM puncak", f"**{num(mem_s['used_max_mb'])} MB "
                           f"({num(mem_s['used_pct_max'], 0)}%)** "
                           f"— terendah {num(mem_s['used_min_mb'])} MB"],
            ["RAM sisa terkecil", f"{num(mem_s['available_min_mb'])} MB tersedia"],
            ["Cache/buffer", f"{num(m['cached_mb'] + m.get('buffers_mb', 0))} MB "
                             f"(bisa dilepas kalau RAM menipis)"],
            ["Swap terpakai", f"maks {num(mem_s['swap_max_mb'])} MB dari "
                              f"{num(m['swap_total_mb'])} MB (perubahan "
                              f"{'+' if mem_s['swap_growth_mb'] >= 0 else '−'}"
                              f"{num(abs(mem_s['swap_growth_mb']), 1)} MB "
                              f"selama observasi)"],
        ]
    else:
        rows += [
            ["RAM terpakai", f"**{num(m['used_mb'])} / {num(m['total_mb'])} MB "
                             f"({num(m['used_pct'], 0)}%)**"],
            ["Swap terpakai",
             f"{num(m['swap_used_mb'])} MB dari {num(m['swap_total_mb'])} MB"],
        ]
    if snap.get("pressure"):
        rows.append(["PSI avg60 (some)", ", ".join(
            f"{k} {num(v, 1)}%" for k, v in snap["pressure"].items())])
    P.append(table(["Metrik", "Nilai"], rows))

    P.append(f"\n**Kesimpulan mesin:** {hv['reason']}\n")

    flags = []
    if hv["overloaded"]:
        flags.append(f"Load per core {num(ld.get('per_core_1m', 0), 2)} (≥ 1,00) — "
                     f"ada proses yang antre menunggu CPU.")
    if hv["stealing"]:
        flags.append(f"Steal time {num(cpu['steal_avg'], 1)}% — VM kekurangan jatah CPU "
                     f"dari host fisik (tetangga berisik / kuota kredit habis).")
    if hv["mem_tight"]:
        flags.append(f"RAM terpakai {num(hv['mem_used_pct'], 0)}% (≥ "
                     f"{num(host.MEM_TIGHT_PCT, 0)}%) — memori nyaris habis; "
                     f"menambah worker justru berisiko kena OOM-kill.")
    if hv["swapping"]:
        flags.append(f"Swap terpakai {num(max(m['swap_used_mb'], mem_s.get('swap_max_mb', 0)))} MB "
                     f"— RAM kurang, ini memperlambat semua proses.")
    avail_min = mem_s.get("available_min_mb", m["available_mb"])
    if m["total_mb"] and avail_min < max(512, m["total_mb"] * 0.1):
        flags.append(f"Sisa RAM sempat tinggal {num(avail_min)} MB dari "
                     f"{num(m['total_mb'])} MB — margin tipis untuk lonjakan job besar.")
    if cpu["iowait_avg"] >= 10:
        flags.append(f"iowait {num(cpu['iowait_avg'], 1)}% — banyak waktu terbuang "
                     f"menunggu disk/jaringan, bukan menghitung.")
    if flags:
        P.append("\n**Tanda yang perlu diperhatikan:**\n\n"
                 + "\n".join(f"- {x}" for x in flags) + "\n")

    if h.get("top"):
        rows = [[num(p["cpu_pct"], 1) + "%", num(p["rss_mb"]) + " MB", p["pid"], p["name"]]
                for p in h["top"]]
        P.append("\n**Proses paling boros CPU:**\n\n"
                 + table(["CPU", "RSS", "PID", "Proses"], rows,
                         ["---:", "---:", "---:", "---"]))

    if h.get("mem_top"):
        total = m["total_mb"] or 1
        rows = [[num(p["rss_mb"]) + " MB", num(p["rss_mb"] / total * 100, 1) + "%",
                 p["pid"], p["name"]] for p in h["mem_top"]]
        P.append("\n**Proses paling boros RAM:**\n\n"
                 + table(["RSS", "% RAM mesin", "PID", "Proses"], rows,
                         ["---:", "---:", "---:", "---"]))
    return "\n".join(P)


def render(data, a) -> str:
    live = data["live"]
    target = data["queue"]
    started = data["started_at"].astimezone(WIB)
    ended = data["ended_at"].astimezone(WIB)
    P = []

    P.append(f"""# Cek Kapasitas — Worker Background (Redis/RQ)

> Dokumen ini **dibuat otomatis** oleh `report.py`. Jangan diedit manual —
> jalankan ulang generatornya untuk memperbarui angka.

**Waktu observasi:** {tanggal(started)}, {started.strftime('%H:%M')}–{ended.strftime('%H:%M')} WIB
({dur(live['span_s'])} sampling live)
**Sumber data:** Redis `{REDIS_HOST}:{REDIS_PORT}` — 100% dari telemetri RQ
**Queue yang dianalisis:** `{target}`
""")

    # --- 1. Jawaban singkat ---
    hv = a["host_verdict"]
    saturated_detail = (
        f"Selama {dur(live['span_s'])} pengamatan, queue ini **tidak pernah punya "
        f"satu pun worker idle** — persis {a['pool_size']} slot, semuanya selalu `busy`. "
        f"Sementara itu {num(a['idle_elsewhere'])} worker lain menganggur karena "
        f"melayani queue yang kosong.")

    if a["pool_saturated"] and hv:
        cpu = data["host"]["cpu"]
        hm = data["host"]["snapshot"]["mem"]
        cm = cpu.get("mem") or {}
        ram_txt = (f"RAM **{num(cm['used_avg_mb'])} / {num(cm['total_mb'])} MB "
                   f"({num(cm['used_pct_avg'], 0)}%, puncak "
                   f"{num(cm['used_pct_max'], 0)}%)**"
                   if cm else
                   f"RAM **{num(hm['used_mb'])} / {num(hm['total_mb'])} MB "
                   f"({num(hm['used_pct'], 0)}%)**")
        detail = (saturated_detail + f" CPU mesin rata-rata "
                  f"**{num(cpu['busy_avg'], 0)}%** dari {data['host']['snapshot']['cores']} "
                  f"core dan {ram_txt} selama periode yang sama.")
        if hv["cpu_bound"] or hv["mem_tight"] or hv["swapping"]:
            verdict = (f"**Pool worker `{target}`: YA, sudah 100% mentok.**\n"
                       f"**Server (mesin): JUGA sudah mentok** — {hv['reason']}\n\n"
                       f"Artinya menambah worker tidak akan menolong; yang dibutuhkan "
                       f"adalah mesin yang lebih besar atau mesin tambahan.")
        else:
            verdict = (f"**Pool worker `{target}`: YA, sudah 100% mentok.**\n"
                       f"**Server (mesin): BELUM mentok** — {hv['reason']}\n\n"
                       f"Artinya yang membatasi adalah *jumlah worker* yang dialokasikan "
                       f"ke queue itu, bukan kapasitas mesinnya.")
    elif a["pool_saturated"]:
        verdict = (f"**Pool worker `{target}`: YA, sudah 100% mentok.**\n"
                   f"**Server (mesin): TIDAK terbukti mentok** — yang mentok adalah "
                   f"*jumlah worker* yang dialokasikan ke queue itu, bukan kapasitas mesinnya.")
        detail = saturated_detail
    elif a["ever_idle"]:
        verdict = (f"**Pool worker `{target}`: BELUM mentok.**\n"
                   f"Masih ada worker yang sempat idle selama pengamatan — "
                   f"artinya pool sanggup menyerap beban saat ini.")
        detail = (f"Utilisasi terukur {num((a['ratio'] or 0) * 100, 0)}% dari kapasitas "
                  f"teoritis pool.")
    else:
        verdict = (f"**Pool worker `{target}`: terpakai penuh, tetapi throughput "
                   f"belum menyentuh kapasitas teoritis.**")
        detail = (f"Tidak ada worker idle, namun throughput baru "
                  f"{num((a['ratio'] or 0) * 100, 0)}% dari kapasitas teoritis — "
                  f"kemungkinan job tertahan menunggu I/O atau layanan lain.")

    P.append(f"## 1. Jawaban Singkat\n\n{verdict}\n\n{detail}\n")

    # --- 2. Angka kapasitas ---
    rows = [
        ["Throughput terukur", f"**{num(live['throughput_per_s'], 3)} job/detik "
                               f"= {num(live['throughput_per_h'])} job/jam**"],
        ["Job selesai saat observasi", f"{num(live['completed'])} job dalam {dur(live['span_s'])}"],
        ["Slot paralel", f"{live['slots_min']}–{live['slots_max']} "
                         f"(rata-rata {num(live['slots_avg'], 2)})"],
    ]
    if a["enc_per_job"]:
        eff = live["throughput_per_h"] * a["enc_per_job"]
        rows.insert(1, ["Isi 1 job", f"**{a['enc_per_job']} encounter** "
                                     f"(dari {data['payload']['sampled']} payload)"])
        rows.insert(2, ["Kapasitas efektif", f"**±{num(eff)} encounter/jam**"])
    if live.get("dur_median"):
        rows.append(["Durasi per job",
                     f"median **{dur(live['dur_median'])}**, avg **{dur(live['dur_avg'])}**, "
                     f"p90 **{dur(live['dur_p90'])}**, maks **{dur(live['dur_max'])}**"])
        rows.append(["Kapasitas teoritis",
                     f"{num(live['slots_avg'], 1)} slot ÷ {dur(live['dur_avg'])} "
                     f"= **{num(live['theoretical_per_h'])} job/jam**"])

    P.append("## 2. Angka Kapasitas Terukur\n\n" + table(["Metrik", "Nilai"], rows))

    if a["ratio"] is not None:
        if a["ratio"] >= SATURATION_RATIO:
            rel = "≈" if a["ratio"] >= 0.95 else "mendekati"
            P.append(f"\n> Throughput terukur ({num(live['throughput_per_h'])}/jam) {rel} "
                     f"kapasitas teoritis ({num(live['theoretical_per_h'])}/jam) — selisih "
                     f"{num(abs(1 - a['ratio']) * 100, 1)}%. Pool berjalan pada utilisasi "
                     f"**~100%**: praktis tidak ada kapasitas tersisa.\n")
        else:
            P.append(f"\n> Throughput terukur baru {num(a['ratio'] * 100, 0)}% dari kapasitas "
                     f"teoritis — masih ada ruang di pool ini.\n")

    waits = data["waits"]["head"]
    P.append(f"\n**Backlog saat ini:** {num(live['pending_last'])} job → "
             f"**ETA habis ±{dur(live['eta_drain_s'])}** (bila tidak ada job masuk lagi).")
    if waits:
        P.append(f"**Waktu tunggu job terdepan di antrean: ±{dur(max(waits))}** "
                 f"sebelum mulai diproses.\n")

    # --- 3. Tren beban ---
    s = live["samples"]
    step = max(1, len(s) // 5)
    rows = [[x["ts"].astimezone(WIB).strftime("%H:%M:%S"), num(x["pending"]),
             "NAIK" if x["d_pending"] > 0 else ("TURUN" if x["d_pending"] < 0 else "FLAT")]
            for x in s[::step]]
    if rows:
        rows[0][2] = "—"
    P.append("## 3. Tren Beban Masuk\n\n"
             + table(["Jam", "Backlog", "Tren"], rows, ["---", "---:", "---"]))

    arr = live["arrival_per_s"]
    if a["trend"] == "NAIK":
        P.append(f"\nBacklog **tumbuh** {num(live['backlog_growth_per_s'], 3)} job/detik: "
                 f"laju masuk (±{num(arr * 3600)}/jam) melebihi kapasitas proses "
                 f"(±{num(live['throughput_per_h'])}/jam). **Beban masuk masih berjalan "
                 f"dan server tidak sanggup mengejar.**\n")
    elif a["trend"] == "TURUN":
        masuk = (f"±{num(arr * 3600)}/jam" if arr * 3600 >= 1
                 else "praktis nol")
        P.append(f"\nBacklog **menyusut** — laju masuk ({masuk}) sudah di bawah kapasitas "
                 f"proses. Hit data selesai; sekarang fase **menguras backlog** dengan "
                 f"laju ±{num(live['throughput_per_h'])}/jam.\n")
    else:
        P.append("\nBacklog **datar** selama pengamatan.\n")

    if data.get("fate"):
        f = data["fate"]
        P.append(f"Pada jendela {dur(f['wait_s'])} terpisah: **{num(f['entered'])} job baru "
                 f"masuk**, {num(f['left'])} job keluar antrean "
                 f"({', '.join(f'{v} {k}' for k, v in f['fates'].items())}).")
        if f["left"] and f["processed"] == f["left"]:
            P.append("Seluruhnya benar-benar diproses — tidak ada yang dibuang/dedup, "
                     "jadi angka throughput di atas sahih.\n")
        elif f["left"]:
            P.append("**Sebagian job hilang tanpa diproses** — angka throughput di atas "
                     "perlu ditafsir ulang.\n")

    # --- 4. Bukti pendukung ---
    P.append("## 4. Kenapa Server Belum Tentu Mentok\n")
    rows = []
    for q in sorted(data["queues"], key=lambda x: -x["pending"]):
        pl = data["pools"]["per_queue"].get(q["queue"], {})
        if q["pending"] == 0 and pl.get("total", 0) == 0:
            continue
        name = f"**{q['queue']}**" if q["queue"] == target else q["queue"]
        rows.append([name, num(pl.get("total", 0)), num(pl.get("busy", 0)),
                     num(pl.get("idle", 0)), num(q["pending"])])
    P.append("\n**a. Alokasi worker.**\n\n"
             + table(["Queue", "Worker", "Busy", "Idle", "Pending"],
                     rows, ["---", "---:", "---:", "---:", "---:"]))

    if a["starved"] and a["idle_elsewhere"]:
        names = ", ".join(f"`{q['queue']}`" for q in a["starved"][:3])
        P.append(f"\nQueue yang menumpuk ({names}) tidak punya worker idle sama sekali, "
                 f"sementara {num(a['idle_elsewhere'])} worker menganggur di queue yang "
                 f"backlog-nya 0. Worker idle **tidak bisa** membantu — tiap pool terikat "
                 f"ke queue-nya sendiri.\n")

    if a["orphaned"]:
        names = ", ".join(f"`{q['queue']}` ({num(q['pending'])} job)" for q in a["orphaned"])
        P.append(f"\n⚠️ **Queue tanpa worker sama sekali:** {names}. Job di sini "
                 f"**tidak akan pernah diproses** sampai ada worker yang dijalankan "
                 f"untuk queue tersebut.\n")

    h = data["health"]
    P.append(f"\n**b. Redis {'bukan' if a['redis_ok'] else 'PATUT DICURIGAI sebagai'} "
             f"bottleneck.** Memori {h.get('used_memory_human')} "
             f"(maxmemory {h.get('maxmemory_human')}), {h.get('instantaneous_ops_per_sec')} "
             f"ops/detik, **{h.get('evicted_keys')} evicted keys**, "
             f"**{h.get('rejected_connections')} rejected connections**, "
             f"uptime {h.get('uptime_in_days')} hari.\n")

    downstream = [q for q in data["queues"]
                  if q["queue"] != target and q["pending"] == 0
                  and data["pools"]["per_queue"].get(q["queue"], {}).get("total", 0) > 0]
    if downstream:
        P.append(f"\n**c. Downstream punya headroom.** {len(downstream)} queue lain "
                 f"backlog-nya 0 dengan worker idle — kalau `{target}` dipercepat, "
                 f"tahap berikutnya masih sanggup menyerap.\n")

    P.append("\n**d. Kegagalan job.**\n")
    if data["failures"]:
        rows = [[f["queue"], num(f["count"]),
                 f"{num(f['newest_h'], 1)} jam lalu" if f["newest_h"] is not None else "—",
                 dur(f["dur_median"]),
                 f"{num(f['hit_timeout'])} dari {num(f['count'])}"]
                for f in sorted(data["failures"], key=lambda x: -x["count"])]
        P.append("\n" + table(["Queue", "Gagal", "Terbaru", "Durasi median", "Kena timeout"],
                              rows, ["---", "---:", "---:", "---:", "---:"]))

    win = f"< {num(RECENT_FAILURE_H, 0)} jam"
    if a["overload_failures"]:
        names = ", ".join(f"`{f['queue']}` ({f['recent_timeout']}x)"
                          for f in a["overload_failures"])
        P.append(f"\n⚠️ **Ada job yang kehabisan waktu (timeout) dalam {win} terakhir:** "
                 f"{names} — ini indikasi nyata server kewalahan.\n")
    elif a["recent_fast_only"]:
        names = ", ".join(f"`{f['queue']}` ({f['recent_fast_fail']}x)"
                          for f in a["recent_fast_only"])
        P.append(f"\nAda kegagalan baru ({win}) di {names}, tetapi semuanya **fast-fail "
                 f"(< {num(collect.FAST_FAIL_S, 0)} detik)** — job ditolak validasi, "
                 f"bukan kehabisan sumber daya. **Bukan isu kapasitas.**\n")
    elif data["failures"]:
        oldest_recent = min(f["newest_h"] for f in data["failures"]
                            if f["newest_h"] is not None)
        P.append(f"\nKegagalan terbaru pun sudah berumur {num(oldest_recent, 1)} jam, dan "
                 f"tidak ada kegagalan baru selama observasi — **bukan isu kapasitas**.\n")
    else:
        P.append("\nTidak ada job gagal sama sekali.\n")

    # --- 5. Kondisi mesin ---
    if a["host_verdict"]:
        P.append(render_host(data, a))
    else:
        P.append(f"""## 5. Yang Belum Bisa Dipastikan

Pengecekan ini hanya lewat port Redis (`{REDIS_PORT}`) — **tanpa akses CPU/RAM host**,
karena `report.py` dijalankan dari luar VM. Itu satu-satunya variabel yang
menentukan apakah menambah worker akan menaikkan throughput:

- CPU host masih longgar → menambah worker menaikkan throughput hampir linier.
- CPU host sudah jenuh → menambah worker **tidak akan** menambah throughput,
  hanya memperbanyak rebutan CPU.

**Cara melengkapinya:** salin folder ini ke VM yang menjalankan worker, lalu
jalankan `report.py` di sana. Metrik CPU/RAM akan ikut terukur otomatis dan
bagian ini berganti menjadi kesimpulan yang pasti. Kalau tetap ingin dijalankan
dari laptop, tambahkan `--ssh <target-vm>` supaya `/proc` VM dibaca dari jauh.
""")

    # --- 6. Rekomendasi ---
    P.append("## 6. Rekomendasi\n")
    hv = a["host_verdict"]
    if hv is None:
        recs = ["**Cek CPU/RAM host lebih dulu** — jalankan `report.py` langsung di "
                "VM worker (atau dari laptop dengan `--ssh <target-vm>`) agar "
                "CPU/RAM ikut terukur. Ini menentukan semua langkah berikutnya."]
    elif hv["cpu_bound"] or hv["mem_tight"] or hv["swapping"]:
        recs = [f"**Tambah kapasitas mesin** (naikkan ukuran instance atau tambah VM). "
                f"{hv['reason']} Menggeser/menambah worker di mesin yang sama "
                f"**tidak akan** menaikkan throughput."]
    else:
        recs = [f"**Mesin masih punya ruang** — {hv['reason']} Jadi penambahan worker "
                f"di bawah ini layak dicoba."]

    if a["pool_saturated"] and a["idle_elsewhere"] and not (
            hv and (hv["cpu_bound"] or hv["mem_tight"] or hv["swapping"])):
        donors = sorted(
            ((qn, p.get("idle", 0)) for qn, p in data["pools"]["per_queue"].items()
             if qn != target and p.get("idle", 0) > 0
             and next((q["pending"] for q in data["queues"] if q["queue"] == qn), 0) == 0),
            key=lambda x: -x[1])[:2]
        donor_txt = ", ".join(f"`{qn}` ({n} idle)" for qn, n in donors) or "pool lain"
        # Dibatasi MAX_SCALE_FACTOR: menyerap semua worker idle sekaligus akan
        # menghasilkan proyeksi yang jauh melampaui kemampuan nyata mesin.
        new_size = min(a["pool_size"] * MAX_SCALE_FACTOR,
                       a["pool_size"] + sum(n for _, n in donors))
        if live.get("theoretical_per_h") and a["pool_size"]:
            proj = live["theoretical_per_h"] / a["pool_size"] * new_size
            eta = live["pending_last"] / (proj / 3600) if proj else None
            prefix = "**Naikkan worker**" if hv else "**Jika CPU longgar:** naikkan worker"
            recs.append(f"{prefix} `{target}` bertahap dari "
                        f"{a['pool_size']} → {new_size} dengan menggeser jatah dari "
                        f"{donor_txt}, sambil memantau CPU tiap tahap. Batas atas "
                        f"teoretis: {num(live['throughput_per_h'])} → "
                        f"**±{num(proj)} job/jam** (backlog habis ±{dur(eta)}) — angka ini "
                        f"mengasumsikan penskalaan linier dan CPU tidak jadi penghalang, "
                        f"jadi perlakukan sebagai batas atas, bukan target.")
        if not hv:
            recs.append("**Jika CPU jenuh:** tambah mesin / naikkan ukuran instance — "
                        "menambah worker di mesin yang sama tidak akan menolong.")
    for q in a["starved"]:
        if q["queue"] == target:
            continue
        pl = data["pools"]["per_queue"].get(q["queue"], {})
        recs.append(f"**`{q['queue']}` juga tercekik**: {num(q['pending'])} pending "
                    f"+ {num(q['deferred'])} deferred dengan hanya "
                    f"{num(pl.get('total', 0))} worker.")
    for q in a["orphaned"]:
        recs.append(f"**Jalankan worker untuk `{q['queue']}`** — {num(q['pending'])} job "
                    f"menggantung tanpa consumer.")
    thr = max(500, int(live["throughput_per_h"] / 2))
    recs.append(f"**Pasang alarm** bila `pending` queue `{target}` > {num(thr)} atau usia "
                f"job terdepan > 15 menit.")
    P.append("\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(recs, 1)) + "\n")

    ssh_arg = ""
    if data.get("host") and data["host"]["snapshot"].get("remote"):
        ssh_arg = f" --ssh {data['host']['snapshot'].get('source')}"
    P.append(f"""## 7. Cara Reproduksi

```bash
cd experiments/cek-load
./.venv/bin/python report.py --queue {target} --minutes {num(live['span_s'] / 60, 0)}{ssh_arg}
```

Langkah manual per bagian ada di [`README.md`](README.md).
""")

    return "\n".join(P)


# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--queue", default=DATA_PARSING_AGENT)
    p.add_argument("--minutes", type=float, default=6.0, help="durasi sampling live")
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--fate-wait", type=float, default=120.0,
                   help="durasi pengecekan nasib job yang keluar antrean")
    p.add_argument("--skip-fate", action="store_true",
                   help="lewati pengecekan nasib job (lebih cepat)")
    p.add_argument("--sample", type=int, default=40, help="jumlah payload job disampel")
    p.add_argument("--no-host", action="store_true",
                   help="jangan ukur CPU/RAM mesin walau tersedia")
    p.add_argument("--ssh", default=HOST_SSH,
                   help="baca CPU/RAM dari VM lain lewat SSH (default: HOST_SSH "
                        "di .env). Tidak perlu bila script dijalankan di VM itu.")
    p.add_argument("--ssh-cmd", default=HOST_SSH_CMD,
                   help="pembungkus khusus, mis. 'gcloud compute ssh vm --zone z "
                        "--command'")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    if not args.no_host and (args.ssh or args.ssh_cmd):
        host.use_ssh(target=args.ssh or None, command=args.ssh_cmd or None)

    out = args.output or (
        f"reports/LAPORAN_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    conn = get_redis()
    conn.ping()
    started_at = collect.now_utc()

    print(f"[1/6] Kondisi Redis & queue…", flush=True)
    health = collect.redis_health(conn)
    queues = collect.queue_stats(conn)
    pools = collect.worker_pools(conn)

    print(f"[2/6] Profil payload job ({args.sample} sampel)…", flush=True)
    try:
        pl = payload_profile(conn, args.queue, args.sample)
    except Exception as e:
        print(f"      dilewati: {type(e).__name__}: {e}")
        pl = {}

    host_on = not args.no_host and host.available()
    if host_on:
        mem0 = host.meminfo()
        print(f"      metrik host AKTIF ({host.source_label()}) — "
              f"{host.cpu_count()} core, RAM {mem0.get('total_mb', 0):.0f} MB "
              f"({mem0.get('used_pct', 0):.0f}% terpakai)", flush=True)
    else:
        print("      metrik host tidak tersedia (jalankan di VM worker, atau "
              "pakai --ssh <target>, untuk mengukur CPU/RAM)", flush=True)

    print(f"[3/6] Pengukuran live {args.minutes} menit "
          f"(sampling {args.interval}s)…", flush=True)

    sampler = host.HostSampler() if host_on else None

    def tick(s):
        cpu = sampler.tick() if sampler else None
        extra = ""
        if cpu:
            mm = cpu.get("mem") or {}
            extra = (f" cpu={cpu['busy']:.0f}%"
                     + (f" ram={mm['used_mb']:.0f}MB/{mm['used_pct']:.0f}%"
                        if mm else ""))
        print(f"      [{s['t']:5.0f}s] pending={s['pending']:>6} "
              f"slot={s['active_slots']} busy={s['busy']} idle={s['idle']} "
              f"selesai={s['completed_total']}{extra}", flush=True)

    live = collect.measure_live(conn, args.queue, args.minutes, args.interval, tick)

    host_data = None
    if host_on:
        summ = sampler.summary()
        if summ:
            host_data = {"snapshot": host.snapshot(), "cpu": summ,
                         "top": host.top_processes(3.0),
                         "mem_top": host.top_memory(6)}

    print("[4/6] Waktu tunggu antrean…", flush=True)
    waits = collect.queue_waits(conn, args.queue)

    fate = None
    if not args.skip_fate:
        print(f"[5/6] Nasib job yang keluar antrean ({args.fate_wait:.0f} detik)…",
              flush=True)
        fate = collect.dequeue_fate(conn, args.queue, args.fate_wait)
    else:
        print("[5/6] Nasib job — dilewati.", flush=True)

    print("[6/6] Ringkasan kegagalan…", flush=True)
    failures = collect.failure_summary(conn)

    data = {
        "queue": args.queue,
        "started_at": started_at,
        "ended_at": collect.now_utc(),
        "health": health,
        "queues": queues,
        "pools": pools,
        "payload": pl,
        "live": live,
        "waits": waits,
        "fate": fate,
        "failures": failures,
        "host": host_data,
    }
    a = analyze(data)

    with open(out, "w") as f:
        f.write(render(data, a))

    print(f"\nLaporan ditulis: {out}")
    line = (f"Kesimpulan: pool {'MENTOK' if a['pool_saturated'] else 'belum mentok'}, "
            f"backlog {a['trend']}, throughput {num(live['throughput_per_h'])} job/jam")
    if a["host_verdict"]:
        hv = a["host_verdict"]
        cm = host_data["cpu"].get("mem") or {}
        ram = (f", RAM {num(cm['used_pct_avg'], 0)}% (puncak "
               f"{num(cm['used_pct_max'], 0)}%, {num(cm['used_avg_mb'])} MB)"
               if cm else "")
        line += (f", mesin "
                 f"{'MENTOK' if hv['cpu_bound'] or hv['mem_tight'] else 'masih longgar'} "
                 f"(CPU {num(host_data['cpu']['busy_avg'], 0)}%{ram})")
    print(line)


if __name__ == "__main__":
    main()
