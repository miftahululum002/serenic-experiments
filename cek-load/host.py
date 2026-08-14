"""Metrik CPU & RAM mesin — dipakai saat script dijalankan DI VM server.

Sengaja memakai pustaka standar saja (baca `/proc` langsung), supaya bisa
langsung jalan di VM tanpa perlu meng-install apa pun. Kalau dijalankan di luar
Linux (mis. laptop lewat port-forward), `available()` mengembalikan False dan
bagian ini otomatis dilewati oleh `report.py`.

Pemakaian:
    import host
    s = host.CpuSampler()
    ...                       # biarkan beban berjalan
    hasil = s.tick()          # persentase CPU sejak tick sebelumnya
"""

import os
import time

PROC = "/proc"
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

# Ambang penilaian (persen CPU rata-rata selama pengamatan)
CPU_SATURATED = 85.0    # di atas ini: mesin jadi penghambat
CPU_ROOMY = 70.0        # di bawah ini: masih ada ruang untuk menambah worker
MEM_TIGHT_PCT = 90.0    # pemakaian RAM di atas ini dianggap sesak


def available() -> bool:
    return os.path.isdir(PROC) and os.path.exists(f"{PROC}/stat")


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------

FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


def _cpu_times() -> dict:
    """Baca akumulasi waktu CPU dari /proc/stat (satuan jiffies)."""
    out = {}
    for line in _read(f"{PROC}/stat").splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        name = parts[0]
        vals = [int(v) for v in parts[1:9]]
        out[name] = dict(zip(FIELDS, vals))
    return out


def cpu_count() -> int:
    return sum(1 for k in _cpu_times() if k != "cpu")


def _delta_pct(prev: dict, cur: dict) -> dict:
    """Ubah selisih dua pembacaan /proc/stat jadi persentase."""
    total = sum(cur[f] - prev[f] for f in FIELDS)
    if total <= 0:
        return {}
    pct = {f: (cur[f] - prev[f]) / total * 100 for f in FIELDS}
    pct["busy"] = 100.0 - pct["idle"] - pct["iowait"]
    return pct


class CpuSampler:
    """Hitung persentase CPU antar pemanggilan `tick()`."""

    def __init__(self):
        self._prev = _cpu_times()
        self._prev_t = time.time()
        self.samples = []       # riwayat persentase agregat

    def tick(self) -> dict:
        cur = _cpu_times()
        agg = _delta_pct(self._prev.get("cpu", {}), cur.get("cpu", {})) \
            if "cpu" in self._prev and "cpu" in cur else {}
        per_core = []
        for name in sorted(k for k in cur if k != "cpu"):
            if name in self._prev:
                d = _delta_pct(self._prev[name], cur[name])
                if d:
                    per_core.append(d["busy"])
        self._prev, self._prev_t = cur, time.time()
        if agg:
            agg["per_core_busy"] = per_core
            agg["cores_over_90"] = sum(1 for c in per_core if c >= 90)
            self.samples.append(agg)
        return agg

    def summary(self) -> dict:
        if not self.samples:
            return {}
        busy = [s["busy"] for s in self.samples]
        busy_sorted = sorted(busy)
        return {
            "n": len(busy),
            "busy_avg": sum(busy) / len(busy),
            "busy_min": busy_sorted[0],
            "busy_max": busy_sorted[-1],
            "busy_p90": busy_sorted[int(len(busy_sorted) * 0.9)]
            if len(busy_sorted) >= 5 else busy_sorted[-1],
            "iowait_avg": sum(s["iowait"] for s in self.samples) / len(self.samples),
            "steal_avg": sum(s["steal"] for s in self.samples) / len(self.samples),
            "system_avg": sum(s["system"] for s in self.samples) / len(self.samples),
            "user_avg": sum(s["user"] for s in self.samples) / len(self.samples),
            "cores_over_90_max": max(s["cores_over_90"] for s in self.samples),
        }


# --------------------------------------------------------------------------
# Memori & beban
# --------------------------------------------------------------------------

def meminfo() -> dict:
    """RAM & swap dalam MB, plus persentase terpakai."""
    vals = {}
    for line in _read(f"{PROC}/meminfo").splitlines():
        k, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            vals[k] = int(parts[0]) / 1024      # kB -> MB

    total = vals.get("MemTotal", 0)
    avail = vals.get("MemAvailable", vals.get("MemFree", 0))
    sw_total = vals.get("SwapTotal", 0)
    sw_free = vals.get("SwapFree", 0)
    return {
        "total_mb": total,
        "available_mb": avail,
        "used_mb": total - avail,
        "used_pct": (total - avail) / total * 100 if total else 0,
        "cached_mb": vals.get("Cached", 0),
        "swap_total_mb": sw_total,
        "swap_used_mb": sw_total - sw_free,
        "swap_used_pct": (sw_total - sw_free) / sw_total * 100 if sw_total else 0,
    }


def loadavg() -> dict:
    parts = _read(f"{PROC}/loadavg").split()
    if len(parts) < 3:
        return {}
    n = cpu_count() or 1
    l1, l5, l15 = (float(p) for p in parts[:3])
    return {"1m": l1, "5m": l5, "15m": l15, "cores": n,
            "per_core_1m": l1 / n, "per_core_15m": l15 / n}


def pressure() -> dict:
    """PSI (/proc/pressure) — indikator paling langsung untuk 'nunggu sumber daya'."""
    out = {}
    for res in ("cpu", "memory", "io"):
        txt = _read(f"{PROC}/pressure/{res}")
        for line in txt.splitlines():
            if line.startswith("some"):
                for kv in line.split()[1:]:
                    k, _, v = kv.partition("=")
                    if k == "avg60":
                        out[res] = float(v)
    return out


def uptime_days():
    txt = _read(f"{PROC}/uptime").split()
    return float(txt[0]) / 86400 if txt else None


# --------------------------------------------------------------------------
# Proses paling boros CPU
# --------------------------------------------------------------------------

def _proc_cpu_times() -> dict:
    out = {}
    for pid in os.listdir(PROC):
        if not pid.isdigit():
            continue
        stat = _read(f"{PROC}/{pid}/stat")
        if not stat:
            continue
        try:
            rparen = stat.rindex(")")
            name = stat[stat.index("(") + 1:rparen]
            f = stat[rparen + 2:].split()
            out[pid] = {"name": name, "jiffies": int(f[11]) + int(f[12]),
                        "rss_mb": int(f[21]) * os.sysconf("SC_PAGE_SIZE") / 1048576}
        except (ValueError, IndexError):
            continue
    return out


def top_processes(interval=3.0, n=8) -> list:
    """CPU% per proses, diukur dari selisih waktu CPU selama `interval` detik."""
    t0, s0 = time.time(), _proc_cpu_times()
    time.sleep(interval)
    t1, s1 = time.time(), _proc_cpu_times()
    span = t1 - t0

    rows = []
    for pid, cur in s1.items():
        prev = s0.get(pid)
        if not prev:
            continue
        pct = (cur["jiffies"] - prev["jiffies"]) / CLK_TCK / span * 100
        if pct > 0.5:
            rows.append({"pid": pid, "name": cur["name"], "cpu_pct": pct,
                         "rss_mb": cur["rss_mb"]})
    rows.sort(key=lambda r: -r["cpu_pct"])
    return rows[:n]


# --------------------------------------------------------------------------

def snapshot() -> dict:
    """Kondisi mesin sesaat (tanpa persentase CPU — itu perlu dua pembacaan)."""
    return {
        "cores": cpu_count(),
        "mem": meminfo(),
        "load": loadavg(),
        "pressure": pressure(),
        "uptime_days": uptime_days(),
        "hostname": _read(f"{PROC}/sys/kernel/hostname").strip() or None,
    }


def verdict(cpu_summary, snap) -> dict:
    """Simpulkan apakah MESIN yang jadi penghambat, bukan sekadar jumlah worker."""
    busy = (cpu_summary or {}).get("busy_avg")
    mem = snap.get("mem", {})
    load = snap.get("load", {})

    cpu_bound = busy is not None and busy >= CPU_SATURATED
    cpu_roomy = busy is not None and busy < CPU_ROOMY
    mem_tight = mem.get("used_pct", 0) >= MEM_TIGHT_PCT
    swapping = mem.get("swap_used_mb", 0) > 64
    overloaded = load.get("per_core_1m", 0) >= 1.0
    stealing = (cpu_summary or {}).get("steal_avg", 0) >= 5.0

    if cpu_bound:
        reason = (f"CPU rata-rata {busy:.0f}% — mesin sudah jenuh, menambah worker "
                  f"tidak akan menaikkan throughput.")
    elif mem_tight or swapping:
        reason = (f"RAM terpakai {mem.get('used_pct', 0):.0f}%"
                  + (f" dan swap terpakai {mem.get('swap_used_mb', 0):.0f} MB" if swapping else "")
                  + " — memori jadi penghambat sebelum CPU.")
    elif cpu_roomy:
        reason = (f"CPU rata-rata hanya {busy:.0f}% — masih ada ruang, "
                  f"menambah worker seharusnya menaikkan throughput.")
    else:
        reason = (f"CPU rata-rata {busy:.0f}% — mendekati batas; tambah worker "
                  f"sedikit demi sedikit sambil dipantau.")

    return {
        "cpu_bound": cpu_bound,
        "cpu_roomy": cpu_roomy,
        "mem_tight": mem_tight,
        "swapping": swapping,
        "overloaded": overloaded,
        "stealing": stealing,
        "reason": reason,
    }


if __name__ == "__main__":
    if not available():
        raise SystemExit("Metrik host tidak tersedia — script ini harus dijalankan "
                         "di VM Linux yang menjalankan worker.")
    snap = snapshot()
    print(f"host      : {snap['hostname']}  ({snap['cores']} core, "
          f"uptime {snap['uptime_days']:.1f} hari)")
    m, ld = snap["mem"], snap["load"]
    print(f"RAM       : {m['used_mb']:.0f} / {m['total_mb']:.0f} MB "
          f"({m['used_pct']:.0f}% terpakai), swap {m['swap_used_mb']:.0f} MB")
    print(f"load avg  : {ld.get('1m')} {ld.get('5m')} {ld.get('15m')} "
          f"(per core 1m: {ld.get('per_core_1m', 0):.2f})")
    if snap["pressure"]:
        print(f"PSI avg60 : {snap['pressure']}")

    print("\nMengukur CPU 5 detik…")
    s = CpuSampler()
    for _ in range(5):
        time.sleep(1)
        d = s.tick()
        print(f"  busy={d['busy']:5.1f}%  user={d['user']:5.1f}%  sys={d['system']:5.1f}%  "
              f"iowait={d['iowait']:4.1f}%  steal={d['steal']:4.1f}%  "
              f"core>90%: {d['cores_over_90']}/{snap['cores']}")
    summ = s.summary()
    print(f"\nrata-rata busy: {summ['busy_avg']:.1f}%  (maks {summ['busy_max']:.1f}%)")
    print(verdict(summ, snap)["reason"])

    print("\nProses paling boros CPU:")
    for p in top_processes(3.0):
        print(f"  {p['cpu_pct']:6.1f}%  {p['rss_mb']:8.0f} MB  "
              f"pid={p['pid']:>7}  {p['name']}")
