import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

import redis
from rq import Queue

from config import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_USER,
    REDIS_PASSWORD,
    DATA_PARSING_AGENT,
)

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _org_id(description: str, job=None) -> str:
    if job is not None:
        try:
            org = job.kwargs.get("managing_organization_id")
            if org:
                return str(org)
        except Exception:
            pass
    match = UUID_RE.search(description or "")
    return match.group(0) if match else "?"


def _list_failed(conn, queue_name: str) -> list[dict]:
    queue = Queue(queue_name, connection=conn)
    jobs = []
    for jid in queue.failed_job_registry.get_job_ids():
        raw = conn.hgetall(f"rq:job:{jid}")
        fields = {}
        for key, value in raw.items():
            key = key.decode() if isinstance(key, bytes) else key
            value = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
            fields[key] = value
        job = None
        try:
            job = queue.fetch_job(jid)
        except Exception:
            job = None
        jobs.append({
            "id": jid,
            "description": fields.get("description", "?"),
            "org_id": _org_id(fields.get("description", ""), job),
        })
    return jobs


def _requeue_job(conn, queue_name: str, jid: str, at_front: bool = False) -> str:
    queue = Queue(queue_name, connection=conn)
    registry = queue.failed_job_registry
    try:
        registry.requeue(jid, at_front=at_front)
        return "ok"
    except Exception as e:
        return f"fail: {e}"


def main():
    parser = argparse.ArgumentParser(description="Re-dispatch job FAILED agar diproses lagi")
    parser.add_argument("--queue", default=DATA_PARSING_AGENT)
    parser.add_argument("--org", help="Filter hanya organisasi tertentu")
    parser.add_argument("--job-id", help="Requeue hanya job id tertentu")
    parser.add_argument("--at-front", action="store_true", help="Masukkan ke antrian depan")
    parser.add_argument("--requeue", action="store_true", help="Lakukan requeue (default dry-run)")
    parser.add_argument("--yes", action="store_true", help="Lewati konfirmasi")
    parser.add_argument("--redis-host", default=REDIS_HOST)
    parser.add_argument("--redis-port", type=int, default=REDIS_PORT)
    parser.add_argument("--redis-user", default=REDIS_USER)
    parser.add_argument("--redis-password", default=REDIS_PASSWORD)
    args = parser.parse_args()

    conn = redis.Redis(
        host=args.redis_host,
        port=args.redis_port,
        username=args.redis_user,
        password=args.redis_password,
        decode_responses=False,
    )
    conn.ping()

    if args.job_id:
        candidates = [j for j in _list_failed(conn, args.queue) if j["id"] == args.job_id]
    else:
        candidates = _list_failed(conn, args.queue)
    if args.org:
        candidates = [j for j in candidates if j["org_id"].startswith(args.org)]

    print(f"=== FAILED jobs queue {args.queue} ===")
    print(f"Total: {len(candidates)}")
    for job in candidates:
        print(f"  [{job['id'][:8]}] org={job['org_id']}  {job['description'][:90]}")
    print("-" * 80)

    if not candidates:
        print("Tidak ada job untuk di-requeue.")
        return

    if not args.requeue:
        print("Dry-run. Tambahkan --requeue untuk benar-benar men-dispatch ulang.")
        return

    if not args.yes:
        answer = input(f"Requeue {len(candidates)} job ke queue {args.queue}? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Dibatalkan.")
            return

    ok = 0
    failed = 0
    for job in candidates:
        result = _requeue_job(conn, args.queue, job["id"], at_front=args.at_front)
        if result == "ok":
            ok += 1
            print(f"  ok     {job['id'][:8]}  org={job['org_id']}")
        else:
            failed += 1
            print(f"  GAGAL  {job['id'][:8]}  {result}")

    print("-" * 80)
    print(f"Selesai: {ok} berhasil, {failed} gagal")


if __name__ == "__main__":
    main()
