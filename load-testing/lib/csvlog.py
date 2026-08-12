"""Penulis CSV sederhana + util statistik, tanpa dependensi berat."""
import csv
import math
from pathlib import Path
from typing import Iterable, Sequence


class CsvLog:
    """CSV yang di-flush per baris supaya aman kalau test di-Ctrl-C di tengah jalan."""

    def __init__(self, path: Path, fieldnames: Sequence[str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh = path.open("w", newline="")
        self._w = csv.DictWriter(self._fh, fieldnames=list(fieldnames))
        self._w.writeheader()
        self._fh.flush()

    def write(self, **row) -> None:
        self._w.writerow(row)
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def percentile(values: Iterable[float], p: float) -> float:
    """Persentil dengan interpolasi linear. p dalam 0..100."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def linreg(xs: Sequence[float], ys: Sequence[float]):
    """Regresi linear least-squares. Mengembalikan (slope, intercept, r2)."""
    n = len(xs)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return float("nan"), float("nan"), float("nan")
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return slope, intercept, r2


def fmt_dur(seconds: float) -> str:
    if seconds is None or (isinstance(seconds, float) and math.isnan(seconds)):
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.2f}j"
