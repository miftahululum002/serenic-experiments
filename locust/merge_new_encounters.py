"""Gabungkan data newEncounters dari banyak file hasil polling menjadi satu file JSON.

Semua file dengan nama new_encounters*.json di dalam --source (rekursif) dibaca,
field newEncounters pada request_data diekstrak, lalu digabung. Jika ada norec
yang sama (duplikat), diambil versi dengan timestamp file paling baru.

Contoh:
    python merge_new_encounters.py
    python merge_new_encounters.py --source data/38c789cc-2564-4f39-88ee-1f855b44d99c \
        --out data/full_new_encounters.json
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Merge & dedup newEncounters")
    parser.add_argument("--source", type=Path, required=True,
                        help="Folder berisi file new_encounters*.json (dicari rekursif)")
    parser.add_argument("--out", type=Path, required=True,
                        help="File output JSON (array of encounters)")
    parser.add_argument("--key", default="norec",
                        help="Field kunci deduplikasi (default: norec)")
    args = parser.parse_args()

    files = sorted(args.source.rglob("new_encounters*.json"))
    print(f"Total file: {len(files)}")

    by_key = {}
    duplicates = 0
    missing_key = 0

    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Skip (parse error): {fp}: {e}")
            continue

        timestamp = data.get("timestamp", "")
        encounters = (data.get("request_data") or {}).get("newEncounters") or []
        for enc in encounters:
            key = enc.get(args.key)
            if not key:
                missing_key += 1
                key = "missing|" + json.dumps(enc, sort_keys=True)
            if key in by_key:
                duplicates += 1
                if timestamp > by_key[key][0]:
                    by_key[key] = (timestamp, enc)
            else:
                by_key[key] = (timestamp, enc)

    results = [v[1] for v in by_key.values()]
    results.sort(key=lambda e: (e.get("tglregistrasi", ""), e.get(args.key, "")))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Unique {args.key}: {len(results)}")
    print(f"Duplikat dibuang (ambil terbaru): {duplicates}")
    print(f"Tanpa kunci {args.key}: {missing_key}")
    print(f"Ditulis ke: {args.out}")


if __name__ == "__main__":
    main()
