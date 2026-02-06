import json
from sqlalchemy import create_engine, text
from collections import defaultdict

# ===== Configuration =====
CONFIG_FILE_PATH = "/home/aldo_serenic_ai/_experiments/2026-02-04 - Test Ingestion Data Coherence/config.env"

# Input: Two JSON file paths
JSON_FILE_PATH_1 = "1_djamil/1_update_encounters_filtered.json"  # First/earlier update file
JSON_FILE_PATH_2 = "1_djamil/2_update_encounters.json"  # Second/later update file (the one being checked)


def load_config(config_path: str) -> dict:
    config = {}
    with open(config_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    return config


CONFIG = load_config(CONFIG_FILE_PATH)
CONNECTION_STRING = CONFIG.get('CONNECTION_STRING', '')

# Buffer: 1 diagnosis is added when encounter is initialized (SEP diagnosis)
CUSTOM_DIAGNOSIS_BUFFER = 1

# ===== Mappings: source fields to resource types =====
OBSERVATION_FIELDS = [
    ('triase_igd', None),
    ('cppt', 'observasi'),
    ('asesmen_awal', 'pemeriksaan_fisik'),
    ('asesmen_awal', 'hasil_lab'),
    ('resume_medis', 'pemeriksaan_fisik'),
    ('resume_medis', 'hasil_lab'),
    ('penunjang_lab', 'hasil_lab'),
    ('prosedur_medis_lain', 'hasil_prosedur'),
]

CLINICAL_IMPRESSION_FIELDS = [
    ('cppt', 'soap'),
    ('asesmen_awal', 'keluhan'),
    ('asesmen_awal', 'riwayat_penyakit_dahulu'),
    ('asesmen_awal', 'riwayat_penyakit_sekarang'),
    ('asesmen_awal', 'assessment_text'),
    ('resume_medis', 'keluhan'),
    ('resume_medis', 'riwayat_penyakit_dahulu'),
    ('resume_medis', 'riwayat_penyakit_sekarang'),
    ('resume_medis', 'resume_text'),
]

DIAGNOSIS_FIELDS = [
    ('cppt', 'diagnosis'),
    ('asesmen_awal', 'diagnosis'),
    ('resume_medis', 'diagnosis'),
    ('laporan_operasi', 'diagnosis_pra_bedah'),
    ('laporan_operasi', 'diagnosis_pasca_bedah'),
    ('diagnosis_aktif', None),
]

PROCEDURE_FIELDS = [
    ('cppt', 'prosedur'),
    ('asesmen_awal', 'prosedur'),
    ('resume_medis', 'prosedur'),
    ('laporan_operasi', 'prosedur_operasi'),
]

DIAGNOSTIC_REPORT_FIELDS = [
    ('asesmen_awal', 'laporan_radiologi'),
    ('asesmen_awal', 'laporan_lab'),
    ('resume_medis', 'laporan_radiologi'),
    ('resume_medis', 'laporan_lab'),
    ('penunjang_lab', 'laporan_lab'),
    ('penunjang_radiologi', 'laporan_radiologi'),
    ('laporan_operasi', 'laporan_operasi'),
    ('prosedur_medis_lain', 'laporan_prosedur'),
]

MEDICATION_FIELDS = [
    ('cppt', 'obat'),
    ('resume_medis', 'obat'),
    ('obat', 'obat'),
]

MEDICATION_REQUEST_FIELDS = [('obat', 'resep_obat')]
MEDICATION_DISPENSE_FIELDS = [('obat', 'pengeluaran_obat')]
MEDICATION_STATEMENT_FIELDS = [
    ('asesmen_awal', 'riwayat_penggunaan_obat'),
    ('resume_medis', 'riwayat_penggunaan_obat'),
]
FAMILY_MEMBER_HISTORY_FIELDS = [
    ('asesmen_awal', 'riwayat_penyakit_keluarga'),
    ('resume_medis', 'riwayat_penyakit_keluarga'),
]
SERVICE_REQUEST_FIELDS = [
    ('penunjang_lab', 'pemesanan'),
    ('penunjang_radiologi', 'pemesanan'),
    ('laporan_operasi', 'pemesanan'),
    ('prosedur_medis_lain', 'pemesanan'),
]
BILLING_FIELDS = [('billing', None)]

TABLE_TO_FIELDS = {
    'custom_observation': OBSERVATION_FIELDS,
    'custom_clinical_impression': CLINICAL_IMPRESSION_FIELDS,
    'custom_diagnosis': DIAGNOSIS_FIELDS,
    'custom_procedure': PROCEDURE_FIELDS,
    'custom_diagnostic_report': DIAGNOSTIC_REPORT_FIELDS,
    'custom_medication': MEDICATION_FIELDS,
    'custom_medication_request': MEDICATION_REQUEST_FIELDS,
    'custom_medication_dispense': MEDICATION_DISPENSE_FIELDS,
    'custom_medication_statement': MEDICATION_STATEMENT_FIELDS,
    'custom_family_member_history': FAMILY_MEMBER_HISTORY_FIELDS,
    'custom_service_request': SERVICE_REQUEST_FIELDS,
    'custom_billing': BILLING_FIELDS,
}


def count_items_from_source(sources: dict, source_name: str, field_name: str | None) -> int:
    if source_name not in sources:
        return 0
    source_data = sources[source_name]
    if source_data is None:
        return 0
    if field_name is None:
        return len(source_data) if isinstance(source_data, list) else 0
    total = 0
    if isinstance(source_data, list):
        for unit in source_data:
            if isinstance(unit, dict) and field_name in unit:
                items = unit.get(field_name) or []
                total += len(items)
    return total


def count_expected_resources(sources: dict, field_mappings: list) -> int:
    total = 0
    for source_name, field_name in field_mappings:
        total += count_items_from_source(sources, source_name, field_name)
    return total


def count_expected_compositions(sources: dict) -> dict:
    counts = {
        'emergency_triage': 0, 'soap': 0, 'initial_assessment': 0,
        'resume': 0, 'operations_report': 0, 'active_diagnosis': 0,
    }
    if sources.get('triase_igd') and len(sources['triase_igd']) > 0:
        counts['emergency_triage'] = 1
    if sources.get('cppt'):
        for unit in sources['cppt']:
            if any(unit.get(f) for f in ['soap', 'observasi', 'diagnosis', 'prosedur', 'obat']):
                counts['soap'] += 1
    if sources.get('asesmen_awal'):
        fields = ['keluhan', 'riwayat_penyakit_dahulu', 'riwayat_penyakit_sekarang',
                  'riwayat_penyakit_keluarga', 'riwayat_penggunaan_obat', 'pemeriksaan_fisik',
                  'hasil_lab', 'laporan_radiologi', 'laporan_lab', 'assessment_text', 'diagnosis', 'prosedur']
        for unit in sources['asesmen_awal']:
            if any(unit.get(f) for f in fields):
                counts['initial_assessment'] += 1
    if sources.get('resume_medis'):
        fields = ['keluhan', 'riwayat_penyakit_dahulu', 'riwayat_penyakit_sekarang',
                  'riwayat_penyakit_keluarga', 'riwayat_penggunaan_obat', 'pemeriksaan_fisik',
                  'hasil_lab', 'laporan_radiologi', 'laporan_lab', 'diagnosis', 'prosedur', 'obat', 'resume_text']
        for unit in sources['resume_medis']:
            if any(unit.get(f) for f in fields):
                counts['resume'] += 1
    if sources.get('laporan_operasi'):
        fields = ['pemesanan', 'diagnosis_pra_bedah', 'diagnosis_pasca_bedah', 'prosedur_operasi', 'laporan_operasi']
        for unit in sources['laporan_operasi']:
            if any(unit.get(f) for f in fields):
                counts['operations_report'] += 1
    if sources.get('diagnosis_aktif') and len(sources['diagnosis_aktif']) > 0:
        counts['active_diagnosis'] = 1
    return counts


def count_expected_composition_sections(sources: dict) -> int:
    total = 0
    if sources.get('triase_igd'):
        total += len(sources['triase_igd'])
    section_fields_cppt = ['soap', 'observasi', 'diagnosis', 'prosedur', 'obat']
    if sources.get('cppt'):
        for unit in sources['cppt']:
            for field in section_fields_cppt:
                total += len(unit.get(field) or [])
    section_fields_asesmen = ['keluhan', 'riwayat_penyakit_dahulu', 'riwayat_penyakit_sekarang',
                              'riwayat_penyakit_keluarga', 'riwayat_penggunaan_obat', 'pemeriksaan_fisik',
                              'hasil_lab', 'laporan_radiologi', 'laporan_lab', 'assessment_text', 'diagnosis', 'prosedur']
    if sources.get('asesmen_awal'):
        for unit in sources['asesmen_awal']:
            for field in section_fields_asesmen:
                total += len(unit.get(field) or [])
    section_fields_resume = ['keluhan', 'riwayat_penyakit_dahulu', 'riwayat_penyakit_sekarang',
                             'riwayat_penyakit_keluarga', 'riwayat_penggunaan_obat', 'pemeriksaan_fisik',
                             'hasil_lab', 'laporan_radiologi', 'laporan_lab', 'diagnosis', 'prosedur', 'obat', 'resume_text']
    if sources.get('resume_medis'):
        for unit in sources['resume_medis']:
            for field in section_fields_resume:
                total += len(unit.get(field) or [])
    section_fields_operasi = ['pemesanan', 'diagnosis_pra_bedah', 'diagnosis_pasca_bedah', 'prosedur_operasi', 'laporan_operasi']
    if sources.get('laporan_operasi'):
        for unit in sources['laporan_operasi']:
            for field in section_fields_operasi:
                total += len(unit.get(field) or [])
    if sources.get('diagnosis_aktif'):
        total += len(sources['diagnosis_aktif'])
    return total


def get_source_breakdown(sources: dict, field_mappings: list) -> dict:
    breakdown = {}
    for source_name, field_name in field_mappings:
        count = count_items_from_source(sources, source_name, field_name)
        if count > 0:
            key = f"{source_name}.{field_name}" if field_name else source_name
            breakdown[key] = count
    return breakdown


def get_encounter_id_from_noregistrasi(engine, noregistrasi: str) -> str | None:
    query = text("SELECT id FROM encounter WHERE id_in_organization = :noreg LIMIT 1")
    with engine.connect() as conn:
        result = conn.execute(query, {'noreg': noregistrasi}).fetchone()
        return result[0] if result else None


def get_db_counts(engine, encounter_id: str) -> dict:
    tables = {
        'custom_observation': 'encounter_id',
        'custom_clinical_impression': 'encounter_id',
        'custom_diagnosis': 'encounter_id',
        'custom_procedure': 'encounter_id',
        'custom_diagnostic_report': 'encounter_id',
        'custom_medication': 'encounter_id',
        'custom_medication_request': 'encounter_id',
        'custom_medication_dispense': 'encounter_id',
        'custom_medication_statement': 'encounter_id',
        'custom_family_member_history': 'patient_id',
        'custom_service_request': 'encounter_id',
        'custom_billing': 'encounter_id',
        'custom_composition': 'encounter_id',
        'custom_composition_section': None,
    }
    counts = {}
    with engine.connect() as conn:
        patient_query = text("SELECT patient FROM encounter WHERE id = :enc_id")
        patient_result = conn.execute(patient_query, {'enc_id': encounter_id}).fetchone()
        patient_id = patient_result[0] if patient_result else None
        for table, filter_col in tables.items():
            if table == 'custom_family_member_history':
                if patient_id:
                    query = text(f"SELECT COUNT(*) FROM {table} WHERE {filter_col} = :id")
                    result = conn.execute(query, {'id': patient_id}).fetchone()
                else:
                    result = (0,)
            elif table == 'custom_composition_section':
                query = text("""
                    SELECT COUNT(*) FROM custom_composition_section ccs
                    JOIN custom_composition cc ON ccs.composition_id = cc.id
                    WHERE cc.encounter_id = :enc_id
                """)
                result = conn.execute(query, {'enc_id': encounter_id}).fetchone()
            else:
                query = text(f"SELECT COUNT(*) FROM {table} WHERE {filter_col} = :enc_id")
                result = conn.execute(query, {'enc_id': encounter_id}).fetchone()
            counts[table] = result[0] if result else 0
    return counts


def get_composition_counts_by_type(engine, encounter_id: str) -> dict:
    query = text("SELECT type, COUNT(*) as cnt FROM custom_composition WHERE encounter_id = :enc_id GROUP BY type")
    counts = defaultdict(int)
    with engine.connect() as conn:
        results = conn.execute(query, {'enc_id': encounter_id}).fetchall()
        for row in results:
            counts[row[0]] = row[1]
    return dict(counts)


def compute_expected(sources: dict) -> dict:
    """Compute expected counts from sources."""
    return {
        'custom_observation': count_expected_resources(sources, OBSERVATION_FIELDS),
        'custom_clinical_impression': count_expected_resources(sources, CLINICAL_IMPRESSION_FIELDS),
        'custom_diagnosis': count_expected_resources(sources, DIAGNOSIS_FIELDS),
        'custom_procedure': count_expected_resources(sources, PROCEDURE_FIELDS),
        'custom_diagnostic_report': count_expected_resources(sources, DIAGNOSTIC_REPORT_FIELDS),
        'custom_medication': count_expected_resources(sources, MEDICATION_FIELDS),
        'custom_medication_request': count_expected_resources(sources, MEDICATION_REQUEST_FIELDS),
        'custom_medication_dispense': count_expected_resources(sources, MEDICATION_DISPENSE_FIELDS),
        'custom_medication_statement': count_expected_resources(sources, MEDICATION_STATEMENT_FIELDS),
        'custom_family_member_history': count_expected_resources(sources, FAMILY_MEMBER_HISTORY_FIELDS),
        'custom_service_request': count_expected_resources(sources, SERVICE_REQUEST_FIELDS),
        'custom_billing': count_expected_resources(sources, BILLING_FIELDS),
    }


def check_encounter_new(update: dict, engine, verbose: bool = True) -> dict:
    """Check an encounter that only exists in file 2 (new encounter)."""
    norec = update.get('norec')
    noregistrasi = update.get('noregistrasi')
    sources = update.get('sources', {})
    
    result = {'norec': norec, 'noregistrasi': noregistrasi, 'status': 'UNKNOWN', 'mismatches': [], 'error': None, 'type': 'NEW'}
    
    if verbose:
        print("=" * 60)
        print(f"[NEW] Norec: {norec}")
        print(f"      Noregistrasi: {noregistrasi}")
        print("=" * 60)
    
    expected = compute_expected(sources)
    expected['custom_diagnosis'] += CUSTOM_DIAGNOSIS_BUFFER
    expected_compositions = count_expected_compositions(sources)
    expected_composition_total = sum(expected_compositions.values())
    expected_sections = count_expected_composition_sections(sources)
    
    if verbose:
        print("\n--- Expected Counts from JSON ---")
        for table, count in expected.items():
            print(f"  {table}: {count}")
        print(f"\n  Compositions (total): {expected_composition_total}")
        print(f"  Composition Sections (total): {expected_sections}")
    
    encounter_id = get_encounter_id_from_noregistrasi(engine, noregistrasi)
    if not encounter_id:
        result['status'] = 'ERROR'
        result['error'] = f"No encounter found with noregistrasi: {noregistrasi}"
        if verbose:
            print(f"\n[ERROR] {result['error']}")
        return result
    
    if verbose:
        print(f"\nEncounter ID (from DB): {encounter_id}")
    
    actual = get_db_counts(engine, encounter_id)
    
    if verbose:
        print("\n--- Actual Counts from DB ---")
        for table, count in actual.items():
            print(f"  {table}: {count}")
    
    if verbose:
        print("\n--- Comparison ---")
    
    all_match = True
    for table in expected:
        exp = expected[table]
        act = actual.get(table, 0)
        if exp != act:
            all_match = False
            result['mismatches'].append(f"{table}: expected={exp}, actual={act}")
        if verbose:
            status = "OK" if exp == act else "MISMATCH"
            print(f"  {table}: expected={exp}, actual={act} [{status}]")
    
    exp_comp = expected_composition_total
    act_comp = actual.get('custom_composition', 0)
    if exp_comp != act_comp:
        all_match = False
        result['mismatches'].append(f"custom_composition: expected={exp_comp}, actual={act_comp}")
    if verbose:
        status = "OK" if exp_comp == act_comp else "MISMATCH"
        print(f"  custom_composition: expected={exp_comp}, actual={act_comp} [{status}]")
    
    exp_sec = expected_sections
    act_sec = actual.get('custom_composition_section', 0)
    if exp_sec != act_sec:
        all_match = False
        result['mismatches'].append(f"custom_composition_section: expected={exp_sec}, actual={act_sec}")
    if verbose:
        status = "OK" if exp_sec == act_sec else "MISMATCH"
        print(f"  custom_composition_section: expected={exp_sec}, actual={act_sec} [{status}]")
    
    result['status'] = 'PASS' if all_match else 'FAIL'
    if verbose:
        print("\n" + "=" * 60)
        print(f"RESULT: {'All counts match!' if all_match else 'Some counts DO NOT match!'}")
        print("=" * 60)
    
    return result


def check_encounter_existing(update1: dict, update2: dict, engine, verbose: bool = True) -> dict:
    """Check an encounter that exists in both files (existing encounter with updates)."""
    norec = update2.get('norec')
    noregistrasi = update2.get('noregistrasi')
    sources1 = update1.get('sources', {})
    sources2 = update2.get('sources', {})
    
    result = {'norec': norec, 'noregistrasi': noregistrasi, 'status': 'UNKNOWN', 'mismatches': [], 'error': None, 'type': 'EXISTING'}
    
    if verbose:
        print("=" * 60)
        print(f"[EXISTING] Norec: {norec}")
        print(f"           Noregistrasi: {noregistrasi}")
        print("=" * 60)
    
    # Compute expected from both files
    expected1 = compute_expected(sources1)
    expected2 = compute_expected(sources2)
    
    # Use only file2 expected counts
    expected_combined = expected2.copy()
    expected_combined['custom_diagnosis'] += CUSTOM_DIAGNOSIS_BUFFER
    
    # Compositions
    compositions1 = count_expected_compositions(sources1)
    compositions2 = count_expected_compositions(sources2)
    sections1 = count_expected_composition_sections(sources1)
    sections2 = count_expected_composition_sections(sources2)
    
    # Use only file2 composition counts
    expected_compositions_combined = compositions2
    
    expected_composition_total = sum(expected_compositions_combined.values())
    expected_sections_total = sections2
    
    if verbose:
        print("\n--- Expected Counts from File 1 ---")
        for table, count in expected1.items():
            print(f"  {table}: {count}")
        print(f"  Compositions: {sum(compositions1.values())}")
        print(f"  Sections: {sections1}")
        
        print("\n--- Expected Counts from File 2 ---")
        for table, count in expected2.items():
            print(f"  {table}: {count}")
        print(f"  Compositions: {sum(compositions2.values())}")
        print(f"  Sections: {sections2}")
        
        print("\n--- Expected (File2 + Buffer) ---")
        for table, count in expected_combined.items():
            print(f"  {table}: {count}")
        print(f"  Compositions (total): {expected_composition_total}")
        print(f"  Sections (total): {expected_sections_total}")
    
    encounter_id = get_encounter_id_from_noregistrasi(engine, noregistrasi)
    if not encounter_id:
        result['status'] = 'ERROR'
        result['error'] = f"No encounter found with noregistrasi: {noregistrasi}"
        if verbose:
            print(f"\n[ERROR] {result['error']}")
        return result
    
    if verbose:
        print(f"\nEncounter ID (from DB): {encounter_id}")
    
    actual = get_db_counts(engine, encounter_id)
    
    if verbose:
        print("\n--- Actual Counts from DB ---")
        for table, count in actual.items():
            print(f"  {table}: {count}")
    
    if verbose:
        print("\n--- Comparison ---")
    
    all_match = True
    for table in expected_combined:
        exp = expected_combined[table]
        act = actual.get(table, 0)
        if exp != act:
            all_match = False
            result['mismatches'].append(f"{table}: expected={exp} (f2:{expected2[table]}), actual={act}")
        if verbose:
            status = "OK" if exp == act else "MISMATCH"
            print(f"  {table}: expected={exp} (f2:{expected2[table]}), actual={act} [{status}]")
    
    exp_comp = expected_composition_total
    act_comp = actual.get('custom_composition', 0)
    if exp_comp != act_comp:
        all_match = False
        result['mismatches'].append(f"custom_composition: expected={exp_comp}, actual={act_comp}")
    if verbose:
        status = "OK" if exp_comp == act_comp else "MISMATCH"
        print(f"  custom_composition: expected={exp_comp} (f2:{sum(compositions2.values())}), actual={act_comp} [{status}]")
    
    exp_sec = expected_sections_total
    act_sec = actual.get('custom_composition_section', 0)
    if exp_sec != act_sec:
        all_match = False
        result['mismatches'].append(f"custom_composition_section: expected={exp_sec}, actual={act_sec}")
    if verbose:
        status = "OK" if exp_sec == act_sec else "MISMATCH"
        print(f"  custom_composition_section: expected={exp_sec} (f2:{sections2}), actual={act_sec} [{status}]")
    
    result['status'] = 'PASS' if all_match else 'FAIL'
    if verbose:
        print("\n" + "=" * 60)
        print(f"RESULT: {'All counts match!' if all_match else 'Some counts DO NOT match!'}")
        print("=" * 60)
    
    return result


def main():
    if not JSON_FILE_PATH_1 or not JSON_FILE_PATH_2:
        print("[ERROR] Set JSON_FILE_PATH_1 and JSON_FILE_PATH_2 at the top of the script.")
        return
    
    if not CONNECTION_STRING:
        print("[ERROR] CONNECTION_STRING not set in config.")
        return
    
    # Load both files
    print(f"Loading File 1: {JSON_FILE_PATH_1}")
    with open(JSON_FILE_PATH_1, 'r') as f:
        data1 = json.load(f)
    updates1 = data1.get('updates', [])
    
    print(f"Loading File 2: {JSON_FILE_PATH_2}")
    with open(JSON_FILE_PATH_2, 'r') as f:
        data2 = json.load(f)
    updates2 = data2.get('updates', [])
    
    print(f"\nFile 1 encounters: {len(updates1)}")
    print(f"File 2 encounters: {len(updates2)}")
    
    # Build lookup for file 1 by noregistrasi
    file1_by_noreg = {u.get('noregistrasi'): u for u in updates1}
    
    engine = create_engine(CONNECTION_STRING)
    
    results = []
    new_count = 0
    existing_count = 0
    
    for i, update2 in enumerate(updates2):
        noregistrasi = update2.get('noregistrasi')
        print(f"\n[{i+1}/{len(updates2)}]")
        
        if noregistrasi in file1_by_noreg:
            # Encounter exists in both files
            existing_count += 1
            update1 = file1_by_noreg[noregistrasi]
            result = check_encounter_existing(update1, update2, engine, verbose=True)
        else:
            # Encounter only in file 2
            new_count += 1
            result = check_encounter_new(update2, engine, verbose=True)
        
        results.append(result)
        print()
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    passed = [r for r in results if r['status'] == 'PASS']
    failed = [r for r in results if r['status'] == 'FAIL']
    errors = [r for r in results if r['status'] == 'ERROR']
    
    print(f"Total: {len(results)} (NEW: {new_count}, EXISTING: {existing_count})")
    print(f"  PASS:  {len(passed)}")
    print(f"  FAIL:  {len(failed)}")
    print(f"  ERROR: {len(errors)}")
    
    if failed:
        print("\n--- Failed Encounters ---")
        for r in failed:
            print(f"  [{r['type']}] [{r['noregistrasi']}] {r['norec']}")
            for m in r['mismatches']:
                print(f"      - {m}")
    
    if errors:
        print("\n--- Errors ---")
        for r in errors:
            print(f"  [{r['type']}] [{r['noregistrasi']}] {r['error']}")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
