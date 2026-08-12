#!/usr/bin/env python3
"""Grafik kedalaman antrean terhadap waktu — output utama pengujian ini.

    python analyze/report.py results/t3_saturation_20260812_143000_queue.csv

Yang dibaca dari grafik:
  - garis datar  -> laju kedatangan masih tertangani
  - garis naik   -> sudah lewat kapasitas, tiap menit utangnya bertambah
  - lag tertua   -> berapa lama encounter paling sial menunggu giliran
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.csvlog import linreg  # noqa: E402


def load(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def _f(row: dict, key: str, default: float = 0.0) -> float:
    v = row.get(key, "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def text_summary(rows: list[dict]) -> None:
    phases: dict[str, list[dict]] = {}
    for r in rows:
        phases.setdefault(r.get("phase", "-"), []).append(r)

    print(f"{'FASE':<16} {'DURASI':>8} {'ANTREAN AWAL':>13} {'AKHIR':>8} {'KEMIRINGAN':>13} "
          f"{'LAG MAKS':>10} {'STATUS':>10}")
    print("-" * 84)
    for phase, rs in phases.items():
        if len(rs) < 2:
            continue
        dur = _f(rs[-1], "elapsed_s") - _f(rs[0], "elapsed_s")
        xs = [_f(r, "elapsed_s") / 60.0 for r in rs]
        ys = [_f(r, "depth") for r in rs]
        slope, _, _ = linreg(xs, ys)
        lag = max(_f(r, "oldest_wait_s") for r in rs)
        verdict = "menumpuk" if slope > 0.5 else ("mengosong" if slope < -0.5 else "stabil")
        print(f"{phase:<16} {dur:>7.0f}s {ys[0]:>13.0f} {ys[-1]:>8.0f} {slope:>+11.2f}/m "
              f"{lag/60:>9.1f}m {verdict:>10}")


def plot(rows: list[dict], out: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib belum terpasang — lewati grafik: pip install matplotlib)")
        return False

    t = [_f(r, "elapsed_s") / 60.0 for r in rows]
    depth = [_f(r, "depth") for r in rows]
    busy = [_f(r, "workers_busy") for r in rows]
    lag = [_f(r, "oldest_wait_s") / 60.0 for r in rows]
    down = [_f(r, "downstream_depth") for r in rows]

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)

    axes[0].plot(t, depth, lw=1.6, label="antrean parsing")
    if any(down):
        axes[0].plot(t, down, lw=1.2, ls="--", alpha=0.7, label="antrean analisis (hilir)")
    axes[0].set_ylabel("job menunggu")
    axes[0].set_title("Kedalaman antrean — datar berarti stabil, naik berarti lewat kapasitas")
    axes[0].legend(loc="upper left")
    axes[0].grid(alpha=0.3)

    axes[1].plot(t, lag, lw=1.6, color="tab:orange")
    axes[1].set_ylabel("lag tertua (menit)")
    axes[1].set_title("Umur job terdepan — perkiraan waktu tunggu yang dirasakan user")
    axes[1].grid(alpha=0.3)

    axes[2].plot(t, busy, lw=1.6, color="tab:green")
    axes[2].set_ylabel("worker sibuk")
    axes[2].set_xlabel("menit sejak mulai")
    axes[2].set_title("Utilisasi worker — mentok di jumlah replika berarti worker jadi bottleneck")
    axes[2].grid(alpha=0.3)

    # Garis batas antar fase.
    last = None
    for r in rows:
        p = r.get("phase", "-")
        if p != last:
            x = _f(r, "elapsed_s") / 60.0
            for ax in axes:
                ax.axvline(x, color="grey", ls=":", alpha=0.5)
            axes[0].text(x, 0.97, f" {p}", transform=axes[0].get_xaxis_transform(),
                         fontsize=7, rotation=90, va="top", ha="left", color="grey")
            last = p

    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print(f"\nGrafik: {out}")
    return True


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"Tidak ditemukan: {path}")

    rows = load(path)
    if not rows:
        sys.exit("CSV kosong.")

    print(f"Sumber: {path}  ({len(rows)} sampel)\n")
    text_summary(rows)
    plot(rows, path.with_suffix(".png"))


if __name__ == "__main__":
    main()
