import zlib
from config import redis_conn
from constant import EKLAIM_BATCH_AGENT
from utils.utility import get_org_id
from rq import Queue


def peek_eklaim_queue_filtered(org_id: str):
    queue_name = EKLAIM_BATCH_AGENT
    queue = Queue(queue_name, connection=redis_conn)

    print(f"=== Mencari job dengan managing_organization_id: {org_id} ===")
    print(f"Total Job di Queue: {len(queue.jobs)}\n" + "-" * 50)

    found = 0
    for i, job in enumerate(queue.jobs, start=1):
        match = False
        func_name = ""
        args = ()
        kwargs = {}
        raw_data = None

        try:
            func_name = job.func_name
            args = job.args
            kwargs = job.kwargs
            if kwargs.get("managing_organization_id") == org_id:
                match = True
        except Exception:
            raw_data = redis_conn.hget(f"rq:job:{job.id}", "data")
            if raw_data and org_id.encode() in raw_data:
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

    print(f"\nTotal job dengan org ID '{org_id}': {found}")


if __name__ == "__main__":
    peek_eklaim_queue_filtered(get_org_id())
