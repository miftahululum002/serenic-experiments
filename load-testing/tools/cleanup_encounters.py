#!/usr/bin/env python3
"""Hapus encounter sintetis yang dibuat harness, langsung dari database.

Dua sumber daftar encounter:

  1. Kohort (default) — `results/<profil>/cohort.json`, daftar persis yang
     dibuat oleh tools/seed_encounters.py. Paling tepat sasaran.
  2. Awalan identifier — `--prefix LTDEV`, untuk menyapu sisa dari run lama
     yang berkas kohort-nya sudah hilang.

Penghapusan SELALU dibatasi satu `managing_organization`, dan selalu butuh
kohort atau prefix — tidak ada mode "hapus semua".

    python tools/cleanup_encounters.py                      # dry-run dari kohort
    python tools/cleanup_encounters.py --apply
    python tools/cleanup_encounters.py --prefix LTDEV       # dry-run sapu bersih
    python tools/cleanup_encounters.py --prefix LTDEV --apply

Yang TIDAK dihapus: master data (praktisi, lokasi). Praktisi yang tercipta dari
`performer_id` objek memakai id asli dari template (mis. "23350"), jadi tidak
bisa dibedakan dari praktisi sungguhan — menghapusnya berisiko. Bersihkan
manual kalau memang perlu.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from config import settings  # noqa: E402
from lib.db import connect, delete_encounter, find_encounters  # noqa: E402
from lib.payload import Cohort  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="",
                    help="sapu berdasarkan awalan id_in_organization, mis. LTDEV. "
                         "Kalau kosong, dipakai daftar dari cohort.json")
    ap.add_argument("--cohort", default="", help="path cohort.json (default: results/<profil>/cohort.json)")
    ap.add_argument("--apply", action="store_true", help="benar-benar hapus (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="batasi jumlah encounter yang diproses")
    ap.add_argument("--verbose", action="store_true", help="tampilkan jumlah baris per tabel")
    ap.add_argument("--keep-cohort", action="store_true",
                    help="jangan singkirkan cohort.json setelah penghapusan berhasil")
    args = ap.parse_args()

    settings.require_db()
    org = settings.organization_id

    # --- kumpulkan target -------------------------------------------------
    noregistrasi = None
    cohort_path: Path | None = None
    sumber = f"prefix '{args.prefix}'"
    if not args.prefix:
        path = cohort_path = Path(args.cohort) if args.cohort else settings.results_dir / "cohort.json"
        if not path.exists():
            sys.exit(f"Kohort tidak ada: {path}\n"
                     "Pakai --prefix untuk menyapu berdasarkan awalan identifier, mis.\n"
                     f"  python tools/cleanup_encounters.py --prefix {settings.synthetic_prefix}")
        cohort = Cohort.load(path)
        noregistrasi = [e["noregistrasi"] for e in cohort.items]
        sumber = f"{path.name} ({len(noregistrasi)} encounter)"

    print(f"Profil     : {config.ACTIVE_PROFILE or '(hanya .env)'}")
    print(f"Database   : {settings.db_user}@{settings.db_host}:{settings.db_port}/{settings.db_name}")
    print(f"Organisasi : {org}")
    print(f"Sumber     : {sumber}")
    print(f"Mode       : {'HAPUS' if args.apply else 'dry-run'}\n")

    conn = connect(settings.db_host, settings.db_port, settings.db_name,
                   settings.db_user, settings.db_password)
    try:
        found = find_encounters(conn, org, noregistrasi=noregistrasi, prefix=args.prefix)
        if args.limit:
            found = found[:args.limit]

        if not found:
            print("Tidak ada encounter yang cocok. Tidak ada yang dihapus.")
            return

        print(f"Ditemukan {len(found)} encounter di database:")
        for e in found[:5]:
            print(f"  {e['noregistrasi']}  ->  {e['id']}")
        if len(found) > 5:
            print(f"  ... dan {len(found) - 5} lainnya")

        # Pengaman terakhir: identifier yang tidak berawalan prefix sintetis
        # kemungkinan besar bukan data test.
        asing = [e for e in found if not e["noregistrasi"].startswith(settings.synthetic_prefix)]
        if asing:
            print(f"\nPERINGATAN: {len(asing)} encounter TIDAK berawalan "
                  f"'{settings.synthetic_prefix}' — mis. {asing[0]['noregistrasi']}.")
            print("Pastikan itu memang data test sebelum melanjutkan.")

        if not args.apply:
            print(f"\nDRY-RUN. Tambahkan --apply untuk menghapus {len(found)} encounter "
                  "beserta seluruh turunannya.")
            return

        print(f"\nMenghapus {len(found)} encounter di organisasi {org}.")
        if input("Ketik 'hapus' untuk melanjutkan: ").strip().lower() != "hapus":
            sys.exit("Dibatalkan.")

        ok, gagal = 0, []
        total_rows: dict[str, int] = {}
        for i, e in enumerate(found, 1):
            try:
                affected = delete_encounter(conn, e["id"])
                ok += 1
                for k, v in affected.items():
                    total_rows[k] = total_rows.get(k, 0) + v
                rows = sum(affected.values())
                print(f"  [{i}/{len(found)}] {e['noregistrasi']}  {rows} baris")
                if args.verbose:
                    for k, v in sorted(affected.items(), key=lambda x: -x[1]):
                        print(f"        {v:>7}  {k}")
            except Exception as err:
                gagal.append((e["noregistrasi"], f"{type(err).__name__}: {err}"))
                print(f"  [{i}/{len(found)}] {e['noregistrasi']}  GAGAL: {err}")

        print(f"\nSelesai: {ok} terhapus, {len(gagal)} gagal.")
        if total_rows:
            print("\nTotal baris terhapus per tabel:")
            for k, v in sorted(total_rows.items(), key=lambda x: -x[1]):
                print(f"  {v:>8}  {k}")
        for noreg, err in gagal[:10]:
            print(f"  GAGAL {noreg}: {err}")

        # Kohort yang encounter-nya sudah dihapus WAJIB disingkirkan. Kalau
        # dibiarkan, test berikutnya akan memakainya, ingestor melewati semua
        # encounter yang tidak ditemukan, job selesai dalam milidetik, dan
        # hasil ukurnya palsu tanpa satu pun pesan error.
        if cohort_path and cohort_path.exists() and not gagal and not args.keep_cohort:
            spent = cohort_path.with_name(f"{cohort_path.stem}_terhapus{cohort_path.suffix}")
            cohort_path.rename(spent)
            print(f"\nKohort dipindahkan ke {spent.name} — encounter-nya sudah tidak ada,")
            print("jadi tidak boleh dipakai test lagi. Seed ulang sebelum menguji:")
            print("  python tools/seed_encounters.py --count 500")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
