import json
from sqlalchemy import create_engine, text
from constant import BASE_PROJECT_PATH

# ===== Configuration =====
CONFIG_FILE_PATH = BASE_PROJECT_PATH  # "/home/aldo_serenic_ai/_experiments/2026-02-04 - Test Ingestion Data Coherence/config.env"

# Input parameters
TARGET_NOREGISTRASI = "0226001045"
TARGET_TABLE = "custom_observation"

# Tables that don't have id_in_organization column (can't check IDs for these)
TABLES_WITHOUT_ID_IN_ORG = {
    "custom_composition_section",
}


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
JSON_FILE_PATH = CONFIG.get("JSON_FILE_PATH", "")
CONNECTION_STRING = CONFIG.get("CONNECTION_STRING", "")
MANAGING_ORGANIZATION_ID = CONFIG.get("MANAGING_ORGANIZATION_ID", "")

# Ensure JSON_FILE_PATH is absolute or make it relative to this script's directory
import os

if not os.path.isabs(JSON_FILE_PATH):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    JSON_FILE_PATH = os.path.join(script_dir, JSON_FILE_PATH)

# Mapping: table -> list of (source_name, field_name) that contribute to it
TABLE_SOURCE_MAPPINGS = {
    "custom_observation": [
        ("triase_igd", None),
        ("cppt", "observasi"),
        ("asesmen_awal", "pemeriksaan_fisik"),
        ("asesmen_awal", "hasil_lab"),
        ("resume_medis", "pemeriksaan_fisik"),
        ("resume_medis", "hasil_lab"),
        ("penunjang_lab", "hasil_lab"),
        ("prosedur_medis_lain", "hasil_prosedur"),
    ],
    "custom_clinical_impression": [
        ("cppt", "soap"),
        ("asesmen_awal", "keluhan"),
        ("asesmen_awal", "riwayat_penyakit_dahulu"),
        ("asesmen_awal", "riwayat_penyakit_sekarang"),
        ("asesmen_awal", "assessment_text"),
        ("resume_medis", "keluhan"),
        ("resume_medis", "riwayat_penyakit_dahulu"),
        ("resume_medis", "riwayat_penyakit_sekarang"),
        ("resume_medis", "resume_text"),
    ],
    "custom_diagnosis": [
        ("cppt", "diagnosis"),
        ("asesmen_awal", "diagnosis"),
        ("resume_medis", "diagnosis"),
        ("laporan_operasi", "diagnosis_pra_bedah"),
        ("laporan_operasi", "diagnosis_pasca_bedah"),
        ("diagnosis_aktif", None),
    ],
    "custom_procedure": [
        ("cppt", "prosedur"),
        ("asesmen_awal", "prosedur"),
        ("resume_medis", "prosedur"),
        ("laporan_operasi", "prosedur_operasi"),
    ],
    "custom_diagnostic_report": [
        ("asesmen_awal", "laporan_radiologi"),
        ("asesmen_awal", "laporan_lab"),
        ("resume_medis", "laporan_radiologi"),
        ("resume_medis", "laporan_lab"),
        ("penunjang_lab", "laporan_lab"),
        ("penunjang_radiologi", "laporan_radiologi"),
        ("laporan_operasi", "laporan_operasi"),
        ("prosedur_medis_lain", "laporan_prosedur"),
    ],
    "custom_medication": [
        ("cppt", "obat"),
        ("resume_medis", "obat"),
        ("obat", "obat"),
    ],
    "custom_medication_request": [
        ("obat", "resep_obat"),
    ],
    "custom_medication_dispense": [
        ("obat", "pengeluaran_obat"),
    ],
    "custom_medication_statement": [
        ("asesmen_awal", "riwayat_penggunaan_obat"),
        ("resume_medis", "riwayat_penggunaan_obat"),
    ],
    "custom_family_member_history": [
        ("asesmen_awal", "riwayat_penyakit_keluarga"),
        ("resume_medis", "riwayat_penyakit_keluarga"),
    ],
    "custom_service_request": [
        ("penunjang_lab", "pemesanan"),
        ("penunjang_radiologi", "pemesanan"),
        ("laporan_operasi", "pemesanan"),
        ("prosedur_medis_lain", "pemesanan"),
    ],
    "custom_billing": [
        ("billing", None),
    ],
}


def get_items_from_source(
    sources: dict, source_name: str, field_name: str | None
) -> list:
    """Get items from a source field with their source path."""
    if source_name not in sources:
        return []

    source_data = sources[source_name]
    if source_data is None:
        return []

    items = []
    source_path = f"{source_name}.{field_name}" if field_name else source_name

    # Direct list (triase_igd, diagnosis_aktif, billing)
    if field_name is None:
        if isinstance(source_data, list):
            for item in source_data:
                if isinstance(item, dict):
                    items.append(
                        {
                            "id": item.get("id"),
                            "source": source_path,
                            "data": item,
                        }
                    )
        return items

    # List of units (cppt, asesmen_awal, resume_medis, etc.)
    if isinstance(source_data, list):
        for unit_idx, unit in enumerate(source_data):
            if isinstance(unit, dict) and field_name in unit:
                field_items = unit.get(field_name) or []
                for item in field_items:
                    if isinstance(item, dict):
                        items.append(
                            {
                                "id": item.get("id"),
                                "source": f"{source_name}[{unit_idx}].{field_name}",
                                "data": item,
                            }
                        )
    return items


def get_all_payload_items(sources: dict, table: str) -> list:
    """Get all items from payload that should go to the specified table."""
    if table not in TABLE_SOURCE_MAPPINGS:
        return []

    all_items = []
    for source_name, field_name in TABLE_SOURCE_MAPPINGS[table]:
        items = get_items_from_source(sources, source_name, field_name)
        all_items.extend(items)
    return all_items


def get_db_ids(engine, table: str, encounter_id: str) -> set:
    """Get all id_in_organization values from DB for the specified table and encounter.

    The id_in_organization format is: {id}::{source}::{section}::{index}
    We extract just the first part (the actual ID).
    """
    query = text(
        f"SELECT id_in_organization FROM {table} WHERE encounter_id = :enc_id AND managing_organization = :managing_org"
    )
    with engine.connect() as conn:
        results = conn.execute(
            query, {"enc_id": encounter_id, "managing_org": MANAGING_ORGANIZATION_ID}
        ).fetchall()
        ids = set()
        for row in results:
            if row[0]:
                # Extract first part before "::"
                id_in_org = row[0].split("::")[0]
                ids.add(id_in_org)
        return ids


def get_composition_sections_from_db(engine, encounter_id: str) -> list:
    """Get all composition sections from DB with their resource IDs."""
    query = text(
        """
        SELECT 
            ccs.id,
            ccs.section_table_name,
            ccs.section_resource_id,
            ccs.section_type,
            cc.type as composition_type
        FROM custom_composition_section ccs
        JOIN custom_composition cc ON ccs.composition_id = cc.id
        WHERE cc.encounter_id = :enc_id AND ccs.managing_organization = :managing_org
        ORDER BY ccs.section_sequence_order
    """
    )

    sections = []
    with engine.connect() as conn:
        results = conn.execute(
            query, {"enc_id": encounter_id, "managing_org": MANAGING_ORGANIZATION_ID}
        ).fetchall()
        for row in results:
            sections.append(
                {
                    "section_id": row[0],
                    "table_name": row[1],
                    "resource_id": row[2],
                    "section_type": row[3],
                    "composition_type": row[4],
                }
            )

    # For each section, extract the id_in_organization from resource
    for section in sections:
        resource_id = section["resource_id"]
        if resource_id:
            # Extract first part before "::"
            if "::" in resource_id:
                section["resource_id_in_org"] = resource_id.split("::")[0]
            else:
                section["resource_id_in_org"] = resource_id
        else:
            section["resource_id_in_org"] = None

    return sections


def get_expected_section_items(sources: dict) -> list:
    """Get all items that should have composition sections with their resource IDs and table."""
    items = []

    # Helper to map field to table
    field_to_table = {
        "soap": "custom_clinical_impression",
        "observasi": "custom_observation",
        "diagnosis": "custom_diagnosis",
        "prosedur": "custom_procedure",
        "obat": "custom_medication",
        "keluhan": "custom_clinical_impression",
        "riwayat_penyakit_dahulu": "custom_clinical_impression",
        "riwayat_penyakit_sekarang": "custom_clinical_impression",
        "riwayat_penyakit_keluarga": "custom_family_member_history",
        "riwayat_penggunaan_obat": "custom_medication_statement",
        "pemeriksaan_fisik": "custom_observation",
        "hasil_lab": "custom_observation",
        "laporan_radiologi": "custom_diagnostic_report",
        "laporan_lab": "custom_diagnostic_report",
        "assessment_text": "custom_clinical_impression",
        "resume_text": "custom_clinical_impression",
        "pemesanan": "custom_service_request",
        "diagnosis_pra_bedah": "custom_diagnosis",
        "diagnosis_pasca_bedah": "custom_diagnosis",
        "prosedur_operasi": "custom_procedure",
        "laporan_operasi": "custom_diagnostic_report",
        "hasil_prosedur": "custom_observation",
        "laporan_prosedur": "custom_diagnostic_report",
    }

    # triase_igd
    if sources.get("triase_igd"):
        for item in sources["triase_igd"]:
            if isinstance(item, dict) and item.get("id"):
                items.append(
                    {
                        "id": item["id"],
                        "source": "triase_igd",
                        "table": "custom_observation",
                    }
                )

    # cppt sections
    section_fields_cppt = ["soap", "observasi", "diagnosis", "prosedur", "obat"]
    if sources.get("cppt"):
        for unit_idx, unit in enumerate(sources["cppt"]):
            for field in section_fields_cppt:
                field_items = unit.get(field) or []
                for item in field_items:
                    if isinstance(item, dict) and item.get("id"):
                        items.append(
                            {
                                "id": item["id"],
                                "source": f"cppt[{unit_idx}].{field}",
                                "table": field_to_table.get(field, "unknown"),
                            }
                        )

    # asesmen_awal sections
    section_fields_asesmen = [
        "keluhan",
        "riwayat_penyakit_dahulu",
        "riwayat_penyakit_sekarang",
        "riwayat_penyakit_keluarga",
        "riwayat_penggunaan_obat",
        "pemeriksaan_fisik",
        "hasil_lab",
        "laporan_radiologi",
        "laporan_lab",
        "assessment_text",
        "diagnosis",
        "prosedur",
    ]
    if sources.get("asesmen_awal"):
        for unit_idx, unit in enumerate(sources["asesmen_awal"]):
            for field in section_fields_asesmen:
                field_items = unit.get(field) or []
                for item in field_items:
                    if isinstance(item, dict) and item.get("id"):
                        items.append(
                            {
                                "id": item["id"],
                                "source": f"asesmen_awal[{unit_idx}].{field}",
                                "table": field_to_table.get(field, "unknown"),
                            }
                        )

    # resume_medis sections
    section_fields_resume = [
        "keluhan",
        "riwayat_penyakit_dahulu",
        "riwayat_penyakit_sekarang",
        "riwayat_penyakit_keluarga",
        "riwayat_penggunaan_obat",
        "pemeriksaan_fisik",
        "hasil_lab",
        "laporan_radiologi",
        "laporan_lab",
        "diagnosis",
        "prosedur",
        "obat",
        "resume_text",
    ]
    if sources.get("resume_medis"):
        for unit_idx, unit in enumerate(sources["resume_medis"]):
            for field in section_fields_resume:
                field_items = unit.get(field) or []
                for item in field_items:
                    if isinstance(item, dict) and item.get("id"):
                        items.append(
                            {
                                "id": item["id"],
                                "source": f"resume_medis[{unit_idx}].{field}",
                                "table": field_to_table.get(field, "unknown"),
                            }
                        )

    # laporan_operasi sections
    section_fields_operasi = [
        "pemesanan",
        "diagnosis_pra_bedah",
        "diagnosis_pasca_bedah",
        "prosedur_operasi",
        "laporan_operasi",
    ]
    if sources.get("laporan_operasi"):
        for unit_idx, unit in enumerate(sources["laporan_operasi"]):
            for field in section_fields_operasi:
                field_items = unit.get(field) or []
                for item in field_items:
                    if isinstance(item, dict) and item.get("id"):
                        items.append(
                            {
                                "id": item["id"],
                                "source": f"laporan_operasi[{unit_idx}].{field}",
                                "table": field_to_table.get(field, "unknown"),
                            }
                        )

    # NOTE: penunjang_lab, penunjang_radiologi, prosedur_medis_lain are NOT counted as composition sections
    # They create resources but NOT composition sections according to 1a_test_coherence.py logic

    # diagnosis_aktif
    if sources.get("diagnosis_aktif"):
        for item in sources["diagnosis_aktif"]:
            if isinstance(item, dict) and item.get("id"):
                items.append(
                    {
                        "id": item["id"],
                        "source": "diagnosis_aktif",
                        "table": "custom_diagnosis",
                    }
                )

    return items


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


def main():
    # Load JSON data
    print(f"Loading JSON from: {JSON_FILE_PATH}")
    with open(JSON_FILE_PATH, "r") as f:
        data = json.load(f)

    updates = data.get("updates", [])
    if not updates:
        print("No updates found in JSON file")
        return

    # Find the update with matching noregistrasi
    update = find_update_by_noregistrasi(updates, TARGET_NOREGISTRASI)
    if not update:
        print(f"No update found with noregistrasi: {TARGET_NOREGISTRASI}")
        return

    sources = update.get("sources", {})
    norec = update.get("norec")

    print("=" * 60)
    print(f"Noregistrasi: {TARGET_NOREGISTRASI}")
    print(f"Norec: {norec}")
    print(f"Table: {TARGET_TABLE}")
    print("=" * 60)

    # Get items from payload
    payload_items = get_all_payload_items(sources, TARGET_TABLE)
    payload_ids = {item["id"] for item in payload_items}

    print(f"\nPayload items count: {len(payload_items)}")
    print(f"Unique IDs in payload: {len(payload_ids)}")

    # Check for duplicate IDs in payload
    id_counts = {}
    for item in payload_items:
        item_id = item["id"]
        if item_id not in id_counts:
            id_counts[item_id] = []
        id_counts[item_id].append(item["source"])

    duplicates = {k: v for k, v in id_counts.items() if len(v) > 1}
    if duplicates:
        print(f"\n--- Duplicate IDs in payload: {len(duplicates)} ---")
        for dup_id, sources_list in duplicates.items():
            print(f"  ID: {dup_id} (appears {len(sources_list)} times)")
            for src in sources_list:
                print(f"    - {src}")

    # Connect to DB
    if not CONNECTION_STRING:
        print("[ERROR] CONNECTION_STRING not set.")
        return

    engine = create_engine(CONNECTION_STRING)

    # Get encounter_id
    encounter_id = get_encounter_id_from_noregistrasi(engine, TARGET_NOREGISTRASI)
    if not encounter_id:
        print(f"[ERROR] No encounter found with noregistrasi: {TARGET_NOREGISTRASI}")
        return

    print(f"Encounter ID: {encounter_id}")

    # Check if this table supports ID-based comparison
    if TARGET_TABLE in TABLES_WITHOUT_ID_IN_ORG:
        print(
            f"\n[NOTE] Table '{TARGET_TABLE}' does not have 'id_in_organization' column."
        )
        print("Performing comparison via resource IDs (composition sections).\n")

        if TARGET_TABLE == "custom_composition_section":
            # Get expected section items from payload
            expected_section_items = get_expected_section_items(sources)

            # NOTE: Do NOT use set() here! Each item represents a SECTION, not a unique resource
            # The same resource ID can be referenced by multiple sections
            expected_count = len(expected_section_items)

            # Get actual sections from DB
            db_sections = get_composition_sections_from_db(engine, encounter_id)
            db_count = len(db_sections)

            print(f"Expected section count: {expected_count}")
            print(f"DB section count: {db_count}")

            if expected_count != db_count:
                print(f"Missing: {expected_count - db_count} sections\n")

                # Create a set of (table, id) from DB for quick lookup
                from collections import Counter

                db_table_id_counter = Counter(
                    [
                        (s["table_name"], s["resource_id_in_org"])
                        for s in db_sections
                        if s["resource_id_in_org"]
                    ]
                )

                # Find all missing section references
                print("--- Missing Composition Sections ---")
                missing_count = 0
                for item in expected_section_items:
                    key = (item["table"], item["id"])
                    # Check if this specific occurrence is in the DB
                    if db_table_id_counter.get(key, 0) == 0:
                        # This resource is completely missing
                        print(f"{item['source']} -> {item['table']} (ID: {item['id']})")
                        missing_count += 1
                    elif db_table_id_counter[key] > 0:
                        # Decrement counter (this occurrence exists)
                        db_table_id_counter[key] -= 1
                    else:
                        # Counter is 0, this is an extra expected occurrence
                        print(f"{item['source']} -> {item['table']} (ID: {item['id']})")
                        missing_count += 1

                if missing_count == 0:
                    print("(None)")
            else:
                print("\n✓ Section counts match!")
        else:
            # Generic handling for other tables without id_in_organization
            print("[WARNING] Generic handling not implemented for this table.")
    else:
        # Standard ID-based comparison
        # Get IDs from DB
        db_ids = get_db_ids(engine, TARGET_TABLE, encounter_id)
        print(f"DB items count: {len(db_ids)}")

        # Find missing items (in payload but not in DB)
        missing_ids = payload_ids - db_ids
        extra_ids = db_ids - payload_ids

        print(f"\n--- Missing in DB (in payload, not in DB): {len(missing_ids)} ---")
        if missing_ids:
            for item in payload_items:
                if item["id"] in missing_ids:
                    print(f"\n  ID: {item['id']}")
                    print(f"  Source: {item['source']}")
                    # Print a summary of the data
                    data = item["data"]
                    if "concept" in data:
                        print(f"  Concept: {data['concept'].get('text', 'N/A')}")
                    if "medication_name" in data:
                        print(f"  Medication: {data['medication_name']}")
                    if "diagnosis_text" in data:
                        print(f"  Diagnosis: {data['diagnosis_text']}")
                    if "procedure_text" in data:
                        print(f"  Procedure: {data['procedure_text']}")
                    if "product_name" in data:
                        print(f"  Product: {data['product_name']}")
        else:
            print("  (None)")

        print(f"\n--- Extra in DB (in DB, not in payload): {len(extra_ids)} ---")
        if extra_ids:
            for eid in extra_ids:
                print(f"  ID: {eid}")
        else:
            print("  (None)")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
