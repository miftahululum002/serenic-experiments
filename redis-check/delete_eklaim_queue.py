import zlib
from redis import Redis
from rq import Queue

TARGET_ORG = "d2a967c2-f848-46b9-8d02-bd94680d6bf3"
DRY_RUN = False  # Set True to only list without deleting

redis_conn = Redis(host="localhost", port=6379)
queue_name = "eklaim_batch_agent_prod"
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
