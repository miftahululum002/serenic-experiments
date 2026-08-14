"""Lacak nasib job yang keluar dari antrean: benar diproses, atau dibuang?

Backlog kadang turun jauh lebih cepat daripada jumlah job yang selesai. Script
ini mengambil isi list antrean, lalu setelah beberapa saat memeriksa status
akhir tiap job yang hilang dari list — apakah finished/started (diproses) atau
canceled/hash hilang (dibuang tanpa diproses).
"""

import argparse
import time
from collections import Counter

from rq import Queue

from config import DATA_PARSING_AGENT, get_redis


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--queue", default=DATA_PARSING_AGENT)
    p.add_argument("--wait", type=float, default=90.0)
    args = p.parse_args()

    conn = get_redis()
    q = Queue(args.queue, connection=conn)

    before = q.get_job_ids()
    print(f"Snapshot awal: {len(before)} job di antrean. "
          f"Menunggu {args.wait:.0f} detik...", flush=True)
    time.sleep(args.wait)
    after = set(q.get_job_ids())

    gone = [j for j in before if j not in after]
    print(f"Job yang keluar dari antrean: {len(gone)}\n")

    fates = Counter()
    for jid in gone:
        h = conn.hgetall(f"rq:job:{jid}")
        if not h:
            fates["HASH HILANG (dihapus/expired — tidak diproses)"] += 1
            continue
        status = (h.get(b"status") or b"?").decode()
        started = bool(h.get(b"started_at"))
        fates[f"status={status} started_at={'ya' if started else 'tidak'}"] += 1

    for k, v in fates.most_common():
        print(f"  {v:>5}  {k}")

    added = [j for j in after if j not in set(before)]
    print(f"\nJob baru masuk antrean selama periode: {len(added)}")
    print(f"Panjang antrean: {len(before)} -> {len(after)}")


if __name__ == "__main__":
    main()
