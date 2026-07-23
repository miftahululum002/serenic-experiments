import psycopg2

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, ORGANIZATION_ID
from utils.logger import get_logger

log = get_logger("query")

TABLE = {
    "additionalDetail": "encounter_additional_details",
    "customClinicalImpression": "custom_clinical_impression",
    "customBilling": "custom_billing",
    "encounter": "encounter",
}


def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def find_encounter_by_noregistrasi(noregistrasi):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM encounter
        WHERE id_in_organization = %s AND managing_organization = %s
        """,
        (noregistrasi, ORGANIZATION_ID),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def count_billing_by_encounter_id(encounter_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM custom_billing WHERE encounter_id = %s",
        (encounter_id,),
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count


def find_encounters_by_noregistrasi_list(noregistrasi_list):
    if not noregistrasi_list:
        return []

    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(noregistrasi_list))
    cur.execute(
        f"""
        SELECT id, id_in_organization FROM encounter
        WHERE id_in_organization IN ({placeholders})
        AND managing_organization = %s
        """,
        (*noregistrasi_list, ORGANIZATION_ID),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": row[0], "noregistrasi": row[1]} for row in rows]


def extract_encounter_ids(noregistrasi_list):
    encounters = find_encounters_by_noregistrasi_list(noregistrasi_list)
    return [enc["id"] for enc in encounters if enc.get("id")]


def delete_encounters_by_noregistrasi(noregistrasi_list):
    encounters = find_encounters_by_noregistrasi_list(noregistrasi_list)
    encounter_ids = [enc["id"] for enc in encounters if enc.get("id")]

    if not encounter_ids:
        return []

    conn = get_connection()
    deleted = []
    # indexIni = 0
    # for eid in encounter_ids:
    for idx, eid in enumerate(encounter_ids):
        log.info(f"{idx+1}/{len(encounter_ids)} [{eid}]")
        delete_encounter_by_id(conn, eid)
        deleted.append(eid)
        # ++indexIni
    conn.close()
    return deleted


def delete_encounter_by_id(connection, encounter_id: str) -> bool:
    """
    Menghapus seluruh data terkait encounter berdasarkan encounter_id
    menggunakan psycopg2 di dalam satu transaksi database.
    """

    log.info(f"========= Start deleting encounter with id: {encounter_id} =========")
    try:
        connection.autocommit = False

        with connection.cursor() as cur:

            def run(label: str, query: str, params: tuple = None):
                cur.execute(query, params or (encounter_id,))
                row_count = cur.rowcount
                log.info(f"[{encounter_id}] Affected rows from {label}: {row_count}")

            # 1. Verification Comments & Checkbox
            run(
                "encounter_verification_comments",
                "DELETE FROM encounter_verification_comments WHERE encounter_id = %s",
            )
            run(
                "encounter_verification_checkbox",
                "DELETE FROM encounter_verification_checkbox WHERE encounter_id = %s",
            )

            # 2. Administrative Validation & Details
            run(
                "encounter_administrative_validation",
                "DELETE FROM encounter_administrative_validation WHERE encounter_id = %s",
            )
            run(
                "encounter_additional_details",
                f"DELETE FROM {TABLE['additionalDetail']} WHERE encounter_id = %s",
            )
            run(
                "encounter_inacbgs_recommendation",
                "DELETE FROM encounter_inacbgs_recommendation WHERE encounter_id = %s",
            )

            # 3. Encounter Codes & Validation
            run(
                "encounter_code_history",
                "DELETE FROM encounter_code_history WHERE encounter_id = %s",
            )
            run("encounter_code", "DELETE FROM encounter_code WHERE encounter_id = %s")
            run(
                "encounter_code_group",
                "DELETE FROM encounter_code_group WHERE encounter_id = %s",
            )
            run(
                "encounter_code_group_checklist_validation_history",
                "DELETE FROM encounter_code_group_checklist_validation_history WHERE encounter_id = %s",
            )
            run(
                "encounter_code_group_checklist_validation",
                "DELETE FROM encounter_code_group_checklist_validation WHERE encounter_id = %s",
            )
            run(
                "encounter_code_recommendation",
                "DELETE FROM encounter_code_recommendation WHERE encounter_id = %s",
            )

            # 4. Update relasi readmisi / fragmentasi
            run(
                "encounter_administrative_validation (update relasi)",
                """
                UPDATE encounter_administrative_validation 
                SET readmisi_previous_encounter_id = NULL, 
                    fragmentasi_previous_encounter_id = NULL 
                WHERE readmisi_previous_encounter_id = %s 
                   OR fragmentasi_previous_encounter_id = %s
                """,
                (encounter_id, encounter_id),
            )

            # 5. Custom Medications & Observations
            run(
                "custom_medication_dispense",
                "DELETE FROM custom_medication_dispense WHERE encounter_id = %s",
            )
            run(
                "custom_medication_request",
                "DELETE FROM custom_medication_request WHERE encounter_id = %s",
            )
            run(
                "custom_medication",
                "DELETE FROM custom_medication WHERE encounter_id = %s",
            )
            run(
                "custom_medication_statement",
                "DELETE FROM custom_medication_statement WHERE encounter_id = %s",
            )
            run(
                "custom_observation",
                "DELETE FROM custom_observation WHERE encounter_id = %s",
            )
            run(
                "custom_procedure",
                "DELETE FROM custom_procedure WHERE encounter_id = %s",
            )
            run(
                "custom_diagnostic_report",
                "DELETE FROM custom_diagnostic_report WHERE encounter_id = %s",
            )

            # 6. Custom Composition Section (Subquery)
            run(
                "custom_composition_section",
                """
                DELETE FROM custom_composition_section 
                WHERE composition_id IN (
                    SELECT id FROM custom_composition WHERE encounter_id = %s
                )
                """,
            )

            # 7. Other Custom Resources & Billing
            run(
                "custom_composition",
                "DELETE FROM custom_composition WHERE encounter_id = %s",
            )
            run(
                "custom_service_request",
                "DELETE FROM custom_service_request WHERE encounter_id = %s",
            )
            run(
                "custom_clinical_impression",
                f"DELETE FROM {TABLE['customClinicalImpression']} WHERE encounter_id = %s",
            )
            run(
                "custom_billing",
                f"DELETE FROM {TABLE['customBilling']} WHERE encounter_id = %s",
            )
            run(
                "codex_manual_analysis",
                "DELETE FROM codex_manual_analysis WHERE encounter_id = %s",
            )

            # 8. Clear SEP Diagnosis & Delete Diagnosis
            run(
                "encounter (clear sep_diagnosis)",
                f"UPDATE {TABLE['encounter']} SET sep_diagnosis = NULL WHERE id = %s",
            )
            run(
                "custom_diagnosis",
                "DELETE FROM custom_diagnosis WHERE encounter_id = %s",
            )

            # 9. History & Main Table
            run(
                "encounter_location_history",
                "DELETE FROM encounter_location_history WHERE encounter_id = %s",
            )
            run(
                "encounter_main_practitioner_history",
                "DELETE FROM encounter_main_practitioner_history WHERE encounter_id = %s",
            )
            run(
                "custom_raw_emr_data",
                "DELETE FROM custom_raw_emr_data WHERE encounter_id = %s",
            )
            run(
                "patient_chart_variable_history",
                "DELETE FROM patient_chart_variable_history WHERE encounter_id = %s",
            )
            run(
                "patient_chart_variable",
                "DELETE FROM patient_chart_variable WHERE encounter_id = %s",
            )

            # 10. Custom Family Member History (by patient_id)
            cur.execute("SELECT patient FROM encounter WHERE id = %s", (encounter_id,))
            row = cur.fetchone()
            if row and row[0]:
                patient_id = row[0]
                cur.execute(
                    "DELETE FROM custom_family_member_history WHERE patient_id = %s",
                    (patient_id,),
                )
                row_count = cur.rowcount
                log.info(
                    f"[{encounter_id}] Affected rows from custom_family_member_history: {row_count}"
                )

            # Hapus record utama encounter
            run("encounter", f"DELETE FROM {TABLE['encounter']} WHERE id = %s")

        connection.commit()
        log.info(
            f"========= Completed deletion for encounter_id: {encounter_id} ========="
        )
        return True

    except Exception as err:
        connection.rollback()
        log.error(f"Deletion failed, rollback executed: {err}")
        raise err
