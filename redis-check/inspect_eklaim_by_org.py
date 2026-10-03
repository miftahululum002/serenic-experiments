"""Inspeksi task eKlaim berdasarkan TARGET_ORG dari .env.

Script ini read-only: tidak menghapus, memindahkan, atau mengubah data Redis.
"""

from config import redis_conn
from constant import EKLAIM_BATCH_AGENT, TARGET_ORG
from rq import Queue


def decode(value):
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def job_matches_org(job, org_id: str) -> bool:
    """Cocokkan organization ID dari kwargs, dengan fallback raw payload."""
    try:
        return job.kwargs.get("managing_organization_id") == org_id
    except Exception:
        raw = redis_conn.hget(f"rq:job:{job.id}", "data") or b""
        return org_id.encode() in raw


def print_job(job, source: str, org_id: str, seen: set[str]) -> bool:
    if not job or job.id in seen or not job_matches_org(job, org_id):
        return False

    seen.add(job.id)
    print(f"  [{source}] {job.id}")

    try:
        kwargs = job.kwargs
        print(f"      status     : {job.get_status()}")
        print(f"      fungsi     : {job.func_name}")
        print(f"      encounter  : {kwargs.get('encounter_id', '-')}")
        print(f"      org        : {kwargs.get('managing_organization_id', '-')}")
        print(f"      admission  : {kwargs.get('admission_type', '-')}")
        print(f"      enqueued   : {job.enqueued_at}")
        print(f"      started    : {job.started_at}")
    except Exception as exc:
        print(f"      payload    : tidak dapat didecode ({exc})")

    return True


def print_daemon_status(org_id: str):
    print("=== INTERNAL EKLAIM DAEMON ===")
    found = 0

    for key in sorted(redis_conn.scan_iter(match="eklaim:daemon:*")):
        data = redis_conn.hgetall(key)
        if decode(data.get(b"batch_org", b"")) != org_id:
            continue

        found += 1
        print(f"  daemon       : {key.decode(errors='replace')}")
        print(f"  queue        : {decode(data.get(b'queue', b'-'))}")
        print(f"  state        : {decode(data.get(b'state', b'-'))}")
        print(f"  pending      : {decode(data.get(b'pending', b'0'))}")
        print(f"  pending_orgs : {decode(data.get(b'pending_orgs', b'0'))}")
        print(f"  batch_org    : {decode(data.get(b'batch_org', b'-'))}")
        print(f"  batches_total: {decode(data.get(b'batches_total', b'0'))}")
        print(f"  succeeded    : {decode(data.get(b'succeeded_total', b'0'))}")
        print(f"  failed       : {decode(data.get(b'failed_total', b'0'))}")

    if not found:
        print("  Tidak ditemukan metadata daemon aktif untuk TARGET_ORG.")


def inspect_rq_jobs(org_id: str):
    queue = Queue(EKLAIM_BATCH_AGENT, connection=redis_conn)
    seen = set()
    counts = {"pending": 0, "started": 0, "failed": 0, "finished": 0, "deferred": 0, "scheduled": 0, "workers": 0}

    print(f"\n=== RQ QUEUE: {EKLAIM_BATCH_AGENT} ===")
    print(f"  total pending queue: {len(queue.jobs)}")

    for job in queue.jobs:
        if print_job(job, "pending", org_id, seen):
            counts["pending"] += 1

    registries = [
        ("started", queue.started_job_registry),
        ("failed", queue.failed_job_registry),
        ("finished", queue.finished_job_registry),
        ("deferred", queue.deferred_job_registry),
        ("scheduled", queue.scheduled_job_registry),
    ]
    for name, registry in registries:
        for job_id in registry.get_job_ids():
            job = queue.fetch_job(job_id)
            if print_job(job, name, org_id, seen):
                counts[name] += 1

    print("\n=== WORKER YANG SEDANG BEKERJA ===")
    for key in redis_conn.scan_iter(match="rq:worker:*"):
        data = redis_conn.hgetall(key)
        worker_queues = decode(data.get(b"queues", b""))
        current_id = data.get(b"current_job") or data.get(b"current_job_id")
        if not current_id:
            continue

        job = queue.fetch_job(decode(current_id))
        if print_job(job, f"worker:{key.decode(errors='replace')[-12:]}", org_id, seen):
            counts["workers"] += 1
        elif "eklaim" in worker_queues.lower():
            print(f"  [worker] {key.decode(errors='replace')} current_job={decode(current_id)}")

    print("\n=== BRUTE-FORCE JOB PAYLOAD ===")
    # Berguna jika job sudah tidak tercantum di queue/registry tetapi payload masih ada.
    scanned = 0
    for key in redis_conn.scan_iter(match="rq:job:*"):
        scanned += 1
        job_id = key.decode(errors="replace").removeprefix("rq:job:")
        if job_id in seen:
            continue
        raw = redis_conn.hget(key, "data") or b""
        if org_id.encode() not in raw:
            continue

        job = queue.fetch_job(job_id)
        if print_job(job, "payload-scan", org_id, seen):
            counts["finished"] += 1

    print(f"  scanned rq:job:* : {scanned}")
    print("\n=== RINGKASAN JOB RQ YANG COCOK ===")
    print(f"  total unik ditemukan: {len(seen)}")
    for name, count in counts.items():
        if count:
            print(f"  {name}: {count}")


if __name__ == "__main__":
    org_id = TARGET_ORG.strip()
    if not org_id or org_id == "your_oragnization_id":
        raise SystemExit("TARGET_ORG belum diisi dengan organization ID yang valid di file .env")

    print(f"TARGET_ORG: {org_id}")
    print_daemon_status(org_id)
    inspect_rq_jobs(org_id)
