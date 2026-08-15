"""Metrik CPU & RAM mesin — bisa dibaca lokal (di VM) atau jarak jauh (SSH).

Sengaja memakai pustaka standar saja (baca `/proc` langsung), supaya bisa
langsung jalan di VM tanpa perlu meng-install apa pun.

Ada dua sumber data:

* **lokal** — dipakai otomatis bila script dijalankan DI VM Linux itu sendiri.
* **SSH**  — dipakai bila `--ssh <target>` / `HOST_SSH` diisi, sehingga CPU/RAM
  VM tetap terukur walau `report.py` dijalankan dari laptop. Semua pembacaan
  satu putaran digabung jadi **satu** perintah `cat` supaya tidak boros koneksi.

Kalau dua-duanya tidak tersedia (mis. laptop macOS tanpa `--ssh`),
`available()` mengembalikan False dan bab CPU/RAM otomatis dilewati.

Pemakaian:
    import host
    host.use_ssh("serenic-prod.asia-southeast2-a.serenic-aurio-mvp")  # opsional
    s = host.HostSampler()
    ...                       # biarkan beban berjalan
    hasil = s.tick()          # persentase CPU + RAM sejak tick sebelumnya
"""

import os
import shlex
import subprocess
import time

PROC = "/proc"
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

# Ambang penilaian (persen CPU rata-rata selama pengamatan)
CPU_SATURATED = 85.0    # di atas ini: mesin jadi penghambat
CPU_ROOMY = 70.0        # di bawah ini: masih ada ruang untuk menambah worker
MEM_TIGHT_PCT = 90.0    # pemakaian RAM di atas ini dianggap sesak

MARK = "@@F:"           # penanda pemisah antar file pada pembacaan borongan

# ControlMaster: koneksi SSH dipakai ulang antar tick, jadi tiap sampling
# hanya ±50 ms alih-alih membangun sesi SSH baru tiap 3 detik.
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=/tmp/.cek-load-ssh-%r@%h:%p",
    "-o", "ControlPersist=180",
]


# --------------------------------------------------------------------------
# Sumber data: lokal atau SSH
# --------------------------------------------------------------------------

class LocalSource:
    """Baca `/proc` mesin ini sendiri."""

    remote = False
    label = "mesin ini (lokal)"

    def ok(self) -> bool:
        return os.path.isdir(PROC) and os.path.exists(f"{PROC}/stat")

    def read(self, path) -> str:
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return ""

    def read_many(self, paths) -> dict:
        return {p: self.read(p) for p in paths}

    def sh(self, script, timeout=60) -> str:
        r = subprocess.run(["/bin/sh", "-c", script], capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout


class SshSource:
    """Baca `/proc` VM lain lewat SSH (atau perintah pembungkus lain)."""

    remote = True

    def __init__(self, target=None, command=None, timeout=30):
        if command:
            # Bebas: "gcloud compute ssh vm --zone z --tunnel-through-iap --command"
            self.argv = shlex.split(command)
            self.label = command.split()[-2] if len(command.split()) > 1 else command
        elif target:
            self.argv = ["ssh", *SSH_OPTS, target]
            self.label = target
        else:
            raise ValueError("SshSource butuh target atau command")
        self.timeout = timeout
        self._cache = {}

    # -- eksekusi -----------------------------------------------------------

    def sh(self, script, timeout=None) -> str:
        r = subprocess.run(self.argv + [script], capture_output=True, text=True,
                           timeout=timeout or self.timeout)
        if r.returncode != 0:
            raise RuntimeError(
                f"perintah remote gagal ({' '.join(self.argv[:2])}…): "
                f"{(r.stderr or '').strip()[:300]}")
        return r.stdout

    def ok(self) -> bool:
        try:
            return "cpu " in self.sh(f"cat {PROC}/stat", timeout=20)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return False

    # -- pembacaan file -----------------------------------------------------

    def read(self, path) -> str:
        if path in self._cache:
            return self._cache[path]
        return self.read_many([path]).get(path, "")

    def read_many(self, paths) -> dict:
        """Satu koneksi SSH untuk banyak file sekaligus."""
        paths = list(paths)
        script = "; ".join(
            f'echo "{MARK}{p}"; cat {p} 2>/dev/null' for p in paths)
        out = {}
        try:
            text = self.sh(script)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return {p: "" for p in paths}
        cur = None
        for line in text.splitlines():
            if line.startswith(MARK):
                cur = line[len(MARK):].strip()
                out[cur] = []
            elif cur is not None:
                out[cur].append(line)
        return {p: "\n".join(out.get(p, [])) for p in paths}

    # -- cache satu putaran -------------------------------------------------

    def prefetch(self, paths):
        self._cache = self.read_many(paths)

    def clear(self):
        self._cache = {}


SOURCE = LocalSource()


def use_ssh(target=None, command=None):
    """Alihkan seluruh pembacaan `/proc` ke VM lain lewat SSH."""
    global SOURCE
    SOURCE = SshSource(target=target, command=command)
    return SOURCE


def use_local():
    global SOURCE
    SOURCE = LocalSource()
    return SOURCE


def source_label() -> str:
    return getattr(SOURCE, "label", "mesin ini (lokal)")


def is_remote() -> bool:
    return getattr(SOURCE, "remote", False)


def available() -> bool:
    return SOURCE.ok()


def _read(path) -> str:
    return SOURCE.read(path)


def _prefetch(paths):
    if hasattr(SOURCE, "prefetch"):
        SOURCE.prefetch(paths)


def _clear():
    if hasattr(SOURCE, "clear"):
        SOURCE.clear()


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------

FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


def _parse_cpu_times(text) -> dict:
    out = {}
    for line in text.splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        out[parts[0]] = dict(zip(FIELDS, (int(v) for v in parts[1:9])))
    return out


def _cpu_times() -> dict:
    """Baca akumulasi waktu CPU dari /proc/stat (satuan jiffies)."""
    return _parse_cpu_times(_read(f"{PROC}/stat"))


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


class HostSampler:
    """Hitung persentase CPU + pemakaian RAM antar pemanggilan `tick()`.

    Tiap tick hanya perlu `/proc/stat` dan `/proc/meminfo`; keduanya diambil
    dalam satu perintah supaya mode SSH tetap satu koneksi per tick.
    """

    PATHS = (f"{PROC}/stat", f"{PROC}/meminfo")

    def __init__(self):
        self._prev = _cpu_times()
        self._prev_t = time.time()
        self.samples = []       # riwayat persentase CPU agregat
        self.mem_samples = []   # riwayat pemakaian RAM

    def tick(self) -> dict:
        raw = SOURCE.read_many(self.PATHS)
        cur = _parse_cpu_times(raw.get(f"{PROC}/stat", ""))
        mem = _parse_meminfo(raw.get(f"{PROC}/meminfo", ""))
        if mem.get("total_mb"):
            self.mem_samples.append(mem)

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
            agg["mem"] = mem
            self.samples.append(agg)
        return agg

    def mem_summary(self) -> dict:
        """Ringkasan RAM selama pengamatan — bukan sekadar potret akhir."""
        if not self.mem_samples:
            return {}
        used = [m["used_mb"] for m in self.mem_samples]
        pct = [m["used_pct"] for m in self.mem_samples]
        avail = [m["available_mb"] for m in self.mem_samples]
        swap = [m["swap_used_mb"] for m in self.mem_samples]
        return {
            "n": len(used),
            "total_mb": self.mem_samples[-1]["total_mb"],
            "used_avg_mb": sum(used) / len(used),
            "used_min_mb": min(used),
            "used_max_mb": max(used),
            "used_pct_avg": sum(pct) / len(pct),
            "used_pct_max": max(pct),
            "available_min_mb": min(avail),
            "swap_max_mb": max(swap),
            "swap_growth_mb": swap[-1] - swap[0],
        }

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
            "mem": self.mem_summary(),
        }


CpuSampler = HostSampler      # nama lama, biar pemakaian lawas tetap jalan


# --------------------------------------------------------------------------
# Memori & beban
# --------------------------------------------------------------------------

def _parse_meminfo(text) -> dict:
    """RAM & swap dalam MB, plus persentase terpakai."""
    vals = {}
    for line in text.splitlines():
        k, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            try:
                vals[k] = int(parts[0]) / 1024      # kB -> MB
            except ValueError:
                continue
    if not vals:
        return {}

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
        "buffers_mb": vals.get("Buffers", 0),
        "swap_total_mb": sw_total,
        "swap_used_mb": sw_total - sw_free,
        "swap_used_pct": (sw_total - sw_free) / sw_total * 100 if sw_total else 0,
    }


def meminfo() -> dict:
    return _parse_meminfo(_read(f"{PROC}/meminfo"))


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
# Proses paling boros CPU & RAM
# --------------------------------------------------------------------------

def _parse_proc_stats(text, page_size=PAGE_SIZE) -> dict:
    """Ubah kumpulan baris /proc/<pid>/stat jadi {pid: {name, jiffies, rss_mb}}."""
    out = {}
    for line in text.splitlines():
        try:
            rparen = line.rindex(")")
            pid = line[:line.index("(")].strip()
            name = line[line.index("(") + 1:rparen]
            f = line[rparen + 2:].split()
            out[pid] = {"name": name, "jiffies": int(f[11]) + int(f[12]),
                        "rss_mb": int(f[21]) * page_size / 1048576}
        except (ValueError, IndexError):
            continue
    return out


def _proc_cpu_times() -> dict:
    out = {}
    for pid in os.listdir(PROC):
        if not pid.isdigit():
            continue
        stat = _read(f"{PROC}/{pid}/stat")
        if stat:
            out.update(_parse_proc_stats(stat))
    return out


# Dua potret /proc/<pid>/stat dalam satu sesi remote — kalau tiap PID dibaca
# terpisah lewat SSH, ratusan round-trip-nya jauh lebih lama dari intervalnya.
_TOP_SCRIPT = """
echo "{m}tck"; getconf CLK_TCK; echo "{m}page"; getconf PAGESIZE
echo "{m}t0"; date +%s.%N
echo "{m}s0"; cat /proc/[0-9]*/stat 2>/dev/null
sleep {interval}
echo "{m}t1"; date +%s.%N
echo "{m}s1"; cat /proc/[0-9]*/stat 2>/dev/null
"""


def _top_remote(interval, n) -> list:
    text = SOURCE.sh(_TOP_SCRIPT.format(m=MARK, interval=interval),
                     timeout=interval + 40)
    sec, cur = {}, None
    for line in text.splitlines():
        if line.startswith(MARK):
            cur = line[len(MARK):].strip()
            sec[cur] = []
        elif cur:
            sec[cur].append(line)

    def one(k, default):
        try:
            return float(sec.get(k, [""])[0])
        except (ValueError, IndexError):
            return default

    tck = one("tck", 100.0) or 100.0
    page = one("page", 4096.0) or 4096.0
    span = one("t1", 0.0) - one("t0", 0.0)
    if span <= 0:
        span = interval
    s0 = _parse_proc_stats("\n".join(sec.get("s0", [])), page)
    s1 = _parse_proc_stats("\n".join(sec.get("s1", [])), page)
    return _top_rows(s0, s1, span, tck, n)


def _top_rows(s0, s1, span, tck, n) -> list:
    rows = []
    for pid, cur in s1.items():
        prev = s0.get(pid)
        if not prev:
            continue
        pct = (cur["jiffies"] - prev["jiffies"]) / tck / span * 100
        if pct > 0.5:
            rows.append({"pid": pid, "name": cur["name"], "cpu_pct": pct,
                         "rss_mb": cur["rss_mb"]})
    rows.sort(key=lambda r: -r["cpu_pct"])
    return rows[:n]


def top_processes(interval=3.0, n=8) -> list:
    """CPU% per proses, diukur dari selisih waktu CPU selama `interval` detik."""
    if is_remote():
        try:
            return _top_remote(interval, n)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return []
    t0, s0 = time.time(), _proc_cpu_times()
    time.sleep(interval)
    t1, s1 = time.time(), _proc_cpu_times()
    return _top_rows(s0, s1, t1 - t0, CLK_TCK, n)


def top_memory(n=8) -> list:
    """Proses paling boros RAM (RSS), diambil dari potret tunggal /proc."""
    if is_remote():
        try:
            text = SOURCE.sh("getconf PAGESIZE; cat /proc/[0-9]*/stat 2>/dev/null",
                             timeout=40)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return []
        head, _, rest = text.partition("\n")
        try:
            page = float(head.strip())
        except ValueError:
            page, rest = PAGE_SIZE, text
        procs = _parse_proc_stats(rest, page)
    else:
        procs = _proc_cpu_times()
    rows = [{"pid": pid, "name": p["name"], "rss_mb": p["rss_mb"]}
            for pid, p in procs.items() if p["rss_mb"] >= 1]
    rows.sort(key=lambda r: -r["rss_mb"])
    return rows[:n]


# --------------------------------------------------------------------------

SNAPSHOT_PATHS = (
    f"{PROC}/stat", f"{PROC}/meminfo", f"{PROC}/loadavg", f"{PROC}/uptime",
    f"{PROC}/sys/kernel/hostname",
    f"{PROC}/pressure/cpu", f"{PROC}/pressure/memory", f"{PROC}/pressure/io",
)


def snapshot() -> dict:
    """Kondisi mesin sesaat (tanpa persentase CPU — itu perlu dua pembacaan)."""
    _prefetch(SNAPSHOT_PATHS)       # satu koneksi untuk seluruh isi snapshot
    try:
        return {
            "source": source_label(),
            "remote": is_remote(),
            "cores": cpu_count(),
            "mem": meminfo(),
            "load": loadavg(),
            "pressure": pressure(),
            "uptime_days": uptime_days(),
            "hostname": _read(f"{PROC}/sys/kernel/hostname").strip() or None,
        }
    finally:
        _clear()


def verdict(cpu_summary, snap) -> dict:
    """Simpulkan apakah MESIN yang jadi penghambat, bukan sekadar jumlah worker."""
    busy = (cpu_summary or {}).get("busy_avg")
    mem = snap.get("mem", {})
    load = snap.get("load", {})
    mem_hist = (cpu_summary or {}).get("mem") or {}

    # Pakai puncak RAM selama pengamatan, bukan hanya potret akhir: RAM bisa
    # sempat mepet lalu turun lagi setelah job selesai.
    used_pct = max(mem.get("used_pct", 0), mem_hist.get("used_pct_max", 0))
    swap_used = max(mem.get("swap_used_mb", 0), mem_hist.get("swap_max_mb", 0))

    cpu_bound = busy is not None and busy >= CPU_SATURATED
    cpu_roomy = busy is not None and busy < CPU_ROOMY
    mem_tight = used_pct >= MEM_TIGHT_PCT
    swapping = swap_used > 64
    overloaded = load.get("per_core_1m", 0) >= 1.0
    stealing = (cpu_summary or {}).get("steal_avg", 0) >= 5.0

    if cpu_bound:
        reason = (f"CPU rata-rata {busy:.0f}% — mesin sudah jenuh, menambah worker "
                  f"tidak akan menaikkan throughput.")
    elif mem_tight or swapping:
        reason = (f"RAM terpakai {used_pct:.0f}%"
                  + (f" dan swap terpakai {swap_used:.0f} MB" if swapping else "")
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
        "mem_used_pct": used_pct,
        "reason": reason,
    }


if __name__ == "__main__":
    import argparse

    from config import HOST_SSH, HOST_SSH_CMD

    p = argparse.ArgumentParser(description="Metrik CPU & RAM mesin worker")
    p.add_argument("--ssh", default=HOST_SSH,
                   help="target SSH VM worker (default: HOST_SSH di .env)")
    p.add_argument("--ssh-cmd", default=HOST_SSH_CMD,
                   help="perintah pembungkus khusus, mis. "
                        "'gcloud compute ssh vm --zone z --command'")
    p.add_argument("--seconds", type=float, default=5.0,
                   help="durasi sampling CPU/RAM")
    args = p.parse_args()

    if args.ssh or args.ssh_cmd:
        use_ssh(target=args.ssh or None, command=args.ssh_cmd or None)
        print(f"sumber    : SSH → {source_label()}")

    if not available():
        raise SystemExit(
            "Metrik host tidak tersedia. Jalankan di VM Linux yang menjalankan "
            "worker, atau pakai --ssh <target> untuk membacanya dari jauh.")

    snap = snapshot()
    print(f"host      : {snap['hostname']}  ({snap['cores']} core, "
          f"uptime {snap['uptime_days']:.1f} hari)")
    m, ld = snap["mem"], snap["load"]
    print(f"RAM       : {m['used_mb']:.0f} / {m['total_mb']:.0f} MB "
          f"({m['used_pct']:.0f}% terpakai), tersedia {m['available_mb']:.0f} MB, "
          f"cache {m['cached_mb']:.0f} MB, swap {m['swap_used_mb']:.0f} MB")
    print(f"load avg  : {ld.get('1m')} {ld.get('5m')} {ld.get('15m')} "
          f"(per core 1m: {ld.get('per_core_1m', 0):.2f})")
    if snap["pressure"]:
        print(f"PSI avg60 : {snap['pressure']}")

    print(f"\nMengukur CPU & RAM {args.seconds:.0f} detik…")
    s = HostSampler()
    for _ in range(int(args.seconds)):
        time.sleep(1)
        d = s.tick()
        if not d:
            continue
        mm = d.get("mem", {})
        print(f"  busy={d['busy']:5.1f}%  user={d['user']:5.1f}%  sys={d['system']:5.1f}%  "
              f"iowait={d['iowait']:4.1f}%  steal={d['steal']:4.1f}%  "
              f"core>90%: {d['cores_over_90']}/{snap['cores']}  "
              f"RAM={mm.get('used_mb', 0):.0f}MB ({mm.get('used_pct', 0):.0f}%)")
    summ = s.summary()
    mem_s = summ.get("mem", {})
    print(f"\nrata-rata busy: {summ['busy_avg']:.1f}%  (maks {summ['busy_max']:.1f}%)")
    if mem_s:
        print(f"RAM rata-rata : {mem_s['used_avg_mb']:.0f} MB "
              f"({mem_s['used_pct_avg']:.0f}%), puncak {mem_s['used_max_mb']:.0f} MB "
              f"({mem_s['used_pct_max']:.0f}%), sisa minimum "
              f"{mem_s['available_min_mb']:.0f} MB")
    print(verdict(summ, snap)["reason"])

    print("\nProses paling boros CPU:")
    for pr in top_processes(3.0):
        print(f"  {pr['cpu_pct']:6.1f}%  {pr['rss_mb']:8.0f} MB  "
              f"pid={pr['pid']:>7}  {pr['name']}")

    print("\nProses paling boros RAM:")
    for pr in top_memory():
        print(f"  {pr['rss_mb']:8.0f} MB  pid={pr['pid']:>7}  {pr['name']}")
