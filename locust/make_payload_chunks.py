"""Pecah update_encounters.json menjadi chunk JSON < 3MB.

Menghasilkan data/update_encounters_chunks/part_XXXX.json — tiap chunk adalah
JSON object {norec: payload} dengan ukuran di bawah --max-mb (default 2.8MB)
supaya bisa di-commit ke git.

Contoh:
    python make_payload_chunks.py
    python make_payload_chunks.py --source data/update_encounters.json \
        --out data/update_encounters_chunks --max-mb 2.5
"""

import argparse
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Chunk payload update encounters")
    parser.add_argument("--source", default="data/update_encounters.json")
    parser.add_argument("--out", default="data/update_encounters_chunks")
    parser.add_argument("--max-mb", type=float, default=2.8,
                        help="Batas ukuran tiap chunk (MB)")
    args = parser.parse_args()

    src = Path(args.source)
    out = Path(args.out)
    max_bytes = int(args.max_mb * 1_000_000)

    print(f"Baca {src} ...")
    with open(src) as f:
        payload = json.load(f)
    total = len(payload)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    chunk = {}
    size = 0
    idx = 0
    max_size = 0
    min_size = None

    def flush():
        nonlocal chunk, size, idx, max_size, min_size
        if not chunk:
            return
        idx += 1
        fp = out / f"part_{idx:04d}.json"
        data = json.dumps(chunk, separators=(",", ":")).encode()
        fp.write_bytes(data)
        sz = len(data)
        max_size = max(max_size, sz)
        min_size = sz if min_size is None else min(min_size, sz)
        chunk = {}
        size = 0

    for norec, upd in payload.items():
        entry = {norec: upd}
        entry_bytes = len(json.dumps(entry, separators=(",", ":")).encode())
        if size + entry_bytes > max_bytes and chunk:
            flush()
        chunk.update(entry)
        size += entry_bytes
    flush()

    print(f"Total {total} norec -> {idx} chunk di {out}/")
    print(f"Ukuran per chunk: min={min_size/1e6:.2f} MB, "
          f"maks={max_size/1e6:.2f} MB (batas {args.max_mb} MB)")
    if max_size >= 3_000_000:
        print("PERHATIAN: ada chunk >= 3MB, kecilkan --max-mb dan ulangi.")


if __name__ == "__main__":
    main()
