import zlib
from collections import defaultdict
from config import redis_conn
from constant import DATA_PARSING_AGENT
from rq import Queue

QUEUE_NAME = DATA_PARSING_AGENT
WORKER_SET_KEY = f"rq:workers:{QUEUE_NAME}"


def parse_job_kwargs(job, queue):
    try:
        kwargs = job.kwargs
        return {
            "job_id": job.id,
            "func": job.func_name,
            "encounter_id": kwargs.get("encounter_id", "-"),
            "admission_type": kwargs.get("admission_type", "-"),
            "org_id": kwargs.get("managing_organization_id", "unknown"),
        }
    except Exception:
        raw_data = redis_conn.hget(f"rq:job:{job.id}", "data")
        org_id = "unknown"
        if raw_data:
            try:
                decompressed = zlib.decompress(raw_data)
                text = decompressed.decode("utf-8", errors="replace")
            except Exception:
                text = raw_data.decode("utf-8", errors="replace")
            for part in text.split(","):
                if "managing_organization_id" in part:
                    org_id = part.split(":")[-1].strip().strip("'\"")
                    break
        return {
            "job_id": job.id,
            "func": "[undecodable]",
            "encounter_id": "-",
            "admission_type": "-",
            "org_id": org_id,
        }


def peek_integration_by_org():
    queue = Queue(QUEUE_NAME, connection=redis_conn)

    print(f"=== Queue: {QUEUE_NAME} ===")
    print(f"Total pending jobs: {len(queue.jobs)}\n")

    pending_by_org = defaultdict(list)
    undecodable_jobs = []

    for job in queue.jobs:
        info = parse_job_kwargs(job, queue)
        pending_by_org[info["org_id"]].append(info)
        if info["func"] == "[undecodable]":
            undecodable_jobs.append(info["job_id"])

    print("--- Pending Jobs by Org ---")
    for org_id in sorted(pending_by_org.keys()):
        jobs = pending_by_org[org_id]
        print(f"\n  Org: {org_id}  ({len(jobs)} jobs)")
        for j in jobs[:3]:
            print(f"    - {j['job_id']}  func={j['func']}")
            if j["encounter_id"] != "-":
                print(f"      encounter: {j['encounter_id']}, type: {j['admission_type']}")
        if len(jobs) > 3:
            print(f"    ... +{len(jobs) - 3} jobs lainnya")

    registry = queue.started_job_registry
    started_ids = registry.get_job_ids()
    print(f"\n\n=== Running Jobs (StartedJobRegistry) ===")
    print(f"Total running: {len(started_ids)}\n")

    running_by_org = defaultdict(list)
    for jid in started_ids:
        job = queue.fetch_job(jid)
        if job:
            info = parse_job_kwargs(job, queue)
            running_by_org[info["org_id"]].append(info)

    if running_by_org:
        print("--- Running Jobs by Org ---")
        for org_id in sorted(running_by_org.keys()):
            jobs = running_by_org[org_id]
            print(f"\n  Org: {org_id}  ({len(jobs)} jobs)")
            for j in jobs:
                print(f"    - {j['job_id']}  func={j['func']}")
                if j["encounter_id"] != "-":
                    print(f"      encounter: {j['encounter_id']}, type: {j['admission_type']}")
    else:
        print("(Tidak ada job yang sedang diproses)")

    print(f"\n\n=== Workers ===")
    worker_ids = redis_conn.smembers(WORKER_SET_KEY)
    busy_count = 0
    for wid_bytes in worker_ids:
        wid = wid_bytes.decode()
        data = redis_conn.hgetall(f"rq:worker:{wid}")
        state = data.get(b"state", b"?").decode()
        current_job_id = data.get(b"current_job_id", b"").decode()
        pid = data.get(b"pid", b"?").decode()
        hostname = data.get(b"hostname", b"?").decode()

        if state == "busy":
            busy_count += 1

        print(f"  {wid[:12]}...  state={state}  pid={pid}  host={hostname}  job={current_job_id or '(none)'}")

    print(f"\n  Total: {len(worker_ids)} workers, {busy_count} busy, {len(worker_ids) - busy_count} idle")

    print(f"\n\n=== Finished Jobs (last 10) ===")
    finished_ids = queue.finished_job_registry.get_job_ids()
    for jid in finished_ids[-10:]:
        job = queue.fetch_job(jid)
        if job:
            try:
                kwargs = job.kwargs
                print(f"  {jid}  org={kwargs.get('managing_organization_id', '-')}  enc={kwargs.get('encounter_id', '-')}  status={job.get_status()}")
            except Exception:
                print(f"  {jid}  (cannot decode)")
    print(f"\n  Total finished: {len(finished_ids)}")

    print(f"\n\n=== Failed Jobs (last 10) ===")
    failed_ids = queue.failed_job_registry.get_job_ids()
    for jid in failed_ids[-10:]:
        job = queue.fetch_job(jid)
        if job:
            try:
                kwargs = job.kwargs
                print(f"  {jid}  org={kwargs.get('managing_organization_id', '-')}  enc={kwargs.get('encounter_id', '-')}")
            except Exception:
                print(f"  {jid}  (cannot decode)")
    print(f"  Total failed: {len(failed_ids)}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Queue     : {QUEUE_NAME}")
    print(f"Pending   : {len(queue.jobs)}")
    print(f"Running   : {len(started_ids)}")
    print(f"Finished  : {len(finished_ids)}")
    print(f"Failed    : {len(failed_ids)}")
    print(f"Workers   : {len(worker_ids)} ({busy_count} busy)")

    print(f"\n--- Pending by Org ---")
    if pending_by_org:
        for org_id, jobs in sorted(pending_by_org.items(), key=lambda x: -len(x[1])):
            print(f"  {org_id}: {len(jobs)} jobs")
    else:
        print("  (empty)")

    print(f"\n--- Running by Org ---")
    if running_by_org:
        for org_id, jobs in sorted(running_by_org.items(), key=lambda x: -len(x[1])):
            print(f"  {org_id}: {len(jobs)} jobs")
    else:
        print("  (empty)")

    if undecodable_jobs:
        print(f"\n({len(undecodable_jobs)} jobs undecodable)")


if __name__ == "__main__":
    peek_integration_by_org()
