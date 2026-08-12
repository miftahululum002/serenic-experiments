"""Bagian yang dipakai bersama semua skenario test."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from lib.payload import Cohort, Template  # noqa: E402
from lib.rq_probe import RQProbe  # noqa: E402


class CohortCursor:
    """Membagikan potongan encounter yang saling lepas.

    Ingestor memasang advisory lock per encounter, jadi dua request yang memuat
    encounter yang sama akan saling menunggu di worker. Untuk pengukuran
    konkurensi, setiap request wajib dapat encounter yang berbeda.
    """

    def __init__(self, cohort: Cohort, allow_wrap: bool = True):
        self.cohort = cohort
        self.pos = 0
        self.allow_wrap = allow_wrap
        self.wrapped = False

    def take(self, n: int) -> list[dict]:
        if n > len(self.cohort):
            sys.exit(f"Butuh {n} encounter dalam satu request tapi kohort cuma {len(self.cohort)}. "
                     f"Seed ulang: python tools/seed_encounters.py --count {n * 2}")
        if self.pos + n > len(self.cohort):
            if not self.allow_wrap:
                sys.exit(f"Kohort habis ({len(self.cohort)} encounter). Seed lebih banyak.")
            self.pos = 0
            self.wrapped = True
        out = self.cohort.slice(self.pos, n)
        self.pos += n
        return out


def load_context(require_redis: bool = True):
    """Validasi konfigurasi lalu kembalikan (template, cohort, probe)."""
    settings.require_api()
    if require_redis:
        settings.require_redis()

    if not settings.template_payload.exists():
        sys.exit(f"Template payload tidak ditemukan: {settings.template_payload}\n"
                 "Isi TEMPLATE_PAYLOAD di .env dengan satu payload /encounters/update yang asli.")
    template = Template(settings.template_payload)

    cohort_path = settings.results_dir / "cohort.json"
    if not cohort_path.exists():
        sys.exit(f"Kohort belum ada: {cohort_path}\n"
                 "Jalankan dulu: python tools/seed_encounters.py --count 600")
    cohort = Cohort.load(cohort_path)

    probe = RQProbe(settings.redis_url, settings.parsing_queue) if require_redis else None
    return template, cohort, probe


def measure_baseline(probe: RQProbe, seconds: float = 60.0, interval: float = 2.0) -> dict:
    """Ukur beban latar sebelum menambahkan beban test.

    Wajib di produksi: antrean tidak pernah kosong dan worker sudah terpakai
    sebagian. Tanpa angka ini, lambda_max yang terukur tidak bisa ditafsirkan —
    kapasitas total sistem = beban latar + beban test yang masih tertampung.
    """
    import time

    from lib.csvlog import linreg

    print(f"Mengukur beban latar selama {seconds:.0f}s (jangan kirim beban test dulu)...")
    samples = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        s = probe.snapshot()
        samples.append(s)
        time.sleep(interval)

    if len(samples) < 2:
        return {}

    mins = (samples[-1].ts - samples[0].ts) / 60.0
    xs = [(s.ts - samples[0].ts) / 60.0 for s in samples]
    slope, _, _ = linreg(xs, [float(s.depth) for s in samples])
    out = {
        "window_s": round(samples[-1].ts - samples[0].ts, 1),
        "background_jobs_per_min": (samples[-1].finished - samples[0].finished) / mins if mins else 0.0,
        "avg_depth": sum(s.depth for s in samples) / len(samples),
        "depth_slope_per_min": slope,
        "avg_busy": sum(s.workers_busy for s in samples) / len(samples),
        "replicas": settings.worker_replicas,
        "failed_delta": samples[-1].failed - samples[0].failed,
    }

    print(f"  Job selesai        : {out['background_jobs_per_min']:.2f} job/menit (beban asli)")
    print(f"  Antrean rata-rata  : {out['avg_depth']:.1f} job, kemiringan {out['depth_slope_per_min']:+.2f}/menit")
    print(f"  Worker sibuk       : {out['avg_busy']:.1f} dari {out['replicas']}")
    if out["failed_delta"]:
        print(f"  PERINGATAN: {out['failed_delta']} job GAGAL selama baseline — ada masalah "
              "yang sudah ada sebelum test dimulai.")

    headroom = out["replicas"] - out["avg_busy"]
    if out["depth_slope_per_min"] > 0.5:
        print("\n  HENTIKAN: antrean sudah menumpuk SEBELUM test dimulai. Sistem sedang "
              "kewalahan oleh beban asli.\n  Menambah beban test sekarang akan memperparah "
              "keterlambatan pasien nyata. Tunggu jam yang lebih sepi.")
    elif headroom < 1.0:
        print(f"\n  PERINGATAN: cuma {headroom:.1f} replika menganggur. Ruang untuk beban test "
              "sangat tipis;\n  beban test akan langsung mengantre di belakang pekerjaan asli.")
    else:
        print(f"\n  Ada ~{headroom:.1f} replika menganggur — cukup untuk menambah beban test.")
    print()
    return out


def preflight(probe: RQProbe, require_idle: bool = True) -> None:
    """Pastikan lingkungan bersih sebelum mengukur."""
    workers = probe.workers()
    expected = settings.worker_replicas
    if not workers:
        print("PERINGATAN: tidak ada worker RQ terdaftar untuk antrean ini. "
              "Pastikan container emr-integration-data-parsing-worker_PROD jalan.")
    else:
        busy = sum(1 for w in workers if w["state"] == "busy")
        print(f"Worker terdaftar : {len(workers)} ({busy} sibuk), diharapkan {expected}")
        if len(workers) != expected:
            print(f"  PERINGATAN: jumlah worker ({len(workers)}) tidak cocok dengan WORKER_REPLICAS "
                  f"({expected}). Semua hitungan kapasitas armada akan salah.\n"
                  f"  Cek dengan: docker compose -f docker-compose.app.prod.yml ps "
                  f"emr-integration-data-parsing-worker_PROD")

    s = probe.snapshot()
    print(f"Antrean awal     : {s.depth} menunggu, {s.started} jalan, {s.failed} gagal")

    host = settings.api_base_url.split("//")[-1].split("/")[0]
    is_prod = host.startswith("api.serenic.ai") or settings.allow_prod

    if s.depth > 0 or s.started > 0:
        if is_prod:
            print("  Antrean tidak kosong — normal di produksi. Beban latar akan diukur "
                  "sebagai baseline\n  dan hasil test ditafsirkan relatif terhadapnya.")
        elif require_idle:
            print("\nAntrean tidak kosong. Ukuran waktu akan tercampur beban lain.")
            if input("Lanjut? [y/N] ").strip().lower() != "y":
                sys.exit("Dibatalkan.")


def check_isolation(probe: RQProbe, before_downstream: int) -> None:
    """Cek apakah pekerjaan menjalar ke worker analisis."""
    if not settings.analysis_coordinator_queue:
        return
    after = int(probe.r.llen(f"rq:queue:{settings.analysis_coordinator_queue}"))
    if after > before_downstream:
        print(
            f"\nCATATAN: antrean analisis bertambah {after - before_downstream} job selama test.\n"
            "         Yang kamu ukur adalah pipeline penuh, bukan worker parsing saja.\n"
            "         Untuk mengisolasi worker parsing, set IS_CODEX_API_V2_PROCESS=false\n"
            "         di prod.env lingkungan test lalu restart api-server."
        )


def downstream_depth(probe: RQProbe) -> int:
    if not settings.analysis_coordinator_queue:
        return 0
    return int(probe.r.llen(f"rq:queue:{settings.analysis_coordinator_queue}"))


def run_paths(name: str) -> tuple[Path, Path, Path]:
    """(csv hasil, csv sampler, json ringkasan) dengan stempel waktu."""
    d = settings.ensure_results_dir()
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    return d / f"{name}_{stamp}.csv", d / f"{name}_{stamp}_queue.csv", d / f"{name}_{stamp}_summary.json"


_LINE_WIDTH = 0


def status_line(prefix: str, msg: str, elapsed: float) -> None:
    """Status hidup di baris yang sama.

    Tanpa ini, layar diam total selama job berjalan — tidak bisa dibedakan dari
    proses yang hang, padahal worker sedang bekerja normal.
    """
    global _LINE_WIDTH
    from lib.csvlog import fmt_dur

    line = f"{prefix}  {msg}  [{fmt_dur(elapsed)}]"
    _LINE_WIDTH = max(_LINE_WIDTH, len(line))
    print(f"\r{line:<{_LINE_WIDTH}}", end="", flush=True)


def clear_line() -> None:
    global _LINE_WIDTH
    if _LINE_WIDTH:
        print(f"\r{' ' * _LINE_WIDTH}\r", end="", flush=True)
        _LINE_WIDTH = 0


class JobIdRecorder:
    """Catat job id yang muncul selama test, untuk tombol darurat tools/abort.py.

    Di produksi diff ini bisa ikut menangkap job asli yang kebetulan masuk pada
    saat bersamaan — karena itu abort.py selalu dry-run lebih dulu dan operator
    yang memutuskan.
    """

    def __init__(self, probe: RQProbe, path: Path):
        self.probe = probe
        self.path = path
        self.baseline_ids = probe.known_ids()
        self.seen: set[str] = set()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def capture(self) -> int:
        new = self.probe.known_ids() - self.baseline_ids - self.seen
        if new:
            self.seen |= new
            with self.path.open("a") as f:
                f.writelines(f"{j}\n" for j in sorted(new))
        return len(self.seen)


def save_summary(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str))
    print(f"\nRingkasan: {path}")


def banner(title: str, template: Template, extra: dict) -> None:
    settings.warn_if_prod()
    print("=" * 78)
    print(title)
    print("=" * 78)
    print(f"API       : {settings.api_base_url}")
    if settings.org_label:
        print(f"Org       : {settings.org_label}")
    print(f"Antrean   : {settings.parsing_queue}")
    print(f"Template  : {settings.template_payload.name} "
          f"({template.bytes_per_encounter / 1024:.0f} KB/encounter)")
    print(f"Sources   : {template.source_summary}")
    for k, v in extra.items():
        print(f"{k:<10}: {v}")
    print()
