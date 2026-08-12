"""Sampler kedalaman antrean yang jalan di thread terpisah.

Grafik kedalaman antrean terhadap waktu adalah output utama seluruh pengujian
ini — bukan tabel RPS. Dari kemiringannya kita tahu apakah sistem stabil
(datar) atau sedang menumpuk (naik).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional, Sequence

from lib.csvlog import CsvLog, linreg
from lib.rq_probe import RQProbe

FIELDS = (
    "ts", "elapsed_s", "phase", "depth", "started", "finished", "failed",
    "deferred", "workers_total", "workers_busy", "oldest_wait_s",
    "downstream_depth",
)


class QueueSampler:
    def __init__(
        self,
        probe: RQProbe,
        out_path: Path,
        interval_s: float = 1.0,
        downstream: Optional[Sequence[str]] = None,
    ):
        self.probe = probe
        self.interval = interval_s
        self.log = CsvLog(out_path, FIELDS)
        self.downstream = [q for q in (downstream or []) if q]
        self.phase = "-"
        self.rows: list[dict] = []
        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None
        self._t0 = 0.0

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> "QueueSampler":
        self._t0 = time.time()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=self.interval * 3)
        self.log.close()

    def mark(self, phase: str) -> None:
        """Tandai fase berjalan supaya baris CSV bisa dikelompokkan saat analisis."""
        self.phase = phase

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # --- internal ----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                s = self.probe.snapshot()
                down = 0
                for q in self.downstream:
                    down += int(self.probe.r.llen(f"rq:queue:{q}"))
                row = {
                    "ts": round(s.ts, 3),
                    "elapsed_s": round(s.ts - self._t0, 3),
                    "phase": self.phase,
                    "depth": s.depth,
                    "started": s.started,
                    "finished": s.finished,
                    "failed": s.failed,
                    "deferred": s.deferred,
                    "workers_total": s.workers_total,
                    "workers_busy": s.workers_busy,
                    "oldest_wait_s": round(s.oldest_wait_seconds, 2) if s.oldest_wait_seconds is not None else "",
                    "downstream_depth": down,
                }
                self.rows.append(row)
                self.log.write(**row)
            except Exception as e:  # jangan pernah menjatuhkan test karena sampler
                print(f"[sampler] error: {type(e).__name__}: {e}")
            self._stop.wait(self.interval)

    # --- analisis ----------------------------------------------------------
    def slope_for_phase(self, phase: str) -> tuple[float, float, int]:
        """Kemiringan kedalaman antrean (job/menit) selama satu fase.

        ~0  -> stabil, laju kedatangan masih tertangani
        >0  -> menumpuk, sudah lewat kapasitas
        """
        rows = [r for r in self.rows if r["phase"] == phase]
        if len(rows) < 3:
            return float("nan"), float("nan"), len(rows)
        xs = [r["elapsed_s"] / 60.0 for r in rows]
        ys = [float(r["depth"]) for r in rows]
        slope, _, r2 = linreg(xs, ys)
        return slope, r2, len(rows)

    def avg_busy_for_phase(self, phase: str) -> float:
        """Rata-rata worker sibuk selama satu fase.

        Kalau angkanya mentok di jumlah replika, bottleneck-nya memang jumlah
        worker. Kalau jauh di bawahnya padahal antrean menumpuk, worker sedang
        menunggu sesuatu yang lain (lock encounter, connection pool, disk) dan
        menambah replika tidak akan menolong.
        """
        rows = [r for r in self.rows if r["phase"] == phase]
        if not rows:
            return float("nan")
        return sum(float(r["workers_busy"]) for r in rows) / len(rows)

    def completed_during(self, phase: str) -> int:
        """Jumlah job yang selesai selama satu fase (dari registry finished)."""
        rows = [r for r in self.rows if r["phase"] == phase]
        if len(rows) < 2:
            return 0
        return max(0, int(rows[-1]["finished"]) - int(rows[0]["finished"]))
