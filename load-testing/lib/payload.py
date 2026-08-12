"""Pembangun payload sintetis untuk endpoint /encounters/new dan /encounters/update.

Strategi: pakai payload asli sebagai template lalu diklon, bukan disintesis dari
schema. Alasannya beban kerja worker parsing ditentukan oleh isi `sources` yang
riil (jumlah observasi, CPPT, billing, dsb) — payload sintetis dari schema akan
terlalu ringan dan hasil ukurnya menyesatkan.

Setiap klon di-remap id-nya supaya tidak bentrok antar-encounter, dengan tetap
menjaga referensi internal (observation.procedure_id -> procedure.id) valid.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

# Subtree yang id-nya menunjuk ke master data (lokasi, praktisi, tim organisasi).
# Kalau ikut di-remap, foreign key-nya putus dan ingestor akan menolak/skip.
PRESERVE_SUBTREE_KEYS = {
    "location",
    "lokasi",
    "practitioner",
    "praktisi",
    "dpjp",
    "dpjp_id",
    "organizational_team",
    "tim_organisasi",
    "performer",
    "performer_id",
    "requester",
    "result_interpreter_id",
}

# Field bertipe `str | PractitionerModel` di resource_models.py. Nilainya boleh
# berupa id praktisi (string) atau objek praktisi lengkap.
PRACTITIONER_REF_KEYS = ("performer_id", "result_interpreter_id", "requester_id")

# Ubah referensi praktisi jadi objek inline. Bisa dimatikan lewat env.
PERFORMER_AS_OBJECT = os.getenv("PERFORMER_AS_OBJECT", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
PERFORMER_PROFESSION = os.getenv("PERFORMER_PROFESSION", "Dokter")


def _iso(dt: datetime) -> str:
    """Format timestamp persis seperti trafik produksi: UTC, presisi detik,
    berakhiran Z — contoh `2026-08-03T18:30:20Z`.

    Formatnya diambil dari payload asli (`start_timestamp`, `created_at`, dan
    `effective_datetime` di dalam sources semuanya memakai bentuk ini). Jangan
    diubah jadi offset `+00:00` atau tanpa zona: payload test harus seidentik
    mungkin dengan yang dikirim HIS.
    """
    return (
        dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        + "Z"
    )


# --------------------------------------------------------------------------
# Remap id
# --------------------------------------------------------------------------
def _collect_ids(node: Any, out: set[str], preserve: bool = False) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            child_preserve = preserve or k in PRESERVE_SUBTREE_KEYS
            if k == "id" and isinstance(v, str) and not preserve:
                out.add(v)
            else:
                _collect_ids(v, out, child_preserve)
    elif isinstance(node, list):
        for item in node:
            _collect_ids(item, out, preserve)


def _apply_map(node: Any, mapping: dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: _apply_map(v, mapping) for k, v in node.items()}
    if isinstance(node, list):
        return [_apply_map(v, mapping) for v in node]
    if isinstance(node, str) and node in mapping:
        return mapping[node]
    return node


def remap_ids(node: Any, suffix: str) -> Any:
    """Ganti semua id resource dalam subtree dengan id baru yang unik.

    Referensi internal (procedure_id, service_request_id, ...) ikut terganti
    karena penggantian dilakukan berdasarkan nilai, bukan nama field.
    """
    ids: set[str] = set()
    _collect_ids(node, ids)
    if not ids:
        return node
    mapping = {old: f"{old[:40]}-{suffix}" for old in ids}
    return _apply_map(node, mapping)


# --------------------------------------------------------------------------
# Template
# --------------------------------------------------------------------------
class Template:
    """Satu encounter update asli, siap diklon."""

    def __init__(self, path: Path):
        self.path = path
        raw = json.loads(Path(path).read_text())
        updates = raw.get("updates") or []
        if not updates:
            raise ValueError(f"{path}: tidak ada 'updates' di template payload")
        self.unit: dict = updates[0]
        self.dspm: dict = (
            raw.get("data_source_processing_mode")
            or self.unit.get("data_source_processing_mode")
            or {}
        )

    @property
    def source_summary(self) -> dict[str, int]:
        return {
            k: len(v) if isinstance(v, list) else 1
            for k, v in (self.unit.get("sources") or {}).items()
        }

    @property
    def bytes_per_encounter(self) -> int:
        return len(json.dumps(self.unit).encode())


def _practitioner_object(ref: str, profession: str) -> dict:
    """Bentuk objek PractitionerModel dari sebuah id praktisi.

    `name` sengaja diisi dengan id-nya sendiri, bukan nama karangan. Alasannya
    ada di ingestor (`_ingest_practitioner`, ingestor.py:865):

        new_name = prac.name if (prac.name and prac.name != prac.id) else existing.name

    Artinya kalau `name == id`, nama praktisi yang sudah ada di database TIDAK
    ditimpa. Mengirim nama karangan seperti "Load Test Practitioner" akan
    menimpa nama dokter sungguhan di master data — terlihat oleh pengguna di
    webapp, dan tidak bisa dikembalikan dari sisi harness.
    """
    return {"id": ref, "name": ref, "profession": profession, "meta": {}}


def objectify_practitioner_refs(
    node: Any,
    profession: str = PERFORMER_PROFESSION,
    keys: Sequence[str] = PRACTITIONER_REF_KEYS,
) -> Any:
    """Ubah referensi praktisi berbentuk string menjadi objek inline.

    Nilai yang sudah berupa objek dibiarkan apa adanya, begitu juga None.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in keys and isinstance(v, str) and v:
                out[k] = _practitioner_object(v, profession)
            else:
                out[k] = objectify_practitioner_refs(v, profession, keys)
        return out
    if isinstance(node, list):
        return [objectify_practitioner_refs(v, profession, keys) for v in node]
    return node


def _scale_weight(sources: dict, weight: int) -> dict:
    """Perbanyak isi tiap data source `weight` kali, dengan id yang di-remap."""
    if weight <= 1:
        return sources
    out = {}
    for key, units in sources.items():
        if not isinstance(units, list) or not units:
            out[key] = units
            continue
        grown = list(units)
        for rep in range(weight - 1):
            for idx, unit in enumerate(units):
                grown.append(
                    remap_ids(copy.deepcopy(unit), f"w{rep}{idx}{uuid.uuid4().hex[:6]}")
                )
        out[key] = grown
    return out


# --------------------------------------------------------------------------
# Encounter identity
# --------------------------------------------------------------------------
class Cohort:
    """Kumpulan encounter sintetis yang sudah ada di DB target."""

    def __init__(self, items: list[dict]):
        self.items = items

    @classmethod
    def load(cls, path: Path) -> "Cohort":
        data = json.loads(Path(path).read_text())
        return cls(data["encounters"])

    def save(self, path: Path, meta: dict | None = None) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps({"meta": meta or {}, "encounters": self.items}, indent=2)
        )

    def slice(self, start: int, count: int) -> list[dict]:
        """Potongan encounter yang saling lepas (disjoint).

        Penting: ingestor memasang advisory lock per encounter, jadi dua request
        yang memuat encounter sama akan saling menunggu dan mengacaukan
        pengukuran konkurensi.
        """
        if start + count > len(self.items):
            raise IndexError(
                f"cohort cuma punya {len(self.items)} encounter, diminta {start}..{start + count}. "
                "Seed lagi dengan --count lebih besar."
            )
        return self.items[start : start + count]

    def __len__(self) -> int:
        return len(self.items)


def make_identities(
    count: int, prefix: str, run_id: str, offset: int = 0
) -> list[dict]:
    return [
        {
            "norec": f"{prefix}-{run_id}-R{i + offset:05d}",
            "noregistrasi": f"{prefix}-{run_id}-N{i + offset:05d}",
            "norm": f"{prefix}-{run_id}-M{i + offset:05d}",
        }
        for i in range(count)
    ]


# --------------------------------------------------------------------------
# Request builder
# --------------------------------------------------------------------------
def build_new_encounters_request(
    identities: Sequence[dict],
    location_id: str = "",
    dpjp_id: str = "",
    admission_type: str = "ranap",
    prefix: str = "LT",
) -> dict:
    """Payload POST /encounters/new — menyiapkan encounter supaya /update punya kerjaan."""
    now = datetime.now(timezone.utc)

    # Kalau master data tidak diberikan, kirim objek inline supaya self-contained.
    loc: Any = location_id or {
        "id": f"{prefix}-LOC-01",
        "organizational_team_id": f"{prefix}-TEAM-01",
        "name": "Load Test Ward",
        "organizational_team_name": "Load Test Team",
    }
    dpjp: Any = dpjp_id or {
        "id": f"{prefix}-DPJP-01",
        "name": "Load Test Practitioner",
        "profession": "Dokter",  # PractitionerProfession.DOCTOR
    }

    items = []
    for i, ident in enumerate(identities):
        items.append(
            {
                "norec": ident["norec"],
                "noregistrasi": ident["noregistrasi"],
                "norm": ident["norm"],
                "sep_data": {
                    "nosep": f"{ident['noregistrasi']}-SEP",
                    "kelasBPJS": 3,
                    "payor": "bpjs",
                },
                "tglregistrasi": _iso(now - timedelta(hours=6)),
                "admissionType": admission_type,
                "dpjp_id": dpjp,
                "location_id": loc,
                "patientInfo": {
                    "dateOfBirth": (now - timedelta(days=365 * 40 + i))
                    .date()
                    .isoformat(),
                    "gender": "laki-laki" if i % 2 == 0 else "perempuan",
                    "name": f"Load Test Patient {i}",
                },
            }
        )

    return {"timestamp": _iso(now), "newEncounters": items}


def build_update_request(
    template: Template,
    identities: Iterable[dict],
    weight: int = 1,
    force_ingest_completed: bool = True,
    performer_as_object: bool = PERFORMER_AS_OBJECT,
    performer_profession: str = PERFORMER_PROFESSION,
) -> dict:
    """Payload POST /encounters/update berisi N encounter.

    `force_ingest_completed=True` penting untuk pengukuran berulang: tanpa itu,
    encounter yang sudah dianggap stale hanya di-ingest bagian billing-nya saja
    dan beban kerja jadi jauh lebih ringan dari run pertama.

    `performer_as_object=True` mengirim referensi praktisi sebagai objek inline
    (`str | PractitionerModel`). Ini mengaktifkan jalur `_ingest_practitioner`
    di worker — kerja tambahan yang ikut terukur, dan memang begitu adanya.
    """
    now = datetime.now(timezone.utc)
    updates = []

    for ident in identities:
        unit = copy.deepcopy(template.unit)
        unit["sources"] = _scale_weight(unit.get("sources") or {}, weight)
        unit = remap_ids(unit, uuid.uuid4().hex[:10])
        # WAJIB setelah remap_ids: kalau dibalik, field `id` pada objek praktisi
        # yang baru dibuat ikut di-remap dan referensinya ke master data putus.
        if performer_as_object:
            unit = objectify_practitioner_refs(unit, performer_profession)
        unit["norec"] = ident["norec"]
        unit["noregistrasi"] = ident["noregistrasi"]
        unit["created_at"] = _iso(now - timedelta(hours=6))
        unit["updated_at"] = _iso(now)
        unit.pop("location", None)  # jangan pindahkan lokasi; master data dipegang /new
        updates.append(unit)

    return {
        "start_timestamp": _iso(now - timedelta(hours=12)),
        "end_timestamp": _iso(now),
        "data_source_processing_mode": template.dspm or {},
        "updates": updates,
        "force_ingest_completed": force_ingest_completed,
        "sending_mode": "batch",
    }


def payload_bytes(payload: dict) -> int:
    return len(json.dumps(payload).encode())
