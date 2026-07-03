from redis import Redis
from rq import Queue

redis_conn = Redis(host="localhost", port=6379)
queue_name = "eklaim_batch_agent_prod"
queue = Queue(queue_name, connection=redis_conn)

print(f"Queue: {queue_name}")
print(f"  Jobs (queued): {len(queue.jobs)}")
print(f"  Started: {queue.started_job_registry.count}")
print(f"  Finished: {queue.finished_job_registry.count}")
print(f"  Failed: {queue.failed_job_registry.count}")
print(f"  Deferred: {queue.deferred_job_registry.count}")
print(f"  Scheduled: {queue.scheduled_job_registry.count}")

print("\n--- Worker Info ---")
workers = queue.get_jobs()  # Actually this won't work for workers, let me check differently
# Check all keys related to this queue
keys = redis_conn.keys(f"rq:queue:{queue_name}*")
print(f"Queue keys: {keys}")

keys2 = redis_conn.keys("rq:worker:*")
print(f"\nWorker keys: {keys2}")
for k in keys2:
    print(f"  {k}: {redis_conn.hgetall(k)}")

# Check for any rq:job: keys with started status
job_keys = redis_conn.keys("rq:job:*")
print(f"\nTotal rq:job:* keys: {len(job_keys)}")
