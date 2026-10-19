"""Update postal_code, latitude, longitude village berdasarkan village_id.

Alur per village:
  1. GET API_VILLAGE/{village_id} untuk ambil code_dagri
  2. Ambil detail dari wilayah.cahyadsn.com pakai code_dagri
  3. PATCH API_VILLAGE/{village_id} dengan {postal_code, latitude, longitude}

Contoh:
    python update_villages.py --village-id 5306071001
    python update_villages.py --village-id 5306071001 5306071002
    python update_villages.py --file data/village_ids.csv     # satu village_id per baris, header boleh ada
    python update_villages.py --village-id 5306071001 --dry-run
"""

import argparse
import sys
from pathlib import Path

import config  # noqa: F401  memuat .env saat di-import
from utils.utility import log, update_villages


def read_village_ids(path: Path) -> list[str]:
    """Baca village_id dari file: kolom pertama tiap baris, baris non-angka (header) dilewati."""
    ids = []
    for line in path.read_text().splitlines():
        value = line.split(",")[0].strip().strip('"')
        if value.isdigit():
            ids.append(value)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--village-id", nargs="+", help="village_id, bisa lebih dari satu")
    source.add_argument("--file", type=Path, help="File berisi village_id (satu per baris / kolom pertama CSV)")
    parser.add_argument("--delay", type=float, default=0, help="Jeda antar village dalam detik (default: 0)")
    parser.add_argument("--detail-delay", type=float, default=1, help="Jeda sebelum request ke wilayah.cahyadsn.com (default: 1)")
    parser.add_argument("--max-retries", type=int, default=5, help="Maks coba ulang saat kena rate limit 429 (default: 5)")
    parser.add_argument("--retry-delay", type=float, default=10, help="Tunggu awal saat 429, berlipat tiap percobaan (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="GET village + detail saja, PATCH tidak dikirim")
    args = parser.parse_args()

    village_ids = args.village_id or read_village_ids(args.file)
    if not village_ids:
        log("Tidak ada village_id untuk diupdate")
        return 1

    failed = update_villages(
        village_ids,
        delay=args.delay,
        detail_delay=args.detail_delay,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
        dry_run=args.dry_run,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
