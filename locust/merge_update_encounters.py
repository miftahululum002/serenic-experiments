"""Gabungkan data updateEncounters dari banyak file hasil polling menjadi satu file JSON.

Semua file dengan nama update_encounters*.json di dalam --source (rekursif) dibaca,
field updates pada request_data diekstrak, lalu digabung. Jika ada norec yang sama
(duplikat), diambil versi dengan timestamp file paling baru.

Contoh:
    python merge_update_encounters.py --source data/38c789cc-2564-4f39-88ee-1f855b44d99c \
        --out data/full_update_encounters.json
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Merge & dedup updateEncounters")
    parser.add_argument("--source", type=Path, required=True,
                        help="Folder berisi file update_encounters*.json (dicari rekursif)")
    parser.add_argument("--out", type=Path, required=True,
                        help="File output JSON (array of updates)")
    parser.add_argument("--key", default="norec",
                        help="Field kunci deduplikasi (default: norec)")
    args = parser.parse_args()

    files = sorted(args.source.rglob("update_encounters*.json"))
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
        updates = (data.get("request_data") or {}).get("updates") or []
        for upd in updates:
            key = upd.get(args.key)
            if not key:
                missing_key += 1
                key = "missing|" + json.dumps(upd, sort_keys=True)
            if key in by_key:
                duplicates += 1
                if timestamp > by_key[key][0]:
                    by_key[key] = (timestamp, upd)
            else:
                by_key[key] = (timestamp, upd)

    results = [v[1] for v in by_key.values()]
    results.sort(key=lambda e: (e.get("updated_at", ""), e.get(args.key, "")))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Unique {args.key}: {len(results)}")
    print(f"Duplikat dibuang (ambil terbaru): {duplicates}")
    print(f"Tanpa kunci {args.key}: {missing_key}")
    print(f"Ditulis ke: {args.out}")


if __name__ == "__main__":
    main()
