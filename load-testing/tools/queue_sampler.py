#!/usr/bin/env python3
"""Pantau kedalaman antrean secara terus-menerus ke CSV + tampilan live.

Berguna dijalankan di terminal terpisah selama test, atau untuk mengamati
beban produksi sehari-hari agar tahu bentuk trafik yang sebenarnya sebelum
menentukan target pengujian.

    python tools/queue_sampler.py --interval 2 --out results/live.csv
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from lib.csvlog import fmt_dur  # noqa: E402
from lib.rq_probe import RQProbe  # noqa: E402
from lib.sampler import QueueSampler  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0, help="detik antar sampel")
    ap.add_argument("--out", default=None, help="path CSV output")
    ap.add_argument("--duration", type=float, default=0, help="berhenti setelah N detik (0 = tanpa batas)")
    args = ap.parse_args()

    settings.require_redis()
    out = Path(args.out) if args.out else settings.ensure_results_dir() / f"queue_{datetime.now():%Y%m%d_%H%M%S}.csv"

    probe = RQProbe(settings.redis_url, settings.parsing_queue)
    sampler = QueueSampler(
        probe, out, args.interval,
        downstream=[settings.analysis_coordinator_queue, settings.analysis_queue],
    )

    print(f"Antrean : {settings.parsing_queue}")
    print(f"Output  : {out}")
    print("Ctrl-C untuk berhenti\n")
    print(f"{'WAKTU':<10} {'MENUNGGU':>9} {'JALAN':>6} {'SELESAI':>8} {'GAGAL':>6} {'LAG TERTUA':>11} {'HILIR':>7}")

    sampler.start()
    t0 = time.time()
    try:
        while True:
            time.sleep(args.interval)
            if not sampler.rows:
                continue
            r = sampler.rows[-1]
            lag = float(r["oldest_wait_s"]) if r["oldest_wait_s"] != "" else None
            print(
                f"{datetime.now():%H:%M:%S}  {r['depth']:>9} {r['started']:>6} {r['finished']:>8} "
                f"{r['failed']:>6} {fmt_dur(lag) if lag is not None else '-':>11} {r['downstream_depth']:>7}"
            )
            if args.duration and time.time() - t0 >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        sampler.stop()
        print(f"\nTersimpan: {out}")


if __name__ == "__main__":
    main()
