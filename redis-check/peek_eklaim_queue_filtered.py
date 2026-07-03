import pickle
import zlib
import json
from redis import Redis
from rq import Queue

# TARGET_ORG = "12dd43d5-82f4-4fc5-a982-ee87d665f0dc"
TARGET_ORG = "d2a967c2-f848-46b9-8d02-bd94680d6bf3"

redis_conn = Redis(host="localhost", port=6379)
queue_name = "eklaim_batch_agent_prod"
queue = Queue(queue_name, connection=redis_conn)

print(f"=== Mencari job dengan managing_organization_id: {TARGET_ORG} ===")
print(f"Total Job di Queue: {len(queue.jobs)}\n" + "-" * 50)

found = 0
for i, job in enumerate(queue.jobs, start=1):
    match = False
    func_name = ""
    args = ()
    kwargs = {}

    try:
        func_name = job.func_name
        args = job.args
        kwargs = job.kwargs
        if kwargs.get("managing_organization_id") == TARGET_ORG:
            match = True
    except Exception:
        raw_data = redis_conn.hget(f"rq:job:{job.id}", "data")
        if raw_data and TARGET_ORG.encode() in raw_data:
            match = True
        func_name = "[undecodable]"

    if not match:
        continue

    found += 1
    print(f"[{i}] Job ID: {job.id}")
    print(f"    Fungsi : {func_name}")

    if kwargs:
        print(f"    Args   : {args}")
        print(f"    Kwargs : {kwargs}")
    elif raw_data:
        print("    Raw Extract (zlib decompress):")
        try:
            decompressed = zlib.decompress(raw_data)
            clean_text = "".join(
                chr(b) for b in decompressed if 32 <= b < 127 or b == 10
            )
            print(f"        {clean_text[:1000]}")
        except Exception:
            clean_text = "".join(chr(b) for b in raw_data if 32 <= b < 127 or b == 10)
            print(f"        {clean_text[:1000]}")
    print("-" * 50)

print(f"\nTotal job dengan org ID '{TARGET_ORG}': {found}")
