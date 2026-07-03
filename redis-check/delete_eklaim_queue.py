import zlib
from config import redis_conn
from constant import EKLAIM_BATCH_AGENT, TARGET_ORG
from rq import Queue

DRY_RUN = False  # Set True to only list without deleting

queue_name = EKLAIM_BATCH_AGENT
queue = Queue(queue_name, connection=redis_conn)

print(f"Target org ID: {TARGET_ORG}")
print(f"Mode: {'DRY RUN (no delete)' if DRY_RUN else 'LIVE (will delete)'}")
print(f"Total job di queue: {len(queue.jobs)}\n")

to_delete = []
for i, job in enumerate(queue.jobs, start=1):
    match = False
    try:
        kwargs = job.kwargs
        if kwargs.get("managing_organization_id") == TARGET_ORG:
            match = True
    except Exception:
        raw_data = redis_conn.hget(f"rq:job:{job.id}", "data")
        if raw_data and TARGET_ORG.encode() in raw_data:
            match = True

    if match:
        to_delete.append(job)
        enc_id = "(unknown)"
        try:
            enc_id = job.kwargs.get("encounter_id", "(unknown)")
        except Exception:
            pass
        print(f"  [{i}] {job.id}  encounter: {enc_id}")

if not to_delete:
    print(f"Tidak ada job dengan org ID '{TARGET_ORG}'.")
    exit(0)

print(f"\nAkan dihapus: {len(to_delete)} job")

if not DRY_RUN:
    for job in to_delete:
        job.delete()
    print(f"Berhasil menghapus {len(to_delete)} job dari queue '{queue_name}'.")
else:
    print("Dry run: tidak ada yang dihapus.")
