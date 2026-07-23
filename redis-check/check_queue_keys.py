from config import redis_conn


def check_queue_keys():
    keys = redis_conn.keys("rq:queue:*")
    print(f"Total rq:queue:* keys: {len(keys)}")
    for k in sorted(keys):
        t = redis_conn.type(k).decode()
        print(f"  {t:10s}  {k.decode()}")

    print("\n--- All rq: keys ---")
    rq_keys = redis_conn.keys("rq:*")
    counts = {}
    for k in sorted(rq_keys):
        prefix = k.decode().rsplit(":", 1)[0]
        counts[prefix] = counts.get(prefix, 0) + 1
    for p, c in sorted(counts.items()):
        print(f"  {p}  -> {c}")


if __name__ == "__main__":
    check_queue_keys()
