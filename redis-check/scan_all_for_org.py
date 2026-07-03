from redis import Redis

TARGET_ORG = "d2a967c2-f848-46b9-8d02-bd94680d6bf3"
redis_conn = Redis(host="localhost", port=6379)

# Brute force scan ALL rq:job: keys for target org
print("=== Scan ALL rq:job:* untuk d2a967c2... ===\n")
job_keys = redis_conn.keys("rq:job:*")
found = 0
for jk in job_keys:
    raw = redis_conn.hget(jk, "data")
    if raw and TARGET_ORG.encode() in raw:
        jid = jk.decode().replace("rq:job:", "")
        found += 1
        print(f"  {jid}")
if found == 0:
    print("  (tidak ada)")

# Juga cek di keys lain
print("\n=== Cek key lain yang mungkin relevan ===")
other = redis_conn.keys("*d2a967c2*")
for k in other:
    print(f"  {k.decode()}")

# Cek intermediate keys
print("\n=== Cek intermediate queue entries ===")
for k in redis_conn.keys("rq:queue:*:intermediate:*"):
    val = redis_conn.get(k)
    if val and TARGET_ORG.encode() in val:
        print(f"  {k.decode()}: {val[:200]}")

print(f"\nDone. Found in rq:job: {found}")
