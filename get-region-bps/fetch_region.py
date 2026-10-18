"""Ambil data wilayah dari API BPS dan simpan response-nya ke folder output/.

Contoh:
    python fetch_region.py                      # parent dari DISTRICT_ID di .env
    python fetch_region.py --parent 5306071
    python fetch_region.py --parent 5306071 5306061 --level desa --periode-merge 2025_2.2025
"""

import argparse
import sys
from urllib.error import HTTPError, URLError

from config import DISTRICT_ID
from utils.utility import build_filename, build_url, fetch, save


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--parent", nargs="+", help="Kode wilayah induk, bisa lebih dari satu (default: DISTRICT_ID di .env)")
    parser.add_argument("--level", default="desa", help="Level wilayah (default: desa)")
    parser.add_argument("--periode-merge", default="2025_2.2025", help="Periode data BPS (default: 2025_2.2025)")
    args = parser.parse_args()

    parents = args.parent or ([DISTRICT_ID] if DISTRICT_ID else [])
    if not parents:
        parser.error("--parent belum diisi dan DISTRICT_ID di .env kosong")

    failed = 0
    for parent in parents:
        url = build_url(args.level, parent, args.periode_merge)
        print(f"GET {url}")
        try:
            status, body = fetch(url)
        except HTTPError as e:
            print(f"  HTTP error {e.code}: {e.reason}")
            failed += 1
            continue
        except URLError as e:
            print(f"  Gagal koneksi: {e.reason}")
            failed += 1
            continue

        path = save(build_filename(args.level, parent, args.periode_merge), body)
        print(f"  status {status}, tersimpan di {path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
