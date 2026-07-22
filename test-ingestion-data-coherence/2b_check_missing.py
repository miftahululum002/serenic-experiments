import json
from collections import defaultdict
from sqlalchemy import create_engine, text
from constant import BASE_PROJECT_PATH

# ===== Configuration =====
CONFIG_FILE_PATH = BASE_PROJECT_PATH  # "/home/aldo_serenic_ai/_experiments/2026-02-04 - Test Ingestion Data Coherence/config.env"

# Input: Two JSON file paths
JSON_FILE_PATH_1 = "1_djamil/3/1_update_encounters.json"  # First/earlier update file
JSON_FILE_PATH_2 = "1_djamil/3/2_update_encounters.json"  # Second/later update file (the one being checked)


# Target parameters
TARGET_NOREGISTRASI = "86efffff-38dc-0a8e-ad7b-1a1c662b8ad4"
TARGET_TABLE = "custom_composition"

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
CONNECTION_STRING = CONFIG.get("CONNECTION_STRING", "")
MANAGING_ORGANIZATION_ID = CONFIG.get("MANAGING_ORGANIZATION_ID", "")

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


def get_db_count_via_join(engine, table: str, encounter_id: str) -> int:
    """Get count from DB for tables that need to join through another table."""
    if table == "custom_composition_section":
        query = text(
            """
            SELECT COUNT(*) FROM custom_composition_section ccs
            JOIN custom_composition cc ON ccs.composition_id = cc.id
            WHERE cc.encounter_id = :enc_id AND ccs.managing_organization = :managing_org
        """
        )
        with engine.connect() as conn:
            result = conn.execute(
                query,
                {"enc_id": encounter_id, "managing_org": MANAGING_ORGANIZATION_ID},
            ).fetchone()
            return result[0] if result else 0
    return 0


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

    # For each section, get the id_in_organization of the resource it points to
    for section in sections:
        table_name = section["table_name"]
        resource_id = section["resource_id"]

        if table_name and resource_id:
            # Query the resource table to get id_in_organization
            id_query = text(
                f"SELECT id_in_organization FROM {table_name} WHERE id = :res_id"
            )
            with engine.connect() as conn:
                result = conn.execute(id_query, {"res_id": resource_id}).fetchone()
                if result and result[0]:
                    # Extract first part before "::"
                    section["resource_id_in_org"] = result[0].split("::")[0]
                else:
                    section["resource_id_in_org"] = None
        else:
            section["resource_id_in_org"] = None

    return sections


def count_expected_composition_sections(sources: dict) -> int:
    """Count total expected composition sections (same logic as 2a)."""
    total = 0
    if sources.get("triase_igd"):
        total += len(sources["triase_igd"])
    section_fields_cppt = ["soap", "observasi", "diagnosis", "prosedur", "obat"]
    if sources.get("cppt"):
        for unit in sources["cppt"]:
            for field in section_fields_cppt:
                total += len(unit.get(field) or [])
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
        for unit in sources["asesmen_awal"]:
            for field in section_fields_asesmen:
                total += len(unit.get(field) or [])
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
        for unit in sources["resume_medis"]:
            for field in section_fields_resume:
                total += len(unit.get(field) or [])
    section_fields_operasi = [
        "pemesanan",
        "diagnosis_pra_bedah",
        "diagnosis_pasca_bedah",
        "prosedur_operasi",
        "laporan_operasi",
    ]
    if sources.get("laporan_operasi"):
        for unit in sources["laporan_operasi"]:
            for field in section_fields_operasi:
                total += len(unit.get(field) or [])
    if sources.get("diagnosis_aktif"):
        total += len(sources["diagnosis_aktif"])
    return total


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


def get_db_ids(engine, table: str, encounter_id: str) -> set:
    """Get all id_in_organization values from DB for the specified table and encounter.

    The id_in_organization format is: {id}::{source}::{section}::{index}
    We extract just the first part (the actual ID).

    For tables without id_in_organization, returns empty set (count-only comparison).
    """
    if table == "custom_composition_section":
        # This table doesn't have id_in_organization, can only do count comparison
        return set()

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
    if not JSON_FILE_PATH_1 or not JSON_FILE_PATH_2:
        print(
            "[ERROR] Set JSON_FILE_PATH_1 and JSON_FILE_PATH_2 at the top of the script."
        )
        return

    if not CONNECTION_STRING:
        print("[ERROR] CONNECTION_STRING not set in config.")
        return

    # Load both JSON files
    print(f"Loading File 1: {JSON_FILE_PATH_1}")
    with open(JSON_FILE_PATH_1, "r") as f:
        data1 = json.load(f)
    updates1 = data1.get("updates", [])

    print(f"Loading File 2: {JSON_FILE_PATH_2}")
    with open(JSON_FILE_PATH_2, "r") as f:
        data2 = json.load(f)
    updates2 = data2.get("updates", [])

    # Find the update in file 2
    update2 = find_update_by_noregistrasi(updates2, TARGET_NOREGISTRASI)
    if not update2:
        print(
            f"[ERROR] No update found with noregistrasi: {TARGET_NOREGISTRASI} in File 2"
        )
        return

    # Check if it exists in file 1
    update1 = find_update_by_noregistrasi(updates1, TARGET_NOREGISTRASI)
    encounter_type = "EXISTING" if update1 else "NEW"

    sources2 = update2.get("sources", {})
    norec = update2.get("norec")

    print("=" * 60)
    print(f"Type: {encounter_type}")
    print(f"Noregistrasi: {TARGET_NOREGISTRASI}")
    print(f"Norec: {norec}")
    print(f"Table: {TARGET_TABLE}")
    print("=" * 60)

    # For this check, we only consider File 2 (as per 2a logic)
    print("\n--- Analyzing File 2 (Expected) ---")
    payload_items = get_all_payload_items(sources2, TARGET_TABLE)
    payload_ids = {item["id"] for item in payload_items}

    print(f"Payload items count: {len(payload_items)}")
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
        print(f"\n--- Duplicate IDs in File 2 payload: {len(duplicates)} ---")
        for dup_id, sources_list in duplicates.items():
            print(f"  ID: {dup_id} (appears {len(sources_list)} times)")
            for src in sources_list:
                print(f"    - {src}")

    # Connect to DB
    engine = create_engine(CONNECTION_STRING)

    # Get encounter_id
    encounter_id = get_encounter_id_from_noregistrasi(engine, TARGET_NOREGISTRASI)
    if not encounter_id:
        print(f"[ERROR] No encounter found with noregistrasi: {TARGET_NOREGISTRASI}")
        return

    print(f"\nEncounter ID: {encounter_id}")

    # Check if this table supports ID-based comparison
    if TARGET_TABLE in TABLES_WITHOUT_ID_IN_ORG:
        print(
            f"\n[NOTE] Table '{TARGET_TABLE}' does not have 'id_in_organization' column."
        )
        print(
            "Performing comparison via resource IDs (join through composition table).\n"
        )

        # For custom_composition_section, get expected items from payload
        if TARGET_TABLE == "custom_composition_section":
            expected_section_count = count_expected_composition_sections(sources2)
            expected_section_items = get_expected_section_items(sources2)

            print(f"Expected total section count (File 2): {expected_section_count}")

            # Count expected sections by table
            expected_by_table = defaultdict(int)
            for item in expected_section_items:
                expected_by_table[item["table"]] += 1

            # Get actual sections from DB
            db_sections = get_composition_sections_from_db(engine, encounter_id)

            # Count DB sections by table
            db_by_table = defaultdict(int)
            for section in db_sections:
                table_name = section.get("table_name")
                if table_name:
                    db_by_table[table_name] += 1

            print(f"Actual total section count (DB): {len(db_sections)}")

            # Compare counts
            if expected_section_count == len(db_sections):
                print("\n✓ Total section counts match!")
            else:
                difference = len(db_sections) - expected_section_count
                if difference > 0:
                    print(f"\n✗ DB has {difference} more sections than expected (OVER)")
                else:
                    print(
                        f"\n✗ DB has {abs(difference)} fewer sections than expected (MISSING)"
                    )

            # Count by table + source (from payload)
            expected_by_table_source = defaultdict(lambda: defaultdict(int))
            for item in expected_section_items:
                table = item["table"]
                # Extract source.field pattern (e.g., 'cppt.soap' from 'cppt[0].soap')
                source_parts = item["source"].split("[")
                if len(source_parts) > 1 and "]." in source_parts[1]:
                    # Has index like 'cppt[0].soap' -> 'cppt.soap'
                    base_source = source_parts[0]
                    field = source_parts[1].split("].")[1]
                    source_key = f"{base_source}.{field}"
                else:
                    # No index like 'triase_igd' or 'diagnosis_aktif'
                    source_key = item["source"]
                expected_by_table_source[table][source_key] += 1

            # Count by table + composition.type.section_type (from DB)
            db_by_table_section_type = defaultdict(lambda: defaultdict(int))
            for section in db_sections:
                table_name = section.get("table_name")
                section_type = section.get("section_type")
                composition_type = section.get("composition_type")
                if table_name and section_type and composition_type:
                    key = f"{composition_type}.{section_type}"
                    db_by_table_section_type[table_name][key] += 1

            # Print counts by table
            print("\n--- Expected Section Counts by Table (File 2) ---")
            for table in sorted(expected_by_table.keys()):
                print(f"  {table}: {expected_by_table[table]}")

            print("\n--- Actual Section Counts by Table (DB) ---")
            for table in sorted(db_by_table.keys()):
                print(f"  {table}: {db_by_table[table]}")

            # Find mismatches
            all_tables = set(expected_by_table.keys()) | set(db_by_table.keys())
            mismatches = []

            for table in sorted(all_tables):
                exp_count = expected_by_table.get(table, 0)
                db_count = db_by_table.get(table, 0)
                if exp_count != db_count:
                    mismatches.append(
                        {
                            "table": table,
                            "expected": exp_count,
                            "actual": db_count,
                            "difference": db_count - exp_count,
                        }
                    )

            print(f"\n--- Mismatched Tables: {len(mismatches)} ---")
            if mismatches:
                for mm in mismatches:
                    status = "OVER" if mm["difference"] > 0 else "MISSING"
                    print(
                        f"\n  {mm['table']}: expected={mm['expected']}, actual={mm['actual']} ({status} {abs(mm['difference'])})"
                    )

                    # Show breakdown by source (from payload)
                    if mm["table"] in expected_by_table_source:
                        print(f"    Expected by data source:")
                        for source, count in sorted(
                            expected_by_table_source[mm["table"]].items()
                        ):
                            print(f"      {source}: {count}")

                    # Show breakdown by composition.type.section_type (from DB)
                    if mm["table"] in db_by_table_section_type:
                        print(f"    Actual by composition.type.section_type:")
                        for sec_type, count in sorted(
                            db_by_table_section_type[mm["table"]].items()
                        ):
                            print(f"      {sec_type}: {count}")
            else:
                print("  (None - all tables match!)")

            # Detailed breakdown for all tables
            print("\n\n=== DETAILED BREAKDOWN BY TABLE ===")
            for table in sorted(all_tables):
                exp_count = expected_by_table.get(table, 0)
                db_count = db_by_table.get(table, 0)
                match_status = "✓ MATCH" if exp_count == db_count else "✗ MISMATCH"

                print(
                    f"\n[{table}] Expected: {exp_count}, Actual: {db_count} [{match_status}]"
                )

                # Show expected by data source
                if table in expected_by_table_source:
                    print(f"  Expected by data source:")
                    for source, count in sorted(
                        expected_by_table_source[table].items()
                    ):
                        print(f"    {source}: {count}")
                else:
                    print(f"  Expected by data source: (none)")

                # Show actual by composition.type.section_type
                if table in db_by_table_section_type:
                    print(f"  Actual by composition.type.section_type:")
                    for sec_type, count in sorted(
                        db_by_table_section_type[table].items()
                    ):
                        print(f"    {sec_type}: {count}")
                else:
                    print(f"  Actual by composition.type.section_type: (none)")
        else:
            # Generic count-based comparison for other tables
            db_count = get_db_count_via_join(engine, TARGET_TABLE, encounter_id)
            expected_count = len(payload_items)

            print(f"Expected count (File 2): {expected_count}")
            print(f"DB count: {db_count}")

            if expected_count == db_count:
                print("\n✓ Counts match!")
            else:
                difference = db_count - expected_count
                if difference > 0:
                    print(f"\n✗ DB has {difference} more items than expected (OVER)")
                else:
                    print(
                        f"\n✗ DB has {abs(difference)} fewer items than expected (MISSING)"
                    )
    else:
        # Get IDs from DB
        db_ids = get_db_ids(engine, TARGET_TABLE, encounter_id)
        print(f"DB items count: {len(db_ids)}")

        # Find missing items (in payload but not in DB)
        missing_ids = payload_ids - db_ids
        extra_ids = db_ids - payload_ids

        print(
            f"\n--- Missing in DB (in File 2 payload, not in DB): {len(missing_ids)} ---"
        )
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

        print(f"\n--- Extra in DB (in DB, not in File 2 payload): {len(extra_ids)} ---")
        if extra_ids:
            for eid in extra_ids:
                print(f"  ID: {eid}")
        else:
            print("  (None)")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
