from config import redis_conn
from constant import EKLAIM_BATCH_AGENT, TARGET_ORG
from rq import Queue
import sys


# Default aman: hanya tampilkan job yang cocok.
# Gunakan `--confirm` untuk benar-benar menghapus.
DRY_RUN = "--confirm" not in sys.argv


def find_active_daemons(org_id: str) -> list[dict]:
    """Cari daemon eKlaim yang sedang memproses organization ID target."""
    active = []
    for key in redis_conn.scan_iter(match="eklaim:daemon:*"):
        data = redis_conn.hgetall(key)
        if data.get(b"batch_org", b"").decode() != org_id:
            continue

        active.append(
            {
                "key": key.decode(),
                "queue": data.get(b"queue", b"-").decode(),
                "state": data.get(b"state", b"-").decode(),
                "pending": data.get(b"pending", b"0").decode(),
                "failed": data.get(b"failed_total", b"0").decode(),
            }
        )
    return active


def find_jobs_by_org(queue_name: str, org_id: str) -> list:
    queue = Queue(queue_name, connection=redis_conn)
    to_delete = []
    for i, job in enumerate(queue.jobs, start=1):
        match = False
        try:
            kwargs = job.kwargs
            if kwargs.get("managing_organization_id") == org_id:
                match = True
        except Exception:
            raw_data = redis_conn.hget(f"rq:job:{job.id}", "data")
            if raw_data and org_id.encode() in raw_data:
                match = True

        if match:
            to_delete.append(job)
            enc_id = "(unknown)"
            try:
                enc_id = job.kwargs.get("encounter_id", "(unknown)")
            except Exception:
                pass
            print(f"  [{i}] {job.id}  encounter: {enc_id}")
    return to_delete


def delete_jobs(to_delete: list, queue_name: str):
    queue = Queue(queue_name, connection=redis_conn)
    for job in to_delete:
        # Hapus ID dari list queue dan payload job dari Redis.
        queue.remove(job)
        job.delete()
    print(f"Berhasil menghapus {len(to_delete)} job dari queue '{queue_name}'.")


if __name__ == "__main__":
    org_id = TARGET_ORG.strip()
    queue_name = EKLAIM_BATCH_AGENT
    queue = Queue(queue_name, connection=redis_conn)

    if not org_id or org_id == "your_oragnization_id":
        raise SystemExit("TARGET_ORG belum diisi dengan organization ID yang valid di file .env")

    print(f"Target org ID: {org_id}")
    print(f"Mode: {'DRY RUN (no delete)' if DRY_RUN else 'LIVE (will delete)'}")
    print(f"Total job di queue: {len(queue.jobs)}\n")

    active_daemons = find_active_daemons(org_id)
    if active_daemons:
        print("Daemon eKlaim yang sedang memproses target:")
        for daemon in active_daemons:
            print(
                f"  {daemon['key']} | state={daemon['state']} | "
                f"pending_internal={daemon['pending']} | failed_total={daemon['failed']}"
            )
        print()

        if not DRY_RUN:
            raise SystemExit(
                "Penghapusan dibatalkan: TARGET_ORG masih diproses daemon eKlaim. "
                "Tunggu sampai daemon idle, lalu jalankan ulang."
            )

    to_delete = find_jobs_by_org(queue_name, org_id)

    if not to_delete:
        print(f"Tidak ada job dengan org ID '{org_id}'.")
    elif DRY_RUN:
        print("\nDry run: tidak ada yang dihapus.")
        print("Jalankan ulang dengan `--confirm` untuk menghapus job yang terdaftar di atas.")
    else:
        delete_jobs(to_delete, queue_name)
