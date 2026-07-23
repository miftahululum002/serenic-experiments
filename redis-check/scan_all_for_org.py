from config import redis_conn
from utils.utility import get_org_id


def scan_all_for_org(org_id: str):
    print(f"=== Scan ALL rq:job:* untuk {org_id[:8]}... ===\n")
    job_keys = redis_conn.keys("rq:job:*")
    found = 0
    for jk in job_keys:
        raw = redis_conn.hget(jk, "data")
        if raw and org_id.encode() in raw:
            jid = jk.decode().replace("rq:job:", "")
            found += 1
            print(f"  {jid}")
    if found == 0:
        print("  (tidak ada)")

    print("\n=== Cek key lain yang mungkin relevan ===")
    other = redis_conn.keys(f"*{org_id[:8]}*")
    for k in other:
        print(f"  {k.decode()}")

    print("\n=== Cek intermediate queue entries ===")
    for k in redis_conn.keys("rq:queue:*:intermediate:*"):
        val = redis_conn.get(k)
        if val and org_id.encode() in val:
            print(f"  {k.decode()}: {val[:200]}")

    print(f"\nDone. Found in rq:job: {found}")


if __name__ == "__main__":
    scan_all_for_org(get_org_id())
