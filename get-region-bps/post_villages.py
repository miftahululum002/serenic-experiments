"""Login ke API lalu POST setiap desa dari hasil fetch_region.py ke endpoint villages.

Contoh:
    python post_villages.py --file output/desa_5306071_2025_2.2025_20261017_222741.json
    python post_villages.py --file output/desa_xxx.json --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

import config  # noqa: F401  memuat .env saat di-import
from utils.utility import build_payload, post_villages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, type=Path, help="File JSON hasil fetch_region.py")
    parser.add_argument("--district-id", help="Override district_id (default: 7 digit pertama kode_bps)")
    parser.add_argument("--dry-run", action="store_true", help="Tampilkan payload tanpa login/POST")
    args = parser.parse_args()

    villages = json.loads(args.file.read_text())
    if not isinstance(villages, list) or not villages:
        print(f"File {args.file} tidak berisi daftar desa")
        return 1

    payloads = [build_payload(v, args.district_id) for v in villages]

    if args.dry_run:
        print(json.dumps(payloads, indent=2, ensure_ascii=False))
        return 0

    return 1 if post_villages(payloads) else 0


if __name__ == "__main__":
    sys.exit(main())
