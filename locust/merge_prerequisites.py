"""Gabungkan semua file prerequisites_*.json menjadi satu payload prerequisites.

Menangani 3 variasi struktur: payload langsung, dibungkus request_data, dan dobel
bungkus. Setiap kategori (lokasi, praktisi, tim_organisasi) digabung dan
diduplikasi berdasarkan field id — jika id sama, diambil versi dari file paling baru.

Contoh:
    python merge_prerequisites.py --source data/38c789cc-2564-4f39-88ee-1f855b44d99c \
        --out data/prerequisites.json
"""

import argparse
import json
from pathlib import Path

CATEGORIES = ("lokasi", "praktisi", "tim_organisasi")


def extract_payload(data):
    """Turun ke bungkus request_data sampai menemukan payload dengan data."""
    cur = data
    while isinstance(cur, dict) and isinstance(cur.get("request_data"), dict):
        cur = cur["request_data"]
    return cur


def main():
    parser = argparse.ArgumentParser(description="Merge & dedup prerequisites")
    parser.add_argument("--source", type=Path, required=True,
                        help="Folder berisi file prerequisites_*.json (dicari rekursif)")
    parser.add_argument("--out", type=Path, required=True,
                        help="File output JSON (payload prerequisites)")
    args = parser.parse_args()

    files = sorted(args.source.rglob("prerequisites_*.json"))
    print(f"Total file: {len(files)}")

    merged = {}
    duplicates = 0

    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Skip (parse error): {fp}: {e}")
            continue

        file_ts = str(data.get("timestamp", ""))
        payload = extract_payload(data)

        for cat in CATEGORIES:
            items = payload.get(cat) or []
            if cat not in merged:
                merged[cat] = {}
            for item in items:
                key = item.get("id")
                if not key:
                    key = "missing|" + json.dumps(item, sort_keys=True)
                if key in merged[cat]:
                    duplicates += 1
                    if file_ts > merged[cat][key][0]:
                        merged[cat][key] = (file_ts, item)
                else:
                    merged[cat][key] = (file_ts, item)

    result = {}
    for cat in CATEGORIES:
        items = [v[1] for v in merged[cat].values()]
        items.sort(key=lambda e: (e.get("name", ""), e.get("id", "")))
        result[cat] = items

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    for cat in CATEGORIES:
        print(f"  {cat}: {len(result[cat])}")
    print(f"Duplikat dibuang (ambil terbaru): {duplicates}")
    print(f"Ditulis ke: {args.out}")


if __name__ == "__main__":
    main()
