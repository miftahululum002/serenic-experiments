"""Ringkas penyebab kegagalan job per queue (baca exc_info dari registry failed)."""

import re
from collections import Counter
from datetime import datetime, timezone

from rq import Queue

from config import get_redis


def main():
    conn = get_redis()
    now = datetime.now(timezone.utc)

    qnames = sorted(
        k.decode().split("rq:queue:", 1)[1] for k in conn.smembers("rq:queues")
    )
    for qname in qnames:
        q = Queue(qname, connection=conn)
        ids = q.failed_job_registry.get_job_ids()
        if not ids:
            continue
        print(f"\n=== {qname} — {len(ids)} job gagal ===")
        kinds = Counter()
        newest = None
        oldest = None
        for jid in ids:
            h = conn.hgetall(f"rq:job:{jid}")
            exc = (h.get(b"exc_info") or b"").decode(errors="replace")
            last = [ln for ln in exc.strip().splitlines() if ln.strip()]
            msg = last[-1] if last else "(tanpa exc_info)"
            msg = re.sub(r"0x[0-9a-f]+|\b[0-9a-f]{8}-[0-9a-f-]{27}\b|\d+", "N", msg)
            kinds[msg[:160]] += 1
            raw = h.get(b"ended_at") or h.get(b"enqueued_at")
            if raw:
                ts = datetime.fromisoformat(raw.decode()).replace(tzinfo=timezone.utc)
                newest = ts if newest is None or ts > newest else newest
                oldest = ts if oldest is None or ts < oldest else oldest
        if oldest and newest:
            print(f"  rentang: {(now-newest).total_seconds()/3600:.1f} jam lalu "
                  f"s/d {(now-oldest).total_seconds()/3600:.1f} jam lalu")
        for msg, c in kinds.most_common(6):
            print(f"  {c:>5}x  {msg}")


if __name__ == "__main__":
    main()
