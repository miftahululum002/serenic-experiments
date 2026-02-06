import json
from sqlalchemy import create_engine, text

# ===== Configuration =====
# CONFIG_FILE_PATH = "/home/aldo_serenic_ai/_experiments/2026-02-04 - Test Ingestion Data Coherence/config.env"
CONFIG_FILE_PATH = "/Users/miftahululum002/projects/serenic/experiments/2026-02-04 - Test Ingestion Data Coherence/config.env"

# Input parameters
# TARGET_NOREGISTRASI = "61a06c47-b63f-21eb-9c2d-cecec7cf29f9"
# TARGET_NOREGISTRASI = "cb4ee796-5621-424a-a6c2-a4b121b5bf29"
TARGET_NOREGISTRASI = "d6a7b1a0-25b6-1e22-562d-572080dcb6ef"
TARGET_TABLE = "custom_diagnostic_report"


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
    query = text(f"SELECT id_in_organization FROM {table} WHERE encounter_id = :enc_id")
    with engine.connect() as conn:
        results = conn.execute(query, {"enc_id": encounter_id}).fetchall()
        ids = set()
        for row in results:
            if row[0]:
                # Extract first part before "::"
                id_in_org = row[0].split("::")[0]
                ids.add(id_in_org)
        return ids


def get_encounter_id_from_noregistrasi(engine, noregistrasi: str) -> str | None:
    query = text("SELECT id FROM encounter WHERE id_in_organization = :noreg LIMIT 1")
    with engine.connect() as conn:
        result = conn.execute(query, {"noreg": noregistrasi}).fetchone()
        return result[0] if result else None


def find_update_by_noregistrasi(updates: list, noregistrasi: str) -> dict | None:
    for update in updates:
        if update.get("noregistrasi") == noregistrasi:
            return update
    return None


def main():
    # Load JSON data
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

    print(f"\n--- Extra in DB (in DB, not in payload): {len(extra_ids)} ---")
    if extra_ids:
        for eid in extra_ids:
            print(f"  ID: {eid}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
