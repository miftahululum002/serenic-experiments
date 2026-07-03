from config import redis_conn
from rq import Queue

# Cek workers set
print("=== rq:workers set ===")
for w in redis_conn.smembers("rq:workers"):
    print(f"  {w.decode()}")

# Cek workers yang mungkin handle eklaim
print("\n=== Semua worker dengan detail state ===")
eklaim_workers = []
for k in redis_conn.keys("rq:worker:*"):
    data = redis_conn.hgetall(k)
    state = data.get(b"state", b"?").decode()
    queues = data.get(b"queues", b"").decode()
    cj_id = data.get(b"current_job_id", b"").decode()
    hostname = data.get(b"hostname", b"").decode()
    pid = data.get(b"pid", b"").decode()
    last_hb = data.get(b"last_heartbeat", b"").decode()
    birth = data.get(b"birth", b"").decode()

    # Cek apakah ada kaitan dengan eklaim
    is_eklaim = "eklaim" in queues or "eklaim" in k.decode().lower()
    is_busy = state == "busy" or cj_id

    if is_eklaim or is_busy:
        eklaim_workers.append(k)
        print(f"\n  Worker: {k.decode()}")
        print(f"    queues: {queues}")
        print(f"    state: {state}")
        print(f"    current_job_id: {cj_id}")
        print(f"    hostname: {hostname}")
        print(f"    pid: {pid}")
        print(f"    last_heartbeat: {last_hb}")
        print(f"    birth: {birth}")

        if cj_id:
            job = Queue(queues.split(",")[0] if queues else "", connection=redis_conn).fetch_job(cj_id)
            if job:
                try:
                    kw = job.kwargs
                    print(f"    -> enc: {kw.get('encounter_id')}")
                    print(f"    -> org: {kw.get('managing_organization_id')}")
                except:
                    pass

print(f"\n\nTotal worker dengan eklaim atau busy: {len(eklaim_workers)}")
