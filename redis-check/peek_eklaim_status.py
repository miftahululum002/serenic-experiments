from config import redis_conn
from constant import EKLAIM_BATCH_AGENT
from rq import Queue


def peek_eklaim_status():
    queue_name = EKLAIM_BATCH_AGENT
    queue = Queue(queue_name, connection=redis_conn)

    print(f"Queue: {queue_name}")
    print(f"  Jobs (queued): {len(queue.jobs)}")
    print(f"  Started: {queue.started_job_registry.count}")
    print(f"  Finished: {queue.finished_job_registry.count}")
    print(f"  Failed: {queue.failed_job_registry.count}")
    print(f"  Deferred: {queue.deferred_job_registry.count}")
    print(f"  Scheduled: {queue.scheduled_job_registry.count}")

    print("\n--- Worker Info ---")
    keys = redis_conn.keys(f"rq:queue:{queue_name}*")
    print(f"Queue keys: {keys}")

    keys2 = redis_conn.keys("rq:worker:*")
    print(f"\nWorker keys: {keys2}")
    for k in keys2:
        print(f"  {k}: {redis_conn.hgetall(k)}")

    job_keys = redis_conn.keys("rq:job:*")
    print(f"\nTotal rq:job:* keys: {len(job_keys)}")


if __name__ == "__main__":
    peek_eklaim_status()
