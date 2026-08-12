"""Pembacaan state RQ langsung dari Redis.

Sengaja membaca field hash mentah, BUKAN lewat `rq.job.Job.fetch()`, karena
Job.fetch akan meng-unpickle argumen job — dan argumennya berisi model pydantic
dari `app.*` yang tidak tersedia di harness ini. Membaca hash mentah membuat
harness bebas dependensi terhadap kode aplikasi.

Layout key RQ (verifikasi dengan `python tools/inspect_redis.py`):
    rq:queue:<name>      LIST  — job id yang menunggu, head = paling lama
    rq:wip:<name>        ZSET  — StartedJobRegistry, anggotanya "job_id:execution_id"
    rq:finished:<name>   ZSET  — job id polos
    rq:failed:<name>     ZSET  — job id polos
    rq:deferred:<name>   ZSET  — job id polos
    rq:job:<id>          HASH  — created_at/enqueued_at/started_at/ended_at/status
    rq:worker:<name>     HASH  — field current_job berisi job id polos

Perhatikan `rq:wip:`: sejak RQ 2.x isinya composite key, bukan job id polos.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

import redis

# Field hash job yang aman dibaca sebagai teks (sisanya berisi pickle).
_TEXT_FIELDS = (
    "status", "origin", "created_at", "enqueued_at", "started_at", "ended_at",
    "worker_name", "ended_reason", "failure_ttl", "timeout",
)

_TS_FORMATS = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")


def parse_ts(raw) -> Optional[datetime]:
    """Parse timestamp RQ (utcformat) menjadi datetime aware UTC."""
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:  # fallback: ISO 8601 apa pun
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class JobTiming:
    job_id: str
    status: str
    created_at: Optional[datetime]
    enqueued_at: Optional[datetime]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]

    @property
    def wait_seconds(self) -> Optional[float]:
        """Lama mengantre: enqueued -> started."""
        if self.enqueued_at and self.started_at:
            return (self.started_at - self.enqueued_at).total_seconds()
        return None

    @property
    def service_seconds(self) -> Optional[float]:
        """Lama dikerjakan worker: started -> ended."""
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds()
        return None

    @property
    def total_seconds(self) -> Optional[float]:
        """End-to-end: enqueued -> ended. Ini yang dirasakan user."""
        if self.enqueued_at and self.ended_at:
            return (self.ended_at - self.enqueued_at).total_seconds()
        return None

    @property
    def is_done(self) -> bool:
        return self.status in ("finished", "failed", "stopped", "canceled")


@dataclass
class QueueSnapshot:
    ts: float
    depth: int
    started: int
    finished: int
    failed: int
    deferred: int
    workers_total: int
    workers_busy: int
    oldest_wait_seconds: Optional[float]


class RQProbe:
    def __init__(self, redis_url: str, queue: str):
        self.queue = queue
        self.r = redis.from_url(redis_url)

    # --- key helpers -------------------------------------------------------
    def _qkey(self) -> str:
        return f"rq:queue:{self.queue}"

    def _rkey(self, registry: str) -> str:
        return f"rq:{registry}:{self.queue}"

    # --- pembacaan dasar ---------------------------------------------------
    def depth(self) -> int:
        return int(self.r.llen(self._qkey()))

    def registry_size(self, registry: str) -> int:
        return int(self.r.zcard(self._rkey(registry)))

    def queued_ids(self, start: int = 0, end: int = -1) -> list[str]:
        return [b.decode() for b in self.r.lrange(self._qkey(), start, end)]

    def registry_ids(self, registry: str, limit: int = 200) -> list[str]:
        """Job id dari sebuah registry, sudah bersih dari execution id.

        PENTING: sejak RQ 2.x, `rq:wip:<queue>` (StartedJobRegistry) menyimpan
        composite key `"{job_id}:{execution_id}"`, bukan job id polos. Registry
        lain (finished/failed/deferred) tetap job id polos.

        Kalau composite key ini dipakai apa adanya, `rq:job:<composite>` tidak
        akan ada dan job yang sedang berjalan terlihat seperti hilang — job
        selesai di server tapi harness menunggu selamanya.

        Aturan pemisahannya menyalin `rq.utils.parse_composite_key`.
        """
        entries = [b.decode() for b in self.r.zrange(self._rkey(registry), 0, limit - 1)]
        return [e.split(":", 1)[0] for e in entries]

    def started_executions(self, limit: int = 200) -> list[tuple[str, str]]:
        """Pasangan (job_id, execution_id) dari StartedJobRegistry."""
        out = []
        for b in self.r.zrange(self._rkey("wip"), 0, limit - 1):
            entry = b.decode()
            job_id, _, execution_id = entry.partition(":")
            out.append((job_id, execution_id))
        return out

    def job(self, job_id: str) -> Optional[JobTiming]:
        vals = self.r.hmget(f"rq:job:{job_id}", _TEXT_FIELDS)
        raw = {k: (v.decode("utf-8", "replace") if v is not None else None) for k, v in zip(_TEXT_FIELDS, vals)}
        if raw["status"] is None and raw["created_at"] is None:
            return None  # job sudah expired / tidak ada
        return JobTiming(
            job_id=job_id,
            status=raw["status"] or "unknown",
            created_at=parse_ts(raw["created_at"]),
            enqueued_at=parse_ts(raw["enqueued_at"]),
            started_at=parse_ts(raw["started_at"]),
            ended_at=parse_ts(raw["ended_at"]),
        )

    # --- worker ------------------------------------------------------------
    def workers(self) -> list[dict]:
        names = self.r.smembers(f"rq:workers:{self.queue}") or self.r.smembers("rq:workers")
        out = []
        for n in names:
            key = n.decode() if isinstance(n, bytes) else n
            if not key.startswith("rq:worker:"):
                key = f"rq:worker:{key}"
            state, current_job, birth = self.r.hmget(key, ("state", "current_job", "birth"))
            out.append({
                "name": key.removeprefix("rq:worker:"),
                "state": state.decode() if state else "?",
                "current_job": current_job.decode() if current_job else None,
                "birth": parse_ts(birth),
            })
        return out

    # --- agregat -----------------------------------------------------------
    def known_ids(self) -> set[str]:
        """Semua job id yang saat ini terlihat — dipakai untuk mendeteksi job baru."""
        ids = set(self.queued_ids())
        for reg in ("wip", "finished", "failed", "deferred"):
            ids.update(self.registry_ids(reg, limit=500))
        return ids

    def oldest_wait_seconds(self) -> Optional[float]:
        """Umur job terdepan di antrean — metrik lag paling jujur."""
        head = self.queued_ids(0, 0)
        if not head:
            return None
        j = self.job(head[0])
        if not j or not j.enqueued_at:
            return None
        return (now_utc() - j.enqueued_at).total_seconds()

    def snapshot(self) -> QueueSnapshot:
        ws = self.workers()
        return QueueSnapshot(
            ts=time.time(),
            depth=self.depth(),
            started=self.registry_size("wip"),
            finished=self.registry_size("finished"),
            failed=self.registry_size("failed"),
            deferred=self.registry_size("deferred"),
            workers_total=len(ws),
            workers_busy=sum(1 for w in ws if w["state"] == "busy"),
            oldest_wait_seconds=self.oldest_wait_seconds(),
        )

    # --- menunggu ----------------------------------------------------------
    def wait_for_new_job(
        self,
        before: set[str],
        timeout: float = 60.0,
        interval: float = 0.25,
        on_tick: Optional[Callable[[float, QueueSnapshot], None]] = None,
    ) -> Optional[str]:
        """Tunggu sampai muncul job id yang belum ada di `before`."""
        start = time.time()
        deadline = start + timeout
        last_tick = 0.0
        while time.time() < deadline:
            new = self.known_ids() - before
            if new:
                return sorted(new)[0]
            elapsed = time.time() - start
            if on_tick and elapsed - last_tick >= 2.0:
                last_tick = elapsed
                on_tick(elapsed, self.snapshot())
            time.sleep(interval)
        return None

    def wait_until_done(
        self,
        job_id: str,
        timeout: float,
        interval: float = 1.0,
        on_tick: Optional[Callable[[float, JobTiming | None, QueueSnapshot], None]] = None,
    ) -> Optional[JobTiming]:
        """Tunggu job selesai (finished/failed). None kalau timeout."""
        start = time.time()
        deadline = start + timeout
        last_tick = 0.0
        seen = False
        while time.time() < deadline:
            j = self.job(job_id)
            if j and j.is_done and j.ended_at:
                return j
            if j is not None:
                seen = True
            elif seen:
                # Hash sempat ada lalu hilang: job selesai dan kedaluwarsa
                # (result_ttl). Menunggu lebih lama tidak ada gunanya.
                return None
            elapsed = time.time() - start
            if on_tick and elapsed - last_tick >= 2.0:
                last_tick = elapsed
                on_tick(elapsed, j, self.snapshot())
            time.sleep(interval)
        return self.job(job_id)

    def collect_job_timings(
        self,
        job_ids: Iterable[str],
        timeout: float,
        interval: float = 1.0,
        on_tick: Optional[Callable[[float, int, int, QueueSnapshot], None]] = None,
    ) -> tuple[dict[str, JobTiming], set[str], set[str]]:
        """Pantau banyak job sekaligus, panen timing begitu masing-masing selesai.

        Mengembalikan (selesai, hilang, belum_selesai).

        Ini WAJIB dipakai untuk banyak job, bukan `wait_until_done` berurutan.
        Sebabnya RQ menghapus hash job sukses setelah `result_ttl` (default 500
        detik). Kalau job ditunggu satu per satu menurut urutan id, job yang
        selesai lebih awal bisa keburu terhapus sebelum sempat dibaca — datanya
        hilang, dan menunggunya menghabiskan `timeout` penuh.
        """
        pending = {j for j in job_ids}
        total = len(pending)
        done: dict[str, JobTiming] = {}
        vanished: set[str] = set()
        start = time.time()
        deadline = start + timeout
        last_tick = 0.0

        while pending and time.time() < deadline:
            for jid in list(pending):
                j = self.job(jid)
                if j is None:
                    # Hash sudah tidak ada: job selesai lalu kedaluwarsa
                    # sebelum sempat dibaca.
                    vanished.add(jid)
                    pending.discard(jid)
                elif j.is_done and j.ended_at:
                    done[jid] = j
                    pending.discard(jid)
            if pending:
                elapsed = time.time() - start
                if on_tick and elapsed - last_tick >= 2.0:
                    last_tick = elapsed
                    on_tick(elapsed, len(done), total, self.snapshot())
                time.sleep(interval)

        return done, vanished, pending

    def wait_until_drained(
        self,
        timeout: float,
        interval: float = 2.0,
        quiet_for: float = 5.0,
        on_tick: Optional[Callable[[float, QueueSnapshot], None]] = None,
    ) -> bool:
        """Tunggu antrean kosong DAN tidak ada job berjalan, stabil selama `quiet_for`."""
        start = time.time()
        deadline = start + timeout
        clear_since = None
        last_tick = 0.0
        while time.time() < deadline:
            s = self.snapshot()
            if s.depth == 0 and s.started == 0:
                clear_since = clear_since or time.time()
                if time.time() - clear_since >= quiet_for:
                    return True
            else:
                clear_since = None
            elapsed = time.time() - start
            if on_tick and elapsed - last_tick >= 2.0:
                last_tick = elapsed
                on_tick(elapsed, s)
            time.sleep(interval)
        return False


def multi_depth(redis_url: str, queues: Iterable[str]) -> dict[str, int]:
    """Kedalaman beberapa antrean sekaligus — untuk memantau cascade ke hilir."""
    r = redis.from_url(redis_url)
    return {q: int(r.llen(f"rq:queue:{q}")) for q in queues if q}
