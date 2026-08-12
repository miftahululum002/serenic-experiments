"""Arsip request dan response yang dipertukarkan dengan API.

Layout mengikuti konvensi server (`save_request_to_file` di route v2):

    payload/{org_id}/{yyyymmdd}/{nama_endpoint}_{timestamp}_{seq}.json
    response/{org_id}/{yyyymmdd}/{nama_endpoint}_{timestamp}_{seq}.json

Nomor `seq` sama untuk request dan response yang berpasangan, jadi keduanya
bisa ditelusuri bolak-balik. File response juga menyimpan nama file request-nya
secara eksplisit.

Penulisan dilakukan di thread terpisah dan TIDAK PERNAH masuk jalur waktu
request. Kalau file 53 KB–6 MB ditulis sinkron di sekitar POST, angka latency
yang diukur ikut memuat biaya disk lokal dan seluruh pengukuran jadi tidak sah.

Konsekuensinya: kalau proses berhenti paksa, beberapa file terakhir bisa belum
sempat tertulis. Itu pertukaran yang disengaja — ketepatan ukur lebih penting
daripada kelengkapan arsip.
"""
from __future__ import annotations

import atexit
import json
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

# Nama file untuk endpoint yang dikenal. Selebihnya diturunkan dari path.
_ENDPOINT_NAMES = {
    "encounters/new": "new_encounters",
    "encounters/update": "update_encounters",
    "encounters/completed": "completed_encounters",
    "encounters/inacbg": "inacbg_only_encounters",
    "encounters/exists": "check_encounters_exists",
    "prerequisites": "prerequisites",
    "health_check": "health_check",
}


def endpoint_name(url: str, api_prefix: str = "") -> str:
    """Turunkan nama file dari URL endpoint.

    /integrations/v2/encounters/update -> update_encounters
    """
    path = url.split("://", 1)[-1]
    path = path[path.find("/"):] if "/" in path else "/"
    path = path.split("?", 1)[0]
    if api_prefix and api_prefix in path:
        path = path.split(api_prefix, 1)[1]
    tail = path.strip("/")
    if tail in _ENDPOINT_NAMES:
        return _ENDPOINT_NAMES[tail]
    parts = [p for p in tail.split("/") if p]
    if not parts:
        return "request"
    # Konvensi server: segmen terakhir di depan (encounters/new -> new_encounters)
    return "_".join(reversed(parts))[:80] or "request"


class _Budget:
    """Pembatas ukuran per jenis arsip, supaya disk tidak penuh diam-diam."""

    def __init__(self, label: str, max_mb: float):
        self.label = label
        self.max_bytes = max_mb * 1e6
        self.used = 0
        self.files = 0
        self.capped = False

    def allow(self, n: int, seq: int) -> bool:
        if self.capped:
            return False
        if self.used + n > self.max_bytes:
            self.capped = True
            mb = self.max_bytes / 1e6
            shown = f"{mb:,.0f}" if mb >= 10 else f"{mb:,.1f}"
            print(f"\n[arsip] batas {shown} MB untuk {self.label} tercapai setelah {seq} request — "
                  f"pengarsipan {self.label} dihentikan, test tetap jalan.")
            return False
        self.used += n
        return True


class RequestArchive:
    def __init__(
        self,
        payload_root: Path,
        response_root: Path,
        org_id: str,
        api_prefix: str = "",
        save_requests: bool = True,
        save_responses: bool = True,
        payload_max_mb: float = 2000.0,
        response_max_mb: float = 500.0,
        queue_size: int = 512,
    ):
        self.payload_root = Path(payload_root)
        self.response_root = Path(response_root)
        self.org_id = org_id or "unknown-org"
        self.api_prefix = api_prefix
        self.save_requests = save_requests
        self.save_responses = save_responses
        self.req_budget = _Budget("payload", payload_max_mb)
        self.resp_budget = _Budget("response", response_max_mb)

        self.dropped = 0
        self._seq = 0
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if save_requests or save_responses:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            atexit.register(self.close)

    @property
    def enabled(self) -> bool:
        return self.save_requests or self.save_responses

    # --- API ---------------------------------------------------------------
    def next_seq(self) -> int:
        """Nomor urut selalu maju, walaupun salah satu jenis arsip dimatikan,
        supaya penomoran request dan response tetap sejajar."""
        with self._lock:
            self._seq += 1
            return self._seq

    def save_request(self, url: str, body: bytes, seq: int, stamp: Optional[datetime] = None) -> str:
        """Antre payload untuk ditulis. Mengembalikan nama file (untuk dirujuk
        dari file response). Tidak pernah memblokir pemanggil."""
        name = self._filename(url, seq, stamp)
        if not self.save_requests:
            return name
        with self._lock:
            if not self.req_budget.allow(len(body), seq):
                return name
        self._enqueue(self._dir(self.payload_root, stamp) / name, body, self.req_budget)
        return name

    def save_response(
        self,
        url: str,
        seq: int,
        *,
        status_code: int,
        ok: bool,
        latency_s: float,
        body_text: str,
        request_file: str,
        error: str = "",
        stamp: Optional[datetime] = None,
    ) -> None:
        """Simpan response beserta status dan waktunya.

        Body mentah saja tidak cukup: saat beban tinggi, yang paling sering
        dicari justru status code dan pesan error — dan itu hilang kalau hanya
        body yang disimpan.
        """
        if not self.save_responses:
            return
        try:
            parsed = json.loads(body_text) if body_text else None
        except (ValueError, TypeError):
            parsed = None

        doc = {
            "endpoint": endpoint_name(url, self.api_prefix),
            "url": url,
            "seq": seq,
            "request_file": request_file,
            "status_code": status_code,
            "ok": ok,
            "latency_ms": round(latency_s * 1000, 1),
            "received_at": (stamp or datetime.now()).isoformat(timespec="milliseconds"),
        }
        if error:
            doc["error"] = error
        if parsed is not None:
            doc["body"] = parsed
        else:
            doc["body_text"] = body_text[:100_000]

        data = json.dumps(doc, indent=2, default=str).encode()
        with self._lock:
            if not self.resp_budget.allow(len(data), seq):
                return
        self._enqueue(self._dir(self.response_root, stamp) / self._filename(url, seq, stamp),
                      data, self.resp_budget)

    def close(self) -> None:
        if not self._thread or self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(timeout=30)
        parts = []
        if self.req_budget.files:
            parts.append(f"{self.req_budget.files} payload")
        if self.resp_budget.files:
            parts.append(f"{self.resp_budget.files} response")
        if parts:
            msg = f"[arsip] {', '.join(parts)} tersimpan untuk org {self.org_id}"
            if self.dropped:
                msg += f", {self.dropped} dilewati (antrean tulis penuh)"
            print(msg)

    # --- internal ----------------------------------------------------------
    def _dir(self, root: Path, stamp: Optional[datetime]) -> Path:
        return root / self.org_id / (stamp or datetime.now()).strftime("%Y%m%d")

    def _filename(self, url: str, seq: int, stamp: Optional[datetime]) -> str:
        now = stamp or datetime.now()
        # Milidetik dan nomor urut wajib: beban test mengirim banyak request
        # dalam detik yang sama, dan tanpa ini file saling menimpa.
        return (f"{endpoint_name(url, self.api_prefix)}_{now.strftime('%Y%m%d_%H%M%S')}"
                f"_{now.microsecond // 1000:03d}_{seq:06d}.json")

    def _enqueue(self, path: Path, data: bytes, budget: _Budget) -> None:
        try:
            self._q.put_nowait((path, data, budget))
        except queue.Full:
            # Lebih baik kehilangan arsip daripada memperlambat pengiriman beban.
            with self._lock:
                self.dropped += 1
                budget.used -= len(data)

    def _loop(self) -> None:
        while True:
            try:
                path, data, budget = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                with self._lock:
                    budget.files += 1
            except Exception as e:
                print(f"[arsip] gagal menulis {path.name}: {type(e).__name__}: {e}")
            finally:
                self._q.task_done()


_ARCHIVE: Optional[RequestArchive] = None


def get_archive() -> RequestArchive:
    """Arsip bersama, dibuat sekali dari konfigurasi profil aktif."""
    global _ARCHIVE
    if _ARCHIVE is None:
        from config import settings
        _ARCHIVE = RequestArchive(
            payload_root=settings.payload_dir,
            response_root=settings.response_dir,
            org_id=settings.payload_org_dir,
            api_prefix=settings.api_v2_prefix,
            save_requests=settings.save_payloads,
            save_responses=settings.save_responses,
            payload_max_mb=settings.payload_max_mb,
            response_max_mb=settings.response_max_mb,
        )
    return _ARCHIVE
