#!/usr/bin/env python3
"""Siapkan kohort encounter sintetis di DB target.

WAJIB dijalankan sebelum test apa pun.

Alasannya: `ingest_update_encounter_request_streaming` melewati encounter yang
tidak ditemukan di DB (ingestor.py:613 "Encounter ... not found, skipping
update"). Kalau kohort belum ada, worker parsing akan menyelesaikan job dalam
hitungan milidetik tanpa mengerjakan apa pun, dan hasil pengukurannya palsu.

/encounters/new memproses secara sinkron di API (tanpa antrean), jadi seeding
tidak mencemari antrean parsing.

    python tools/seed_encounters.py --count 600
"""
import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from lib.driver import make_client, post_json  # noqa: E402
from lib.payload import Cohort, build_new_encounters_request, make_identities, payload_bytes  # noqa: E402


async def run(count: int, batch: int, admission: str, out: Path, run_id: str,
              append: bool) -> None:
    # Kohort lama tidak boleh hilang begitu saja: berkas ini satu-satunya
    # catatan encounter apa saja yang pernah dibuat harness. Kalau ditimpa
    # tanpa jejak, encounter lamanya jadi yatim di database dan hanya bisa
    # dibersihkan lewat --prefix.
    existing: list[dict] = []
    if out.exists():
        old = Cohort.load(out)
        if append:
            existing = old.items
            print(f"Menambah ke kohort yang ada: {len(existing)} encounter\n")
        else:
            archive = out.with_name(f"{out.stem}_{datetime.now():%Y%m%d_%H%M%S}{out.suffix}")
            out.rename(archive)
            print(f"Kohort lama ({len(old)} encounter) diarsipkan ke {archive.name}.")
            print("Encounter itu MASIH ADA di database. Hapus dengan:")
            print(f"  python tools/cleanup_encounters.py --cohort {archive} --apply")
            print("Atau pakai --append untuk menambah ke kohort lama, bukan menggantinya.\n")

    offset = len(existing)
    identities = make_identities(count, settings.synthetic_prefix, run_id, offset=offset)
    created: list[dict] = []
    failed = 0

    async with make_client(timeout_s=900) as client:
        for start in range(0, count, batch):
            chunk = identities[start:start + batch]
            payload = build_new_encounters_request(
                chunk, settings.location_id, settings.dpjp_id, admission, settings.synthetic_prefix
            )
            t0 = time.perf_counter()
            res = await post_json(client, settings.url_new, payload, settings.headers, settings.auth,
                                  encounters=len(chunk))
            dt = time.perf_counter() - t0

            if res.ok:
                created.extend(chunk)
                print(f"  [{len(created):>5}/{count}] batch {len(chunk):>3} encounter  "
                      f"{payload_bytes(payload)/1024:>7.0f} KB  {dt:>6.1f}s")
            else:
                failed += len(chunk)
                print(f"  GAGAL batch {start}: HTTP {res.status_code} {res.error}")
                if not created:
                    sys.exit(
                        "\nBatch pertama gagal — hentikan di sini.\n"
                        "Penyebab paling sering: master data lokasi/praktisi inline ditolak.\n"
                        "Ambil id asli dari GET /integrations/v2/prerequisites lalu isi "
                        "LOCATION_ID dan DPJP_ID di .env."
                    )

    if not created:
        sys.exit("Tidak ada encounter yang berhasil dibuat.")

    total = existing + created
    Cohort(total).save(out, meta={
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "api_base_url": settings.api_base_url,
        "org_label": settings.org_label,
        "admission_type": admission,
        "requested": count,
        "created": len(created),
        "failed": failed,
        "carried_over": len(existing),
        "total": len(total),
    })

    print(f"\nKohort tersimpan: {out}")
    if existing:
        print(f"  sebelumnya : {len(existing)}")
    print(f"  baru       : {len(created)}")
    print(f"  gagal      : {failed}")
    print(f"  TOTAL      : {len(total)} encounter siap dipakai")
    print(f"\nIdentifier baru berawalan '{settings.synthetic_prefix}-{run_id}-'.")
    print("Kohort ini bisa dipakai ULANG untuk semua test — tidak perlu seed ulang tiap kali.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=600,
                    help="jumlah encounter. Sediakan berlebih: tiap request test memakai potongan yang berbeda.")
    ap.add_argument("--batch", type=int, default=50, help="encounter per request /encounters/new")
    ap.add_argument("--admission", default="ranap", choices=["ranap", "rajal", "igd"],
                    help="ranap = kasus terberat, paling representatif untuk batas atas")
    ap.add_argument("--append", action="store_true",
                    help="tambahkan ke kohort yang sudah ada, bukan menggantinya. "
                         "Dipakai kalau test butuh lebih banyak encounter.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    settings.require_api()
    run_id = f"{datetime.now():%m%d%H%M}"
    out = Path(args.out) if args.out else settings.ensure_results_dir() / "cohort.json"

    settings.warn_if_prod()
    print(f"Target   : {settings.url_new}")
    print(f"Kredensial: {settings.credential_fingerprint}")
    if settings.org_label:
        print(f"Org      : {settings.org_label}")
    print(f"Encounter: {args.count} ({args.admission})\n")
    asyncio.run(run(args.count, args.batch, args.admission, out, run_id, args.append))


if __name__ == "__main__":
    main()
