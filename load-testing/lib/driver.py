"""Driver beban open-loop.

Perbedaan penting dengan Locust default (closed-loop): di sini request
dijadwalkan pada waktu absolut dan tetap dikirim walaupun request sebelumnya
belum selesai. Kalau server melambat, beban TIDAK ikut melambat — persis
seperti kiriman dari HIS rumah sakit. Closed-loop akan menyembunyikan
penumpukan (coordinated omission) dan membuat sistem terlihat sehat padahal
antreannya tumbuh.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import httpx


@dataclass
class RequestResult:
    idx: int
    scheduled_ts: float
    submit_ts: float
    latency_s: float
    status_code: int
    ok: bool
    request_bytes: int
    encounters: int
    request_id: str = ""
    error: str = ""
    # Selisih antara waktu jadwal dan waktu kirim sebenarnya. Kalau ini
    # membengkak, load generator-nya yang jadi bottleneck, bukan server.
    schedule_lag_s: float = 0.0


@dataclass
class LoadStats:
    results: list[RequestResult] = field(default_factory=list)
    max_inflight: int = 0

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def encounters_sent(self) -> int:
        return sum(r.encounters for r in self.results if r.ok)

    @property
    def max_schedule_lag(self) -> float:
        return max((r.schedule_lag_s for r in self.results), default=0.0)


def make_client(timeout_s: float = 600.0, verify: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=30.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        verify=verify,
    )


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    headers: dict,
    auth=None,
    *,
    idx: int = 0,
    scheduled_ts: float = 0.0,
    encounters: int = 0,
    body: Optional[bytes] = None,
) -> RequestResult:
    """Satu POST. `body` boleh diisi bytes yang sudah diserialisasi sebelumnya
    supaya biaya json.dumps tidak masuk ke jalur waktu kirim."""
    import json as _json

    data = body if body is not None else _json.dumps(payload).encode()

    # Diarsipkan lewat antrean di thread lain — sengaja SEBELUM stopwatch
    # dinyalakan, dan tidak menyentuh disk di jalur ini.
    from lib.payload_store import get_archive
    archive = get_archive()
    seq = archive.next_seq()
    req_file = archive.save_request(url, data, seq)

    submit = time.time()
    t0 = time.perf_counter()
    try:
        resp = await client.post(url, content=data, headers=headers, auth=auth)
        latency = time.perf_counter() - t0
        ok = 200 <= resp.status_code < 300
        text = resp.text
        req_id = ""
        if ok:
            try:
                req_id = (resp.json() or {}).get("requestId", "")
            except Exception:
                pass
        archive.save_response(url, seq, status_code=resp.status_code, ok=ok, latency_s=latency,
                              body_text=text, request_file=req_file)
        return RequestResult(
            idx=idx, scheduled_ts=scheduled_ts or submit, submit_ts=submit, latency_s=latency,
            status_code=resp.status_code, ok=ok, request_bytes=len(data), encounters=encounters,
            request_id=req_id, error="" if ok else text[:300],
            schedule_lag_s=max(0.0, submit - scheduled_ts) if scheduled_ts else 0.0,
        )
    except Exception as e:  # timeout, connection reset, dst
        latency = time.perf_counter() - t0
        err = f"{type(e).__name__}: {e}"
        # Kegagalan transport justru yang paling perlu diarsipkan: status_code=0
        # menandai tidak ada respons sama sekali, bukan server yang membalas error.
        archive.save_response(url, seq, status_code=0, ok=False, latency_s=latency,
                              body_text="", request_file=req_file, error=err)
        return RequestResult(
            idx=idx, scheduled_ts=scheduled_ts or submit, submit_ts=submit,
            latency_s=latency, status_code=0, ok=False,
            request_bytes=len(data), encounters=encounters, error=err[:300],
            schedule_lag_s=max(0.0, submit - scheduled_ts) if scheduled_ts else 0.0,
        )


def arrival_offsets(rate_per_min: float, duration_s: float, poisson: bool, seed: int = 1) -> list[float]:
    """Waktu kedatangan relatif (detik) untuk laju tertentu.

    poisson=True memberi inter-arrival eksponensial (lebih realistis, ada burst
    alami). poisson=False memberi jarak tetap (lebih mudah dibaca saat mencari
    titik jenuh).
    """
    if rate_per_min <= 0:
        return []
    mean_gap = 60.0 / rate_per_min
    rng = random.Random(seed)
    out: list[float] = []
    t = 0.0
    while t < duration_s:
        out.append(t)
        t += rng.expovariate(1.0 / mean_gap) if poisson else mean_gap
    return out


async def run_open_loop(
    *,
    offsets: list[float],
    fire: Callable[[int, float], Awaitable[RequestResult]],
    on_result: Optional[Callable[[RequestResult], None]] = None,
    start_at: Optional[float] = None,
) -> LoadStats:
    """Kirim request pada `offsets` detik setelah start, tanpa menunggu respons."""
    stats = LoadStats()
    t_start = start_at or time.time()
    inflight = 0
    tasks: list[asyncio.Task] = []

    async def _run(i: int, target_ts: float):
        nonlocal inflight
        inflight += 1
        stats.max_inflight = max(stats.max_inflight, inflight)
        try:
            res = await fire(i, target_ts)
            stats.results.append(res)
            if on_result:
                on_result(res)
        finally:
            inflight -= 1

    for i, off in enumerate(offsets):
        target = t_start + off
        delay = target - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        tasks.append(asyncio.create_task(_run(i, target)))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    stats.results.sort(key=lambda r: r.idx)
    return stats


def summarize(stats: LoadStats, window_s: float) -> dict:
    from lib.csvlog import percentile

    lat = [r.latency_s for r in stats.results if r.ok]
    return {
        "requests": len(stats.results),
        "ok": stats.ok_count,
        "failed": stats.fail_count,
        "encounters_sent": stats.encounters_sent,
        "achieved_rpm": len(stats.results) / (window_s / 60.0) if window_s > 0 else float("nan"),
        "http_p50_s": percentile(lat, 50),
        "http_p95_s": percentile(lat, 95),
        "http_max_s": max(lat) if lat else float("nan"),
        "max_inflight": stats.max_inflight,
        "max_schedule_lag_s": stats.max_schedule_lag,
    }


def warn_if_client_bound(stats: LoadStats, threshold_s: float = 1.0) -> Optional[str]:
    """Deteksi load generator yang tidak sanggup mengejar jadwal."""
    lag = stats.max_schedule_lag
    if lag > threshold_s:
        return (
            f"PERINGATAN: schedule lag maksimum {lag:.1f}s — generator beban tertinggal dari jadwal. "
            "Angka throughput di bawah ini adalah batas CLIENT, bukan batas server. "
            "Turunkan ukuran payload, atau jalankan driver dari mesin yang lebih dekat/kuat."
        )
    return None
