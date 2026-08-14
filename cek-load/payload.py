"""Baca isi payload job RQ tanpa perlu meng-install backend.

Payload disimpan zlib-compressed + pickle, dan mereferensikan kelas dari paket
`app` (backend) yang tidak ada di sini. Unpickler di bawah mengganti kelas yang
tidak dikenal dengan stub generik — cukup untuk membaca struktur dan menghitung
berapa encounter/norec yang dibawa tiap job.
"""

import argparse
import io
import pickle
import zlib
from collections import Counter

from rq import Queue

from config import DATA_PARSING_AGENT, get_redis


class Stub:
    """Pengganti generik untuk kelas dari modul yang tidak tersedia."""

    def __init__(self, *args, **kwargs):
        self._args, self._kwargs = args, kwargs

    def __setstate__(self, state):
        # Pydantic menyimpan field sebenarnya di dalam kunci "__dict__".
        if isinstance(state, dict):
            inner = state.get("__dict__")
            self.__dict__.update(inner if isinstance(inner, dict) else state)
        else:
            self._state = state

    def __repr__(self):
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        return f"<{type(self).__name__} {d if d else self.__dict__}>"


_stub_cache = {}


class TolerantUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        try:
            return super().find_class(module, name)
        except (ImportError, AttributeError):
            key = f"{module}.{name}"
            if key not in _stub_cache:
                _stub_cache[key] = type(name, (Stub,), {"__module__": module})
            return _stub_cache[key]


def load_payload(conn, jid):
    raw = conn.hget(f"rq:job:{jid}", b"data")
    if raw is None:
        return None
    try:
        raw = zlib.decompress(raw)
    except zlib.error:
        pass  # tidak terkompresi
    return TolerantUnpickler(io.BytesIO(raw)).load()


def describe(v, depth=0, maxdepth=3):
    pad = "  " * (depth + 1)
    if isinstance(v, (list, tuple)):
        out = [f"{type(v).__name__}(len={len(v)})"]
        if v and depth < maxdepth:
            out.append(f"\n{pad}[0] -> {describe(v[0], depth + 1, maxdepth)}")
        return "".join(out)
    if isinstance(v, dict):
        out = [f"dict(keys={len(v)}): {list(v)[:12]}"]
        if depth < maxdepth:
            for k, sub in list(v.items())[:6]:
                out.append(f"\n{pad}{k} -> {describe(sub, depth + 1, maxdepth)}")
        return "".join(out)
    if isinstance(v, Stub):
        d = {k: sub for k, sub in v.__dict__.items() if not k.startswith("_")}
        out = [f"{type(v).__name__}(attrs={list(d)[:12]})"]
        if depth < maxdepth:
            for k, sub in list(d.items())[:6]:
                out.append(f"\n{pad}{k} -> {describe(sub, depth + 1, maxdepth)}")
        return "".join(out)
    s = str(v)
    return s if len(s) <= 120 else s[:120] + "…"


ENCOUNTER_FIELDS = ("updates", "encounters", "new_encounters", "completed_encounters")


def count_encounters(args, kwargs):
    """Jumlah encounter yang dibawa satu job.

    Dicari dari field yang memang menampung encounter (`updates`, dll), bukan
    koleksi terbesar — atribut lain seperti `data_source_processing_mode` bisa
    jauh lebih panjang dan menyesatkan.
    """
    stack = list(args) + list(kwargs.values())
    seen = 0
    while stack and seen < 200:
        v = stack.pop()
        seen += 1
        if isinstance(v, Stub):
            for name in ENCOUNTER_FIELDS:
                got = v.__dict__.get(name)
                if isinstance(got, (list, tuple)):
                    return len(got)
            stack.extend(v.__dict__.values())
        elif isinstance(v, dict):
            for name in ENCOUNTER_FIELDS:
                if isinstance(v.get(name), (list, tuple)):
                    return len(v[name])
            stack.extend(v.values())
    return None


def count_items(obj):
    """Tebak berapa 'item' (encounter/norec) yang dibawa satu job."""
    best = 0
    stack = [obj]
    seen = 0
    while stack and seen < 500:
        v = stack.pop()
        seen += 1
        if isinstance(v, (list, tuple)):
            best = max(best, len(v))
            stack.extend(v[:5])
        elif isinstance(v, dict):
            stack.extend(list(v.values())[:20])
        elif isinstance(v, Stub):
            stack.extend(list(v.__dict__.values())[:20])
    return best


def profile(conn, qname, sample=30) -> dict:
    """Profil isi job: fungsi apa, berapa item per job, dari organisasi mana."""
    q = Queue(qname, connection=conn)
    ids = q.get_job_ids(0, sample - 1)
    funcs, items, orgs, errors = Counter(), Counter(), Counter(), Counter()
    sizes = []
    for jid in ids:
        raw = conn.hget(f"rq:job:{jid}", b"data")
        if raw:
            sizes.append(len(raw))
        try:
            func, _inst, fargs, fkwargs = load_payload(conn, jid)
        except Exception as e:
            errors[type(e).__name__] += 1
            continue
        funcs[func.rsplit(".", 1)[-1]] += 1
        items[count_encounters(fargs, fkwargs)] += 1
        for a in fargs:
            s = str(a)
            if len(s) == 36 and s.count("-") == 4:
                orgs[s] += 1
    return {
        "queue": qname,
        "sampled": len(ids),
        "funcs": dict(funcs),
        "items_per_job": dict(items),
        "orgs": dict(orgs),
        "errors": dict(errors),
        "size_avg_kb": (sum(sizes) / len(sizes) / 1024) if sizes else None,
        "size_min_kb": (min(sizes) / 1024) if sizes else None,
        "size_max_kb": (max(sizes) / 1024) if sizes else None,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--queue", default=DATA_PARSING_AGENT)
    p.add_argument("--sample", type=int, default=25)
    p.add_argument("--show", type=int, default=1, help="berapa job ditampilkan detail")
    args = p.parse_args()

    conn = get_redis()
    q = Queue(args.queue, connection=conn)
    ids = q.get_job_ids(0, args.sample - 1)
    print(f"Queue {args.queue}: {q.count} job pending, "
          f"sampling {len(ids)} job terdepan\n")

    sizes = Counter()
    shown = 0
    for jid in ids:
        try:
            payload = load_payload(conn, jid)
        except Exception as e:
            print(f"  {jid}: gagal dibaca ({type(e).__name__}: {e})")
            continue
        func, _inst, fargs, fkwargs = payload
        n = count_items({"args": fargs, "kwargs": fkwargs})
        sizes[n] += 1
        if shown < args.show:
            shown += 1
            print(f"--- contoh job {jid} ---")
            print(f"func: {func}")
            for i, a in enumerate(fargs):
                print(f"  args[{i}] -> {describe(a)}")
            for k, v in fkwargs.items():
                print(f"  {k} -> {describe(v)}")
            print()

    print("Distribusi jumlah item terbesar per job:")
    for n, c in sorted(sizes.items()):
        print(f"  {c:>4} job membawa koleksi terbesar berukuran {n}")


if __name__ == "__main__":
    main()
