"""Pecah array JSON menjadi beberapa file dengan ukuran tetap (jumlah item).

Contoh:
    python split_json.py --source data/full_new_encounters.json \
        --out data/full_new_encounters_chunks --size 30
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Split JSON array into chunks")
    parser.add_argument("--source", type=Path, required=True,
                        help="File JSON array yang akan dipecah")
    parser.add_argument("--out", type=Path, required=True,
                        help="Folder output untuk file chunk")
    parser.add_argument("--size", type=int, default=30,
                        help="Jumlah item per chunk (default: 30)")
    args = parser.parse_args()

    with open(args.source, encoding="utf-8") as f:
        data = json.load(f)
    total = len(data)

    args.out.mkdir(parents=True, exist_ok=True)
    for idx in range(0, total, args.size):
        chunk = data[idx:idx + args.size]
        fp = args.out / f"chunk_{idx // args.size + 1:04d}.json"
        fp.write_text(json.dumps(chunk, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    n_chunks = (total + args.size - 1) // args.size
    print(f"Total {total} item -> {n_chunks} chunk (maks {args.size}/chunk) di {args.out}/")


if __name__ == "__main__":
    main()
