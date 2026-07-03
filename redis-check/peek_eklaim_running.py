import zlib
from redis import Redis
from rq import Queue

redis_conn = Redis(host="localhost", port=6379)
queue_name = "eklaim_batch_agent_prod"
queue = Queue(queue_name, connection=redis_conn)

registry = queue.started_job_registry
job_ids = registry.get_job_ids()
jobs = [queue.fetch_job(jid) for jid in job_ids if queue.fetch_job(jid)]

print(f"=== Job Sedang Diproses: {queue_name} ===")
print(f"Total: {len(jobs)}\n" + "-" * 60)

if not jobs:
    print("(Tidak ada job yang sedang diproses)")
else:
    for job in jobs:
        print(f"Job ID    : {job.id}")
        print(f"Fungsi    : {job.func_name}")
        print(f"Enqueued  : {job.enqueued_at}")
        print(f"Started   : {job.started_at}")
        try:
            kwargs = job.kwargs
            print(f"Encounter : {kwargs.get('encounter_id', '-')}")
            print(f"Org ID    : {kwargs.get('managing_organization_id', '-')}")
            print(f"Tipe      : {kwargs.get('admission_type', '-')}")
            print(f"Diagnosis : {kwargs.get('diagnosis_codes', '-')}")
            print(f"Prosedur  : {kwargs.get('procedure_codes', '-')}")
        except Exception:
            print("  (payload tidak bisa didecode)")
        print("-" * 60)
