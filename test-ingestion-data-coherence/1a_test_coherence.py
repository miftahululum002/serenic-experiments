import json
from sqlalchemy import create_engine, text
from collections import defaultdict
from constant import BASE_PROJECT_PATH

# ===== Configuration =====
# CONFIG_FILE_PATH = "/Users/miftahululum002/projects/serenic/experiments/test-ingestion-data-coherence/config.env"
CONFIG_FILE_PATH = BASE_PROJECT_PATH
# con "/Users/miftahululum002/projects/serenic/experiments/test-ingestion-data-coherence/config.env"


def load_config(config_path: str) -> dict:
    """Load configuration from env file."""
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
ENCOUNTER_INDEX = int(CONFIG.get("ENCOUNTER_INDEX", "0"))
RUN_ALL = CONFIG.get("RUN_ALL", "false").lower() == "true"
MANAGING_ORGANIZATION_ID = CONFIG.get("MANAGING_ORGANIZATION_ID", "")

# Buffer: 1 diagnosis is added when encounter is initialized (SEP diagnosis)
CUSTOM_DIAGNOSIS_BUFFER = 1

# ===== Mappings: source fields to resource types =====
OBSERVATION_FIELDS = [
    ("triase_igd", None),  # direct list
    ("cppt", "observasi"),
    ("asesmen_awal", "pemeriksaan_fisik"),
    ("asesmen_awal", "hasil_lab"),
    ("resume_medis", "pemeriksaan_fisik"),
    ("resume_medis", "hasil_lab"),
    ("penunjang_lab", "hasil_lab"),
    ("prosedur_medis_lain", "hasil_prosedur"),
]

CLINICAL_IMPRESSION_FIELDS = [
    ("cppt", "soap"),
    ("asesmen_awal", "keluhan"),
    ("asesmen_awal", "riwayat_penyakit_dahulu"),
    ("asesmen_awal", "riwayat_penyakit_sekarang"),
    ("asesmen_awal", "assessment_text"),
    ("resume_medis", "keluhan"),
    ("resume_medis", "riwayat_penyakit_dahulu"),
    ("resume_medis", "riwayat_penyakit_sekarang"),
    ("resume_medis", "resume_text"),
]

DIAGNOSIS_FIELDS = [
    ("cppt", "diagnosis"),
    ("asesmen_awal", "diagnosis"),
    ("resume_medis", "diagnosis"),
    ("laporan_operasi", "diagnosis_pra_bedah"),
    ("laporan_operasi", "diagnosis_pasca_bedah"),
    ("diagnosis_aktif", None),
]

PROCEDURE_FIELDS = [
    ("cppt", "prosedur"),
    ("asesmen_awal", "prosedur"),
    ("resume_medis", "prosedur"),
    ("laporan_operasi", "prosedur_operasi"),
]

DIAGNOSTIC_REPORT_FIELDS = [
    ("asesmen_awal", "laporan_radiologi"),
    ("asesmen_awal", "laporan_lab"),
    ("resume_medis", "laporan_radiologi"),
    ("resume_medis", "laporan_lab"),
    ("penunjang_lab", "laporan_lab"),
    ("penunjang_radiologi", "laporan_radiologi"),
    ("laporan_operasi", "laporan_operasi"),
    ("prosedur_medis_lain", "laporan_prosedur"),
]

MEDICATION_FIELDS = [
    ("cppt", "obat"),
    ("resume_medis", "obat"),
    ("obat", "obat"),
]

MEDICATION_REQUEST_FIELDS = [
    ("obat", "resep_obat"),
]

MEDICATION_DISPENSE_FIELDS = [
    ("obat", "pengeluaran_obat"),
]

MEDICATION_STATEMENT_FIELDS = [
    ("asesmen_awal", "riwayat_penggunaan_obat"),
    ("resume_medis", "riwayat_penggunaan_obat"),
]

FAMILY_MEMBER_HISTORY_FIELDS = [
    ("asesmen_awal", "riwayat_penyakit_keluarga"),
    ("resume_medis", "riwayat_penyakit_keluarga"),
]

SERVICE_REQUEST_FIELDS = [
    ("penunjang_lab", "pemesanan"),
    ("penunjang_radiologi", "pemesanan"),
    ("laporan_operasi", "pemesanan"),
    ("prosedur_medis_lain", "pemesanan"),
]

BILLING_FIELDS = [
    ("billing", None),
]


def count_items_from_source(
    sources: dict, source_name: str, field_name: str | None
) -> int:
    """Count items from a source field."""
    if source_name not in sources:
        return 0

    source_data = sources[source_name]
    if source_data is None:
        return 0

    # Direct list (triase_igd, diagnosis_aktif, billing)
    if field_name is None:
        return len(source_data) if isinstance(source_data, list) else 0

    # List of units (cppt, asesmen_awal, resume_medis, etc.)
    total = 0
    if isinstance(source_data, list):
        for unit in source_data:
            if isinstance(unit, dict) and field_name in unit:
                items = unit.get(field_name) or []
                total += len(items)
    return total


def count_expected_resources(sources: dict, field_mappings: list) -> int:
    """Count expected resources from JSON sources."""
    total = 0
    for source_name, field_name in field_mappings:
        total += count_items_from_source(sources, source_name, field_name)
    return total


def count_expected_compositions(sources: dict) -> dict:
    """Count expected compositions by type from JSON sources."""
    counts = {
        "emergency_triage": 0,
        "soap": 0,
        "initial_assessment": 0,
        "resume": 0,
        "operations_report": 0,
        "active_diagnosis": 0,
    }

    # triase_igd -> 1 composition if has data
    if sources.get("triase_igd") and len(sources["triase_igd"]) > 0:
        counts["emergency_triage"] = 1

    # cppt -> each unit is 1 composition
    if sources.get("cppt"):
        for unit in sources["cppt"]:
            if any(
                unit.get(f)
                for f in ["soap", "observasi", "diagnosis", "prosedur", "obat"]
            ):
                counts["soap"] += 1

    # asesmen_awal -> each unit is 1 composition
    if sources.get("asesmen_awal"):
        fields = [
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
        for unit in sources["asesmen_awal"]:
            if any(unit.get(f) for f in fields):
                counts["initial_assessment"] += 1

    # resume_medis -> each unit is 1 composition
    if sources.get("resume_medis"):
        fields = [
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
        for unit in sources["resume_medis"]:
            if any(unit.get(f) for f in fields):
                counts["resume"] += 1

    # laporan_operasi -> each unit is 1 composition
    if sources.get("laporan_operasi"):
        fields = [
            "pemesanan",
            "diagnosis_pra_bedah",
            "diagnosis_pasca_bedah",
            "prosedur_operasi",
            "laporan_operasi",
        ]
        for unit in sources["laporan_operasi"]:
            if any(unit.get(f) for f in fields):
                counts["operations_report"] += 1

    # diagnosis_aktif -> 1 composition if has data
    if sources.get("diagnosis_aktif") and len(sources["diagnosis_aktif"]) > 0:
        counts["active_diagnosis"] = 1

    return counts


def count_expected_composition_sections(sources: dict) -> int:
    """Count expected total composition sections from JSON sources."""
    total = 0

    # triase_igd -> 1 section per observation (all under same section_type 'triase_igd')
    if sources.get("triase_igd"):
        total += len(sources["triase_igd"])

    # cppt -> sections per unit
    section_fields_cppt = ["soap", "observasi", "diagnosis", "prosedur", "obat"]
    if sources.get("cppt"):
        for unit in sources["cppt"]:
            for field in section_fields_cppt:
                items = unit.get(field) or []
                total += len(items)

    # asesmen_awal -> sections per unit
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
                items = unit.get(field) or []
                total += len(items)

    # resume_medis -> sections per unit
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
                items = unit.get(field) or []
                total += len(items)

    # laporan_operasi -> sections per unit
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
                items = unit.get(field) or []
                total += len(items)

    # diagnosis_aktif -> 1 section per diagnosis
    if sources.get("diagnosis_aktif"):
        total += len(sources["diagnosis_aktif"])

    return total


def get_source_breakdown(sources: dict, field_mappings: list) -> dict:
    """Get breakdown of counts per source field."""
    breakdown = {}
    for source_name, field_name in field_mappings:
        count = count_items_from_source(sources, source_name, field_name)
        if count > 0:
            key = f"{source_name}.{field_name}" if field_name else source_name
            breakdown[key] = count
    return breakdown


def print_source_breakdown(sources: dict, field_mappings: list):
    """Print breakdown of source data for debugging."""
    breakdown = get_source_breakdown(sources, field_mappings)
    print("    Source breakdown:")
    if breakdown:
        for source, count in breakdown.items():
            print(f"      - {source}: {count}")
    else:
        print("      (no source data found in JSON)")


# Mapping from table name to field mappings
TABLE_TO_FIELDS = {
    "custom_observation": OBSERVATION_FIELDS,
    "custom_clinical_impression": CLINICAL_IMPRESSION_FIELDS,
    "custom_diagnosis": DIAGNOSIS_FIELDS,
    "custom_procedure": PROCEDURE_FIELDS,
    "custom_diagnostic_report": DIAGNOSTIC_REPORT_FIELDS,
    "custom_medication": MEDICATION_FIELDS,
    "custom_medication_request": MEDICATION_REQUEST_FIELDS,
    "custom_medication_dispense": MEDICATION_DISPENSE_FIELDS,
    "custom_medication_statement": MEDICATION_STATEMENT_FIELDS,
    "custom_family_member_history": FAMILY_MEMBER_HISTORY_FIELDS,
    "custom_service_request": SERVICE_REQUEST_FIELDS,
    "custom_billing": BILLING_FIELDS,
}


def get_encounter_id_from_noregistrasi(engine, noregistrasi: str) -> str | None:
    """Get the actual encounter id from the noregistrasi field."""
    query = text(
        "SELECT id FROM encounter WHERE id_in_organization = :noreg AND managing_organization = :managing_org LIMIT 1"
    )

    print(f"Query: {query}")
    with engine.connect() as conn:
        result = conn.execute(
            query, {"noreg": noregistrasi, "managing_org": MANAGING_ORGANIZATION_ID}
        ).fetchone()
        return result[0] if result else None


def get_db_counts(engine, encounter_id: str) -> dict:
    """Get actual counts from database for an encounter."""
    tables = {
        "custom_observation": "encounter_id",
        "custom_clinical_impression": "encounter_id",
        "custom_diagnosis": "encounter_id",
        "custom_procedure": "encounter_id",
        "custom_diagnostic_report": "encounter_id",
        "custom_medication": "encounter_id",
        "custom_medication_request": "encounter_id",
        "custom_medication_dispense": "encounter_id",
        "custom_medication_statement": "encounter_id",
        "custom_family_member_history": "patient_id",  # needs patient lookup
        "custom_service_request": "encounter_id",
        "custom_billing": "encounter_id",
        "custom_composition": "encounter_id",
        "custom_composition_section": None,  # needs composition lookup
    }

    counts = {}
    with engine.connect() as conn:
        # Get patient_id for this encounter
        patient_query = text(
            "SELECT patient FROM encounter WHERE id = :enc_id AND managing_organization = :managing_org"
        )
        patient_result = conn.execute(
            patient_query,
            {"enc_id": encounter_id, "managing_org": MANAGING_ORGANIZATION_ID},
        ).fetchone()
        patient_id = patient_result[0] if patient_result else None

        for table, filter_col in tables.items():
            if table == "custom_family_member_history":
                if patient_id:
                    query = text(
                        f"SELECT COUNT(*) FROM {table} WHERE {filter_col} = :id AND managing_organization = :managing_org"
                    )
                    result = conn.execute(
                        query,
                        {"id": patient_id, "managing_org": MANAGING_ORGANIZATION_ID},
                    ).fetchone()
                else:
                    result = (0,)
            elif table == "custom_composition_section":
                query = text(
                    """
                    SELECT COUNT(*) FROM custom_composition_section ccs
                    JOIN custom_composition cc ON ccs.composition_id = cc.id
                    WHERE cc.encounter_id = :enc_id AND ccs.managing_organization = :managing_org
                """
                )
                result = conn.execute(
                    query,
                    {"enc_id": encounter_id, "managing_org": MANAGING_ORGANIZATION_ID},
                ).fetchone()
            else:
                query = text(
                    f"SELECT COUNT(*) FROM {table} WHERE {filter_col} = :enc_id AND managing_organization = :managing_org"
                )
                result = conn.execute(
                    query,
                    {"enc_id": encounter_id, "managing_org": MANAGING_ORGANIZATION_ID},
                ).fetchone()
            counts[table] = result[0] if result else 0

    return counts


def get_composition_counts_by_type(engine, encounter_id: str) -> dict:
    """Get composition counts by type from database."""
    query = text(
        """
        SELECT type, COUNT(*) as cnt
        FROM custom_composition
        WHERE encounter_id = :enc_id AND managing_organization = :managing_org
        GROUP BY type
    """
    )

    counts = defaultdict(int)
    with engine.connect() as conn:
        results = conn.execute(
            query, {"enc_id": encounter_id, "managing_org": MANAGING_ORGANIZATION_ID}
        ).fetchall()
        for row in results:
            counts[row[0]] = row[1]
    return dict(counts)


def check_single_encounter(update: dict, engine, verbose: bool = True) -> dict:
    """Check a single encounter and return result dict."""
    norec = update.get("norec")
    noregistrasi = update.get("noregistrasi")
    sources = update.get("sources", {})

    result = {
        "norec": norec,
        "noregistrasi": noregistrasi,
        "status": "UNKNOWN",
        "mismatches": [],
        "error": None,
    }

    if verbose:
        print("=" * 60)
        print(f"Norec: {norec}")
        print(f"Noregistrasi: {noregistrasi}")
        print("=" * 60)

    # Expected counts from JSON
    expected = {
        "custom_observation": count_expected_resources(sources, OBSERVATION_FIELDS),
        "custom_clinical_impression": count_expected_resources(
            sources, CLINICAL_IMPRESSION_FIELDS
        ),
        "custom_diagnosis": count_expected_resources(sources, DIAGNOSIS_FIELDS)
        + CUSTOM_DIAGNOSIS_BUFFER,
        "custom_procedure": count_expected_resources(sources, PROCEDURE_FIELDS),
        "custom_diagnostic_report": count_expected_resources(
            sources, DIAGNOSTIC_REPORT_FIELDS
        ),
        "custom_medication": count_expected_resources(sources, MEDICATION_FIELDS),
        "custom_medication_request": count_expected_resources(
            sources, MEDICATION_REQUEST_FIELDS
        ),
        "custom_medication_dispense": count_expected_resources(
            sources, MEDICATION_DISPENSE_FIELDS
        ),
        "custom_medication_statement": count_expected_resources(
            sources, MEDICATION_STATEMENT_FIELDS
        ),
        "custom_family_member_history": count_expected_resources(
            sources, FAMILY_MEMBER_HISTORY_FIELDS
        ),
        "custom_service_request": count_expected_resources(
            sources, SERVICE_REQUEST_FIELDS
        ),
        "custom_billing": count_expected_resources(sources, BILLING_FIELDS),
    }

    expected_compositions = count_expected_compositions(sources)
    expected_composition_total = sum(expected_compositions.values())
    expected_sections = count_expected_composition_sections(sources)

    if verbose:
        print("\n--- Expected Counts from JSON ---")
        for table, count in expected.items():
            print(f"  {table}: {count}")
        print(f"\n  Compositions (total): {expected_composition_total}")
        for comp_type, count in expected_compositions.items():
            print(f"    - {comp_type}: {count}")
        print(f"  Composition Sections (total): {expected_sections}")

    # Get actual encounter_id from noregistrasi
    encounter_id = get_encounter_id_from_noregistrasi(engine, noregistrasi)
    if not encounter_id:
        result["status"] = "ERROR"
        result["error"] = f"No encounter found with noregistrasi: {noregistrasi}"
        if verbose:
            print(f"\n[ERROR] {result['error']}")
        return result

    if verbose:
        print(f"\nEncounter ID (from DB): {encounter_id}")

    actual = get_db_counts(engine, encounter_id)
    actual_compositions_by_type = get_composition_counts_by_type(engine, encounter_id)

    if verbose:
        print("\n--- Actual Counts from DB ---")
        for table, count in actual.items():
            print(f"  {table}: {count}")
        print(f"\n  Compositions by type:")
        for comp_type, count in actual_compositions_by_type.items():
            print(f"    - {comp_type}: {count}")

    # Comparison
    if verbose:
        print("\n--- Comparison ---")

    all_match = True
    for table in expected:
        exp = expected[table]
        act = actual.get(table, 0)
        status = "OK" if exp == act else "MISMATCH"
        if exp != act:
            all_match = False
            result["mismatches"].append(f"{table}: expected={exp}, actual={act}")
        if verbose:
            print(f"  {table}: expected={exp}, actual={act} [{status}]")
            if exp != act and table in TABLE_TO_FIELDS:
                print_source_breakdown(sources, TABLE_TO_FIELDS[table])

    # Composition comparison
    exp_comp = expected_composition_total
    act_comp = actual.get("custom_composition", 0)
    if exp_comp != act_comp:
        all_match = False
        result["mismatches"].append(
            f"custom_composition: expected={exp_comp}, actual={act_comp}"
        )
    if verbose:
        comp_status = "OK" if exp_comp == act_comp else "MISMATCH"
        print(
            f"  custom_composition: expected={exp_comp}, actual={act_comp} [{comp_status}]"
        )
        if exp_comp != act_comp:
            print("    Composition breakdown:")
            for comp_type, count in expected_compositions.items():
                if count > 0:
                    print(f"      - {comp_type}: {count}")

    # Composition sections comparison
    exp_sec = expected_sections
    act_sec = actual.get("custom_composition_section", 0)
    if exp_sec != act_sec:
        all_match = False
        result["mismatches"].append(
            f"custom_composition_section: expected={exp_sec}, actual={act_sec}"
        )
    if verbose:
        sec_status = "OK" if exp_sec == act_sec else "MISMATCH"
        print(
            f"  custom_composition_section: expected={exp_sec}, actual={act_sec} [{sec_status}]"
        )

    result["status"] = "PASS" if all_match else "FAIL"

    if verbose:
        print("\n" + "=" * 60)
        if all_match:
            print("RESULT: All counts match!")
        else:
            print("RESULT: Some counts DO NOT match!")
        print("=" * 60)

    return result


def main():
    # Load JSON data
    with open(JSON_FILE_PATH, "r") as f:
        data = json.load(f)

    updates = data.get("updates", [])
    if not updates:
        print("No updates found in JSON file")
        return

    if not CONNECTION_STRING:
        print("[ERROR] CONNECTION_STRING not set.")
        return

    engine = create_engine(CONNECTION_STRING)

    if RUN_ALL:
        # Run for all encounters
        print(f"Running coherence check for ALL {len(updates)} encounters...\n")

        results = []
        for i, update in enumerate(updates):
            print(f"\n[{i+1}/{len(updates)}] Checking encounter...")
            result = check_single_encounter(update, engine, verbose=True)
            results.append(result)
            print()

        # Print summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        passed = [r for r in results if r["status"] == "PASS"]
        failed = [r for r in results if r["status"] == "FAIL"]
        errors = [r for r in results if r["status"] == "ERROR"]

        print(f"Total: {len(results)}")
        print(f"  PASS:  {len(passed)}")
        print(f"  FAIL:  {len(failed)}")
        print(f"  ERROR: {len(errors)}")

        if failed:
            print("\n--- Failed Encounters ---")
            for r in failed:
                print(f"  [{r['noregistrasi']}] {r['norec']}")
                for m in r["mismatches"]:
                    print(f"      - {m}")

        if errors:
            print("\n--- Errors ---")
            for r in errors:
                print(f"  [{r['noregistrasi']}] {r['error']}")

        print("=" * 60)
    else:
        # Run for single encounter
        update = updates[ENCOUNTER_INDEX]
        check_single_encounter(update, engine, verbose=True)


if __name__ == "__main__":
    main()
