"""Fetch data desa dari API BPS, lalu langsung POST ke endpoint villages.

Response BPS tetap disimpan ke folder output/ sebagai arsip.

Contoh:
    python sync_villages.py                      # parent dari DISTRICT_ID di .env
    python sync_villages.py --parent 5306071 5306061
    python sync_villages.py --dry-run            # fetch saja, tampilkan payload tanpa login/POST
"""

import argparse
import json
import sys

from config import DISTRICT_ID
from utils.utility import build_payload, fetch_villages, log, post_villages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--parent", nargs="+", help="Kode wilayah induk, bisa lebih dari satu (default: DISTRICT_ID di .env)")
    parser.add_argument("--level", default="desa", help="Level wilayah (default: desa)")
    parser.add_argument("--periode-merge", default="2025_2.2025", help="Periode data BPS (default: 2025_2.2025)")
    parser.add_argument("--district-id", help="Override district_id (default: 7 digit pertama kode_bps)")
    parser.add_argument("--max-requests", type=int, default=10, help="Maks request per district sebelum jeda; 0 = tanpa jeda (default: 10)")
    parser.add_argument("--delay", type=float, default=10, help="Lama jeda dalam detik setelah mencapai --max-requests (default: 10)")
    parser.add_argument("--max-retries", type=int, default=5, help="Maks coba ulang saat kena rate limit 429 (default: 5)")
    parser.add_argument("--retry-delay", type=float, default=10, help="Tunggu awal saat 429, berlipat tiap percobaan (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Tampilkan payload tanpa login/POST")
    args = parser.parse_args()

    parents = args.parent or ([DISTRICT_ID] if DISTRICT_ID else [])
    if not parents:
        parser.error("--parent belum diisi dan DISTRICT_ID di .env kosong")

    payloads = []
    for parent in parents:
        villages = fetch_villages(parent, args.level, args.periode_merge)
        payloads += [build_payload(v, args.district_id) for v in villages]

    if not payloads:
        print()
        log("Tidak ada desa untuk dikirim")
        return 1

    if args.dry_run:
        print(json.dumps(payloads, indent=2, ensure_ascii=False))
        return 0

    print()
    return 1 if post_villages(
        payloads,
        max_requests=args.max_requests,
        delay=args.delay,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
    ) else 0


if __name__ == "__main__":
    sys.exit(main())
