"""Penghapusan encounter langsung ke database.

Urutan penghapusan disalin dari
`experiments/testing-ingestor/utils/query.py:delete_encounter_by_id` — urutan
itu menghormati foreign key, jadi jangan diacak. Menambah tabel baru harus
disisipkan pada posisi yang benar, bukan ditempel di akhir.

Satu encounter = satu transaksi. Kalau ada satu yang gagal, hanya encounter itu
yang di-rollback; sisanya tetap terhapus.
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

import psycopg2

# (label, SQL). Placeholder %s diisi encounter_id, kecuali yang punya
# jumlah parameter berbeda — ditangani terpisah di bawah.
DELETE_STEPS: list[tuple[str, str]] = [
    # 1. Verification
    ("encounter_verification_comments",
     "DELETE FROM encounter_verification_comments WHERE encounter_id = %s"),
    ("encounter_verification_checkbox",
     "DELETE FROM encounter_verification_checkbox WHERE encounter_id = %s"),

    # 2. Administrative validation & details
    ("encounter_administrative_validation",
     "DELETE FROM encounter_administrative_validation WHERE encounter_id = %s"),
    ("encounter_additional_details",
     "DELETE FROM encounter_additional_details WHERE encounter_id = %s"),
    ("encounter_inacbgs_recommendation",
     "DELETE FROM encounter_inacbgs_recommendation WHERE encounter_id = %s"),

    # 3. Codes & validation
    ("encounter_code_history",
     "DELETE FROM encounter_code_history WHERE encounter_id = %s"),
    ("encounter_code",
     "DELETE FROM encounter_code WHERE encounter_id = %s"),
    ("encounter_code_group",
     "DELETE FROM encounter_code_group WHERE encounter_id = %s"),
    ("encounter_code_group_checklist_validation_history",
     "DELETE FROM encounter_code_group_checklist_validation_history WHERE encounter_id = %s"),
    ("encounter_code_group_checklist_validation",
     "DELETE FROM encounter_code_group_checklist_validation WHERE encounter_id = %s"),
    ("encounter_code_recommendation",
     "DELETE FROM encounter_code_recommendation WHERE encounter_id = %s"),

    # 5. Medications & observations
    ("custom_medication_dispense",
     "DELETE FROM custom_medication_dispense WHERE encounter_id = %s"),
    ("custom_medication_request",
     "DELETE FROM custom_medication_request WHERE encounter_id = %s"),
    ("custom_medication",
     "DELETE FROM custom_medication WHERE encounter_id = %s"),
    ("custom_medication_statement",
     "DELETE FROM custom_medication_statement WHERE encounter_id = %s"),
    ("custom_observation",
     "DELETE FROM custom_observation WHERE encounter_id = %s"),
    ("custom_procedure",
     "DELETE FROM custom_procedure WHERE encounter_id = %s"),
    ("custom_diagnostic_report",
     "DELETE FROM custom_diagnostic_report WHERE encounter_id = %s"),

    # 6. Composition section lewat subquery (harus sebelum custom_composition)
    ("custom_composition_section",
     "DELETE FROM custom_composition_section WHERE composition_id IN "
     "(SELECT id FROM custom_composition WHERE encounter_id = %s)"),

    # 7. Resource lain & billing
    ("custom_composition",
     "DELETE FROM custom_composition WHERE encounter_id = %s"),
    ("custom_service_request",
     "DELETE FROM custom_service_request WHERE encounter_id = %s"),
    ("custom_clinical_impression",
     "DELETE FROM custom_clinical_impression WHERE encounter_id = %s"),
    ("custom_billing",
     "DELETE FROM custom_billing WHERE encounter_id = %s"),
    ("codex_manual_analysis",
     "DELETE FROM codex_manual_analysis WHERE encounter_id = %s"),

    # 8. Lepas sep_diagnosis sebelum menghapus diagnosis
    ("encounter (clear sep_diagnosis)",
     "UPDATE encounter SET sep_diagnosis = NULL WHERE id = %s"),
    ("custom_diagnosis",
     "DELETE FROM custom_diagnosis WHERE encounter_id = %s"),

    # 9. History & tabel pendukung
    ("encounter_location_history",
     "DELETE FROM encounter_location_history WHERE encounter_id = %s"),
    ("encounter_main_practitioner_history",
     "DELETE FROM encounter_main_practitioner_history WHERE encounter_id = %s"),
    ("custom_raw_emr_data",
     "DELETE FROM custom_raw_emr_data WHERE encounter_id = %s"),
    ("patient_chart_variable_history",
     "DELETE FROM patient_chart_variable_history WHERE encounter_id = %s"),
    ("patient_chart_variable",
     "DELETE FROM patient_chart_variable WHERE encounter_id = %s"),
]


def connect(host: str, port: int, dbname: str, user: str, password: str):
    return psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)


def find_encounters(conn, organization_id: str, *,
                    noregistrasi: Optional[Iterable[str]] = None,
                    prefix: str = "") -> list[dict]:
    """Cari encounter dalam SATU organisasi, berdasarkan daftar noregistrasi
    atau awalan identifier.

    Salah satu dari `noregistrasi` / `prefix` wajib diisi. Tanpa itu fungsi
    menolak jalan — supaya tidak mungkin tidak sengaja memilih seluruh
    encounter milik organisasi.
    """
    ids = list(noregistrasi or [])
    if not ids and not prefix:
        raise ValueError("butuh daftar noregistrasi atau prefix; menolak memilih semua encounter")

    with conn.cursor() as cur:
        if ids:
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"SELECT id, id_in_organization FROM encounter "
                f"WHERE id_in_organization IN ({placeholders}) AND managing_organization = %s",
                (*ids, organization_id),
            )
        else:
            cur.execute(
                "SELECT id, id_in_organization FROM encounter "
                "WHERE id_in_organization LIKE %s AND managing_organization = %s",
                (f"{prefix}%", organization_id),
            )
        return [{"id": r[0], "noregistrasi": r[1]} for r in cur.fetchall()]


def delete_encounter(conn, encounter_id: str,
                     on_step: Optional[Callable[[str, int], None]] = None) -> dict[str, int]:
    """Hapus satu encounter beserta seluruh turunannya, dalam satu transaksi."""
    affected: dict[str, int] = {}
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for label, sql in DELETE_STEPS:
                cur.execute(sql, (encounter_id,))
                if cur.rowcount:
                    affected[label] = cur.rowcount
                if on_step:
                    on_step(label, cur.rowcount)

            # 4. Lepas relasi readmisi/fragmentasi yang menunjuk ke encounter ini
            #    dari encounter LAIN — dua parameter, jadi di luar DELETE_STEPS.
            cur.execute(
                "UPDATE encounter_administrative_validation "
                "SET readmisi_previous_encounter_id = NULL, "
                "    fragmentasi_previous_encounter_id = NULL "
                "WHERE readmisi_previous_encounter_id = %s "
                "   OR fragmentasi_previous_encounter_id = %s",
                (encounter_id, encounter_id),
            )
            if cur.rowcount:
                affected["encounter_administrative_validation (lepas relasi)"] = cur.rowcount

            # 10. Riwayat keluarga terikat ke pasien, bukan ke encounter.
            cur.execute("SELECT patient FROM encounter WHERE id = %s", (encounter_id,))
            row = cur.fetchone()
            if row and row[0]:
                cur.execute("DELETE FROM custom_family_member_history WHERE patient_id = %s",
                            (row[0],))
                if cur.rowcount:
                    affected["custom_family_member_history"] = cur.rowcount

            cur.execute("DELETE FROM encounter WHERE id = %s", (encounter_id,))
            affected["encounter"] = cur.rowcount

        conn.commit()
        return affected
    except Exception:
        conn.rollback()
        raise
