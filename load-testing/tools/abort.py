#!/usr/bin/env python3
"""Tombol darurat: lihat isi antrean, dan buang job test yang belum jalan.

Cara menghentikan test, berurutan dari yang paling aman:

  1. Ctrl-C pada driver test    -> kedatangan baru berhenti, antrean mengosong sendiri.
     Ini hampir selalu cukup.
  2. Tool ini dengan --job-ids  -> buang job test yang MASIH MENGANTRE.
  3. Restart container worker   -> membatalkan job yang sedang jalan. Job yang
     terbunuh di tengah jalan bisa meninggalkan encounter berstatus PROCESSING.

Job yang SUDAH jalan tidak bisa dibatalkan dari sini.

PENTING: di produksi, antrean berisi campuran job test dan job asli. Tool ini
tidak bisa membedakannya dari Redis saja — argumen job disimpan dalam bentuk
pickle yang butuh kode aplikasi untuk dibaca. Karena itu penghapusan HANYA
menerima daftar job id eksplisit yang dicatat oleh test (file
`results/*_jobids.txt`), dan default-nya dry-run.

    python tools/abort.py                                  # lihat keadaan
    python tools/abort.py --job-ids results/t3_..._jobids.txt          # dry-run
    python tools/abort.py --job-ids results/t3_..._jobids.txt --apply  # eksekusi
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from lib.csvlog import fmt_dur  # noqa: E402
from lib.rq_probe import RQProbe  # noqa: E402


def show_state(probe: RQProbe) -> None:
    s = probe.snapshot()
    print(f"Antrean : {settings.parsing_queue}")
    print(f"  menunggu {s.depth} | jalan {s.started} | selesai {s.finished} | gagal {s.failed}")
    print(f"  worker sibuk {s.workers_busy}/{s.workers_total} (WORKER_REPLICAS={settings.worker_replicas})")
    if s.oldest_wait_seconds:
        print(f"  job terdepan sudah menunggu {fmt_dur(s.oldest_wait_seconds)}")

    busy = [w for w in probe.workers() if w["state"] == "busy"]
    if busy:
        print("\nJob yang SEDANG jalan (tidak bisa dibuang dari sini):")
        for w in busy:
            j = probe.job(w["current_job"]) if w["current_job"] else None
            age = ""
            if j and j.started_at:
                from lib.rq_probe import now_utc
                age = f"  berjalan {fmt_dur((now_utc() - j.started_at).total_seconds())}"
            print(f"  {w['name'][:40]:<40} job={(w['current_job'] or '-')[:12]}{age}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-ids", help="file berisi satu job id per baris (dari hasil test)")
    ap.add_argument("--apply", action="store_true", help="benar-benar hapus (default: dry-run)")
    args = ap.parse_args()

    settings.require_redis()
    probe = RQProbe(settings.redis_url, settings.parsing_queue)
    show_state(probe)

    if not args.job_ids:
        print("\nTidak ada --job-ids, jadi tidak ada yang dihapus.")
        print("Untuk menghentikan test: Ctrl-C driver-nya, antrean akan mengosong sendiri.")
        return

    path = Path(args.job_ids)
    if not path.exists():
        sys.exit(f"Tidak ditemukan: {path}")
    wanted = {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}

    queued = set(probe.queued_ids())
    running = {w["current_job"] for w in probe.workers() if w["current_job"]}

    removable = sorted(wanted & queued)
    already_running = sorted(wanted & running)
    gone = len(wanted) - len(removable) - len(already_running)

    print(f"\nDari {len(wanted)} job id test:")
    print(f"  masih mengantre (bisa dibuang) : {len(removable)}")
    print(f"  sedang jalan (tidak bisa)      : {len(already_running)}")
    print(f"  sudah selesai / tidak ada      : {gone}")

    if not removable:
        print("\nTidak ada yang bisa dibuang.")
        return

    if not args.apply:
        print(f"\nDRY-RUN. Tambahkan --apply untuk benar-benar membuang {len(removable)} job.")
        return

    key = f"rq:queue:{settings.parsing_queue}"
    removed = 0
    for jid in removable:
        removed += int(probe.r.lrem(key, 0, jid))
    print(f"\nDibuang {removed} job dari antrean.")
    print("Encounter yang bersangkutan tetap ada di DB — bersihkan lewat query "
          f"dengan filter prefix '{settings.synthetic_prefix}-'.")


if __name__ == "__main__":
    main()
