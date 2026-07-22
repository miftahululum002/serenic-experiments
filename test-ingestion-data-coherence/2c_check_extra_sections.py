import json
from collections import defaultdict
from sqlalchemy import create_engine, text
from constant import BASE_PROJECT_PATH

# ===== Configuration =====
CONFIG_FILE_PATH = BASE_PROJECT_PATH  # "/home/aldo_serenic_ai/_experiments/2026-02-04 - Test Ingestion Data Coherence/config.env"

# Input: JSON file path (use only one file for comparison)
JSON_FILE_PATH = "1_djamil/3/1_update_encounters.json"

# Target parameters
TARGET_NOREGISTRASI = "08525283-bfd0-1583-5ad2-7e2c9f8e196d"
TARGET_SECTION_TABLE_NAME = "custom_composition_section"  # The table to focus on


def load_config(config_path: str) -> dict:
    config = {}
    with open(config_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config


CONFIG = load_config(CONFIG_FILE_PATH)
CONNECTION_STRING = CONFIG.get("CONNECTION_STRING", "")
MANAGING_ORGANIZATION_ID = CONFIG.get("MANAGING_ORGANIZATION_ID", "")


def get_encounter_id_from_noregistrasi(engine, noregistrasi: str) -> str | None:
    query = text(
        "SELECT id FROM encounter WHERE id_in_organization = :noreg AND managing_organization = :managing_org LIMIT 1"
    )
    with engine.connect() as conn:
        result = conn.execute(
            query, {"noreg": noregistrasi, "managing_org": MANAGING_ORGANIZATION_ID}
        ).fetchone()
        return result[0] if result else None


def find_update_by_noregistrasi(updates: list, noregistrasi: str) -> dict | None:
    for update in updates:
        if update.get("noregistrasi") == noregistrasi:
            return update
    return None


def format_payload_data(data: dict, table_name: str) -> str:
    """Format payload data to show only important fields."""
    output = []

    if table_name == "custom_observation":
        # Show concept (key) and value
        concept = data.get("concept", {}).get("text") or data.get("concept", {}).get(
            "code", "N/A"
        )
        output.append(f"concept: {concept}")

        value_type = data.get("value_type")
        if value_type == "quantity" and data.get("value"):
            unit = data.get("value_unit", "")
            output.append(f"value: {data['value']} {unit}".strip())
        elif value_type == "string" and data.get("value"):
            output.append(f"value: {data['value']}")
        elif value_type == "integer" and data.get("value") is not None:
            output.append(f"value: {data['value']}")
        elif value_type == "float" and data.get("value") is not None:
            output.append(f"value: {data['value']}")
        elif value_type == "boolean" and data.get("value") is not None:
            output.append(f"value: {data['value']}")

        if data.get("category"):
            output.append(f"category: {data['category']}")
        if data.get("effective_datetime"):
            output.append(f"effective_datetime: {data['effective_datetime']}")

    elif table_name == "custom_diagnosis":
        diagnosis = data.get("diagnosis_text") or data.get("code", "N/A")
        output.append(f"diagnosis: {diagnosis}")
        if data.get("is_primary"):
            output.append(f"is_primary: {data['is_primary']}")

    elif table_name == "custom_clinical_impression":
        if data.get("text"):
            output.append(f"text: {data['text'][:100]}...")
        if data.get("category"):
            output.append(f"category: {data['category']}")

    elif table_name == "custom_procedure":
        procedure = data.get("procedure_text") or data.get("code", "N/A")
        output.append(f"procedure: {procedure}")

    elif table_name == "custom_diagnostic_report":
        concept = data.get("concept", {}).get("text") or data.get("concept", {}).get(
            "code", "N/A"
        )
        output.append(f"concept: {concept}")
        if data.get("result"):
            output.append(f"result: {data['result'][:100]}...")

    elif table_name == "custom_medication":
        output.append(f"medication: {data.get('medication_name', 'N/A')}")
        if data.get("dosage_quantity"):
            unit = data.get("dosage_unit", "")
            output.append(f"dosage: {data['dosage_quantity']} {unit}".strip())

    return "\n  ".join(output) if output else "(no key data)"


def format_resource_data(data: dict, table_name: str) -> str:
    """Format resource data to show only important fields."""
    output = []

    if table_name == "custom_observation":
        # Show concept (key) and value
        concept = data.get("concept_text") or data.get("concept_code", "N/A")
        output.append(f"concept: {concept}")

        value_type = data.get("value_type")
        if value_type:
            # Find the actual value based on type
            if value_type == "quantity" and data.get("value_quantity_value"):
                unit = data.get("value_quantity_unit", "")
                output.append(f"value: {data['value_quantity_value']} {unit}".strip())
            elif value_type == "string" and data.get("value_string"):
                output.append(f"value: {data['value_string']}")
            elif value_type == "integer" and data.get("value_integer") is not None:
                output.append(f"value: {data['value_integer']}")
            elif value_type == "float" and data.get("value_float") is not None:
                output.append(f"value: {data['value_float']}")
            elif value_type == "boolean" and data.get("value_boolean") is not None:
                output.append(f"value: {data['value_boolean']}")

        if data.get("category"):
            output.append(f"category: {data['category']}")
        if data.get("effective_time"):
            output.append(f"effective_time: {data['effective_time']}")

    elif table_name == "custom_diagnosis":
        diagnosis = data.get("diagnosis_text") or data.get("code", "N/A")
        output.append(f"diagnosis: {diagnosis}")
        if data.get("is_primary"):
            output.append(f"is_primary: {data['is_primary']}")

    elif table_name == "custom_clinical_impression":
        if data.get("text"):
            output.append(f"text: {data['text'][:100]}...")
        if data.get("category"):
            output.append(f"category: {data['category']}")

    elif table_name == "custom_procedure":
        procedure = data.get("procedure_text") or data.get("code", "N/A")
        output.append(f"procedure: {procedure}")

    elif table_name == "custom_diagnostic_report":
        concept = data.get("concept_text") or data.get("concept_code", "N/A")
        output.append(f"concept: {concept}")
        if data.get("result"):
            output.append(f"result: {data['result'][:100]}...")

    elif table_name == "custom_medication":
        output.append(f"medication: {data.get('medication_name', 'N/A')}")
        if data.get("dosage_quantity"):
            unit = data.get("dosage_unit", "")
            output.append(f"dosage: {data['dosage_quantity']} {unit}".strip())

    return "\n  ".join(output) if output else "(no key data)"


def get_resource_data_from_db(engine, table_name: str, resource_id_in_org: str) -> dict:
    """Get the actual resource data from the database using id_in_organization."""
    # Define columns to fetch for each table type
    columns_map = {
        "custom_observation": [
            "id",
            "id_in_organization",
            "category",
            "concept_code",
            "concept_text",
            "value_type",
            "value_quantity_value",
            "value_quantity_unit",
            "value_string",
            "value_boolean",
            "value_integer",
            "value_float",
            "effective_time",
            "created_time",
        ],
        "custom_clinical_impression": [
            "id",
            "id_in_organization",
            "category",
            "text",
            "effective_time",
            "created_time",
        ],
        "custom_diagnosis": [
            "id",
            "id_in_organization",
            "code",
            "diagnosis_text",
            "is_primary",
            "created_time",
        ],
        "custom_procedure": [
            "id",
            "id_in_organization",
            "code",
            "procedure_text",
            "created_time",
        ],
        "custom_diagnostic_report": [
            "id",
            "id_in_organization",
            "category",
            "concept_code",
            "concept_text",
            "result",
            "effective_time",
            "created_time",
        ],
        "custom_medication": [
            "id",
            "id_in_organization",
            "medication_name",
            "dosage_quantity",
            "dosage_unit",
            "created_time",
        ],
        "custom_service_request": [
            "id",
            "id_in_organization",
            "category",
            "concept_code",
            "concept_text",
            "created_time",
        ],
    }

    columns = columns_map.get(table_name, ["id", "id_in_organization", "created_time"])
    columns_str = ", ".join(columns)

    # Query by id_in_organization (which includes the extended format with ::)
    query = text(
        f"SELECT {columns_str} FROM {table_name} WHERE id_in_organization = :id_in_org"
    )

    with engine.connect() as conn:
        result = conn.execute(query, {"id_in_org": resource_id_in_org}).fetchone()
        if result:
            # Convert to dict
            data = {}
            for idx, col in enumerate(columns):
                data[col] = result[idx]
            return data
    return None


def get_composition_sections_from_db(
    engine, encounter_id: str, table_name: str
) -> list:
    """Get all composition sections from DB for a specific table."""
    query = text(
        """
        SELECT 
            ccs.id as section_id,
            ccs.section_table_name,
            ccs.section_resource_id,
            ccs.section_type,
            ccs.section_sequence_order,
            cc.type as composition_type,
            cc.id as composition_id
        FROM custom_composition_section ccs
        JOIN custom_composition cc ON ccs.composition_id = cc.id
        WHERE cc.encounter_id = :enc_id AND ccs.section_table_name = :table_name AND ccs.managing_organization = :managing_org
        ORDER BY ccs.section_sequence_order
    """
    )

    sections = []
    with engine.connect() as conn:
        results = conn.execute(
            query,
            {
                "enc_id": encounter_id,
                "table_name": table_name,
                "managing_org": MANAGING_ORGANIZATION_ID,
            },
        ).fetchall()
        for row in results:
            sections.append(
                {
                    "section_id": row[0],
                    "table_name": row[1],
                    "resource_id": row[2],
                    "section_type": row[3],
                    "sequence_order": row[4],
                    "composition_type": row[5],
                    "composition_id": row[6],
                }
            )

    # For each section, extract the id_in_organization from section_resource_id
    # The section_resource_id is in format: {id}::{source}::{field}::{index}
    # We need to extract the first part (the actual id_in_organization)
    for section in sections:
        resource_id = section["resource_id"]

        if resource_id:
            # The resource_id IS the full id_in_organization with extended format
            # Extract just the base ID (first part before ::)
            if "::" in resource_id:
                section["resource_id_in_org"] = resource_id.split("::")[0]
            else:
                section["resource_id_in_org"] = resource_id

            # Also store the full ID for querying
            section["resource_full_id_in_org"] = resource_id
        else:
            section["resource_id_in_org"] = None
            section["resource_full_id_in_org"] = None

    return sections


def get_expected_items_for_table(sources: dict, table_name: str) -> list:
    """Get all expected items from payload for a specific table."""
    items = []

    # Map table to fields
    table_to_fields = {
        "custom_observation": [
            ("triase_igd", None, "custom_observation"),
            ("cppt", "observasi", "custom_observation"),
            ("asesmen_awal", "pemeriksaan_fisik", "custom_observation"),
            ("asesmen_awal", "hasil_lab", "custom_observation"),
            ("resume_medis", "pemeriksaan_fisik", "custom_observation"),
            ("resume_medis", "hasil_lab", "custom_observation"),
            ("penunjang_lab", "hasil_lab", "custom_observation"),
            ("prosedur_medis_lain", "hasil_prosedur", "custom_observation"),
        ],
        "custom_clinical_impression": [
            ("cppt", "soap", "custom_clinical_impression"),
            ("asesmen_awal", "keluhan", "custom_clinical_impression"),
            ("asesmen_awal", "riwayat_penyakit_dahulu", "custom_clinical_impression"),
            ("asesmen_awal", "riwayat_penyakit_sekarang", "custom_clinical_impression"),
            ("asesmen_awal", "assessment_text", "custom_clinical_impression"),
            ("resume_medis", "keluhan", "custom_clinical_impression"),
            ("resume_medis", "riwayat_penyakit_dahulu", "custom_clinical_impression"),
            ("resume_medis", "riwayat_penyakit_sekarang", "custom_clinical_impression"),
            ("resume_medis", "resume_text", "custom_clinical_impression"),
        ],
        "custom_diagnosis": [
            ("cppt", "diagnosis", "custom_diagnosis"),
            ("asesmen_awal", "diagnosis", "custom_diagnosis"),
            ("resume_medis", "diagnosis", "custom_diagnosis"),
            ("laporan_operasi", "diagnosis_pra_bedah", "custom_diagnosis"),
            ("laporan_operasi", "diagnosis_pasca_bedah", "custom_diagnosis"),
            ("diagnosis_aktif", None, "custom_diagnosis"),
        ],
        "custom_procedure": [
            ("cppt", "prosedur", "custom_procedure"),
            ("asesmen_awal", "prosedur", "custom_procedure"),
            ("resume_medis", "prosedur", "custom_procedure"),
            ("laporan_operasi", "prosedur_operasi", "custom_procedure"),
        ],
        "custom_diagnostic_report": [
            ("asesmen_awal", "laporan_radiologi", "custom_diagnostic_report"),
            ("asesmen_awal", "laporan_lab", "custom_diagnostic_report"),
            ("resume_medis", "laporan_radiologi", "custom_diagnostic_report"),
            ("resume_medis", "laporan_lab", "custom_diagnostic_report"),
            ("penunjang_lab", "laporan_lab", "custom_diagnostic_report"),
            ("penunjang_radiologi", "laporan_radiologi", "custom_diagnostic_report"),
            ("laporan_operasi", "laporan_operasi", "custom_diagnostic_report"),
            ("prosedur_medis_lain", "laporan_prosedur", "custom_diagnostic_report"),
        ],
        "custom_medication": [
            ("cppt", "obat", "custom_medication"),
            ("resume_medis", "obat", "custom_medication"),
            ("obat", "obat", "custom_medication"),
        ],
        "custom_service_request": [
            ("penunjang_lab", "pemesanan", "custom_service_request"),
            ("penunjang_radiologi", "pemesanan", "custom_service_request"),
            ("laporan_operasi", "pemesanan", "custom_service_request"),
            ("prosedur_medis_lain", "pemesanan", "custom_service_request"),
        ],
        "custom_medication_statement": [
            ("asesmen_awal", "riwayat_penggunaan_obat", "custom_medication_statement"),
            ("resume_medis", "riwayat_penggunaan_obat", "custom_medication_statement"),
        ],
        "custom_family_member_history": [
            (
                "asesmen_awal",
                "riwayat_penyakit_keluarga",
                "custom_family_member_history",
            ),
            (
                "resume_medis",
                "riwayat_penyakit_keluarga",
                "custom_family_member_history",
            ),
        ],
    }

    if table_name not in table_to_fields:
        return items

    for source_name, field_name, _ in table_to_fields[table_name]:
        if source_name not in sources:
            continue

        source_data = sources[source_name]
        if source_data is None:
            continue

        # Direct list (triase_igd, diagnosis_aktif)
        if field_name is None:
            if isinstance(source_data, list):
                for item in source_data:
                    if isinstance(item, dict) and item.get("id"):
                        items.append(
                            {
                                "id": item["id"],
                                "source": source_name,
                                "field": None,
                                "data": item,
                            }
                        )
            continue

        # List of units
        if isinstance(source_data, list):
            for unit_idx, unit in enumerate(source_data):
                if isinstance(unit, dict) and field_name in unit:
                    field_items = unit.get(field_name) or []
                    for item in field_items:
                        if isinstance(item, dict) and item.get("id"):
                            items.append(
                                {
                                    "id": item["id"],
                                    "source": f"{source_name}[{unit_idx}]",
                                    "field": field_name,
                                    "data": item,
                                }
                            )

    return items


def main():
    if not JSON_FILE_PATH:
        print("[ERROR] Set JSON_FILE_PATH at the top of the script.")
        return

    if not CONNECTION_STRING:
        print("[ERROR] CONNECTION_STRING not set in config.")
        return

    # Load JSON file
    print(f"Loading file: {JSON_FILE_PATH}")
    with open(JSON_FILE_PATH, "r") as f:
        data = json.load(f)
    updates = data.get("updates", [])

    # Find the update
    update = find_update_by_noregistrasi(updates, TARGET_NOREGISTRASI)
    if not update:
        print(f"[ERROR] No update found with noregistrasi: {TARGET_NOREGISTRASI}")
        return

    sources = update.get("sources", {})
    norec = update.get("norec")

    print("=" * 80)
    print(f"Noregistrasi: {TARGET_NOREGISTRASI}")
    print(f"Norec: {norec}")
    print(f"Target Table: {TARGET_SECTION_TABLE_NAME}")
    print("=" * 80)

    # Connect to DB
    engine = create_engine(CONNECTION_STRING)

    # Get encounter_id
    encounter_id = get_encounter_id_from_noregistrasi(engine, TARGET_NOREGISTRASI)
    if not encounter_id:
        print(f"[ERROR] No encounter found with noregistrasi: {TARGET_NOREGISTRASI}")
        return

    print(f"Encounter ID: {encounter_id}\n")

    # Get expected items from payload
    expected_items = get_expected_items_for_table(sources, TARGET_SECTION_TABLE_NAME)
    expected_ids = set(item["id"] for item in expected_items)

    # Get actual sections from DB
    db_sections = get_composition_sections_from_db(
        engine, encounter_id, TARGET_SECTION_TABLE_NAME
    )

    # Build expected sections by source
    expected_by_source = defaultdict(list)
    for item in expected_items:
        if item["field"]:
            source_key = f"{item['source'].split('[')[0]}.{item['field']}"
        else:
            source_key = item["source"]
        expected_by_source[source_key].append(item["id"])

    # Build actual sections by composition.type.section_type
    db_by_comp_section = defaultdict(list)
    for section in db_sections:
        key = f"{section['composition_type']}.{section['section_type']}"
        db_by_comp_section[key].append(
            {"resource_id": section["resource_id_in_org"], "section": section}
        )

    # Find mismatches by comparing counts    # Map expected sources to DB composition.section_type
    # This is a heuristic mapping based on common patterns
    source_to_comp_map = {
        "triase_igd": "emergency-triage.triase_igd",
        "asesmen_awal.pemeriksaan_fisik": "initial-assessment.pemeriksaan_fisik",
        "asesmen_awal.hasil_lab": "initial-assessment.hasil_lab",
        "asesmen_awal.keluhan": "initial-assessment.keluhan",
        "asesmen_awal.riwayat_penyakit_dahulu": "initial-assessment.riwayat_penyakit_dahulu",
        "asesmen_awal.riwayat_penyakit_sekarang": "initial-assessment.riwayat_penyakit_sekarang",
        "asesmen_awal.assessment_text": "initial-assessment.assessment_text",
        "asesmen_awal.diagnosis": "initial-assessment.diagnosis",
        "asesmen_awal.prosedur": "initial-assessment.prosedur",
        "asesmen_awal.laporan_lab": "initial-assessment.laporan_lab",
        "asesmen_awal.laporan_radiologi": "initial-assessment.laporan_radiologi",
        "resume_medis.pemeriksaan_fisik": "resume.pemeriksaan_fisik",
        "resume_medis.hasil_lab": "resume.hasil_lab",
        "resume_medis.keluhan": "resume.keluhan",
        "resume_medis.riwayat_penyakit_dahulu": "resume.riwayat_penyakit_dahulu",
        "resume_medis.riwayat_penyakit_sekarang": "resume.riwayat_penyakit_sekarang",
        "resume_medis.resume_text": "resume.resume_text",
        "resume_medis.diagnosis": "resume.diagnosis",
        "resume_medis.prosedur": "resume.prosedur",
        "resume_medis.obat": "resume.obat",
        "resume_medis.laporan_lab": "resume.laporan_lab",
        "resume_medis.laporan_radiologi": "resume.laporan_radiologi",
        "cppt.soap": "soap.soap",
        "cppt.observasi": "soap.observasi",
        "cppt.diagnosis": "soap.diagnosis",
        "cppt.prosedur": "soap.prosedur",
        "cppt.obat": "soap.obat",
        "diagnosis_aktif": "active-diagnosis.diagnosis",
    }

    mismatches = []
    for source_key in sorted(expected_by_source.keys()):
        expected_ids = expected_by_source[source_key]
        expected_count = len(expected_ids)

        # Get mapped DB key
        db_key = source_to_comp_map.get(source_key)
        if not db_key:
            continue

        db_items = db_by_comp_section.get(db_key, [])
        db_count = len(db_items)
        db_ids = [item["resource_id"] for item in db_items if item["resource_id"]]

        if expected_count != db_count:
            difference = db_count - expected_count
            status = "OVER" if difference > 0 else "MISSING"
            mismatches.append(
                {
                    "source": source_key,
                    "db_key": db_key,
                    "expected_count": expected_count,
                    "db_count": db_count,
                    "difference": difference,
                    "status": status,
                    "expected_ids": set(expected_ids),
                    "db_ids": set(db_ids),
                    "db_items": db_items,
                }
            )

    if not mismatches:
        print("\n✓ All section types match for this table!")
        return

    if not mismatches:
        print("\n✓ All section types match!")
        return

    # Analyze each mismatch in detail
    for mm in mismatches:
        print("\n" + "=" * 80)
        print(f"{mm['source']} → {mm['db_key']}")
        print(
            f"Expected: {mm['expected_count']}, Actual: {mm['db_count']} ({mm['status']} {abs(mm['difference'])})"
        )
        print("=" * 80)

        # Get expected items for this source
        expected_items_for_source = []
        for item in expected_items:
            if item["field"]:
                source_key = f"{item['source'].split('[')[0]}.{item['field']}"
            else:
                source_key = item["source"]
            if source_key == mm["source"]:
                expected_items_for_source.append(item)

        print(
            f"\n--- EXPECTED FROM PAYLOAD ({len(expected_items_for_source)} items) ---\n"
        )
        for idx, item in enumerate(expected_items_for_source, 1):
            formatted = format_payload_data(item["data"], TARGET_SECTION_TABLE_NAME)
            print(f"[{idx}] {formatted}")

        # Get actual items from DB
        print(f"\n--- ACTUAL FROM DB ({len(mm['db_items'])} items) ---\n")
        for idx, db_item in enumerate(mm["db_items"], 1):
            section = db_item["section"]

            # Get actual resource data from DB using the full id_in_organization
            resource_full_id = section.get("resource_full_id_in_org")
            if resource_full_id:
                resource_data = get_resource_data_from_db(
                    engine, TARGET_SECTION_TABLE_NAME, resource_full_id
                )
                if resource_data:
                    formatted = format_resource_data(
                        resource_data, TARGET_SECTION_TABLE_NAME
                    )
                    print(f"[{idx}] {formatted}")
                else:
                    print(f"[{idx}] (Resource not found - orphaned section)")
                    print(f"  id_in_organization: {resource_full_id}")
            else:
                print(f"[{idx}] (No resource ID)")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
