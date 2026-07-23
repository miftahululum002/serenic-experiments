import argparse
import json
import sys
import io
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from utils.query import find_encounter_by_noregistrasi, get_connection, ORGANIZATION_ID
from utils.logger import get_logger

log = get_logger("verify_data_rsst")

# ===== Mapping: source fields -> resource table =====
RESOURCE_FIELDS = {
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
        ("diagnosis_awal", None),
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

# ===== Counting functions =====


def count_items_from_source(sources, source_name, field_name):
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


def count_expected_resources(sources, field_mappings):
    total = 0
    for source_name, field_name in field_mappings:
        total += count_items_from_source(sources, source_name, field_name)
    return total


def count_expected_compositions(sources):
    counts = {
        "emergency-triage": 0,
        "soap": 0,
        "initial-assessment": 0,
        "resume": 0,
        "operations-report": 0,
        "active-diagnosis": 0,
        "initial-diagnosis": 0,
    }

    if sources.get("triase_igd") and len(sources["triase_igd"]) > 0:
        counts["emergency-triage"] = 1

    if sources.get("cppt"):
        for unit in sources["cppt"]:
            if any(
                unit.get(f)
                for f in ["soap", "observasi", "diagnosis", "prosedur", "obat"]
            ):
                counts["soap"] += 1

    asesmen_fields = [
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
            if any(unit.get(f) for f in asesmen_fields):
                counts["initial-assessment"] += 1

    resume_fields = [
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
            if any(unit.get(f) for f in resume_fields):
                counts["resume"] += 1

    operasi_fields = [
        "pemesanan",
        "diagnosis_pra_bedah",
        "diagnosis_pasca_bedah",
        "prosedur_operasi",
        "laporan_operasi",
    ]
    if sources.get("laporan_operasi"):
        for unit in sources["laporan_operasi"]:
            if any(unit.get(f) for f in operasi_fields):
                counts["operations-report"] += 1

    if sources.get("diagnosis_aktif") and len(sources["diagnosis_aktif"]) > 0:
        counts["active-diagnosis"] = 1

    if sources.get("diagnosis_awal"):
        diagnosis_awal = sources["diagnosis_awal"]
        if isinstance(diagnosis_awal, list) and len(diagnosis_awal) > 0:
            counts["initial-diagnosis"] = 1

    return counts


def count_expected_composition_sections(sources):
    total = 0

    # triase_igd -> sections per observation
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

    # diagnosis_awal -> 1 section per diagnosis (initial-diagnosis composition)
    if sources.get("diagnosis_awal"):
        diagnosis_awal = sources["diagnosis_awal"]
        if isinstance(diagnosis_awal, list):
            total += len(diagnosis_awal)

    return total


# ===== DB query functions =====


def get_db_resource_counts(encounter_id):
    conn = get_connection()
    cur = conn.cursor()
    tables = [
        "custom_observation",
        "custom_clinical_impression",
        "custom_diagnosis",
        "custom_procedure",
        "custom_diagnostic_report",
        "custom_medication",
        "custom_medication_request",
        "custom_medication_dispense",
        "custom_medication_statement",
        "custom_family_member_history",
        "custom_service_request",
        "custom_billing",
    ]

    counts = {}
    for table in tables:
        if table == "custom_family_member_history":
            cur.execute("SELECT patient FROM encounter WHERE id = %s", (encounter_id,))
            row = cur.fetchone()
            patient_id = row[0] if row else None
            if patient_id:
                cur.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE patient_id = %s", (patient_id,)
                )
            else:
                cur.execute("SELECT 0")
        else:
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE encounter_id = %s", (encounter_id,)
            )
        counts[table] = cur.fetchone()[0]

    cur.close()
    conn.close()
    return counts


def get_db_composition_counts_by_type(encounter_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT type, COUNT(*) FROM custom_composition WHERE encounter_id = %s GROUP BY type",
        (encounter_id,),
    )
    counts = defaultdict(int)
    for row in cur.fetchall():
        counts[row[0]] = row[1]
    cur.close()
    conn.close()
    return dict(counts)


def get_db_composition_section_count(encounter_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM custom_composition_section ccs
        JOIN custom_composition cc ON ccs.composition_id = cc.id
        WHERE cc.encounter_id = %s
        """,
        (encounter_id,),
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count


# ===== Verification =====


def verify_encounter(enc, out=print):
    noreg = enc.get("noregistrasi")
    sources = enc.get("sources", {})

    encounter_id = find_encounter_by_noregistrasi(noreg)
    if not encounter_id:
        out(f"[{noreg}] Encounter NOT FOUND in DB")
        return "NOT_FOUND"

    out(f"[{noreg}] encounter_id: {encounter_id}")

    all_ok = True

    # --- Level 1: Resource counts ---
    out("\n  --- Resource Counts ---")
    expected_resource_totals = {}
    db_resource_counts = get_db_resource_counts(encounter_id)

    for table, field_mappings in RESOURCE_FIELDS.items():
        expected_count = count_expected_resources(sources, field_mappings)
        expected_resource_totals[table] = expected_count
        db_count = db_resource_counts.get(table, 0)
        status = "OK" if expected_count == db_count else "MISMATCH"
        if status == "MISMATCH":
            all_ok = False
        out(f"  {table:35} payload={expected_count:>3}  db={db_count:>3}  {status}")

    # --- Level 2: Composition counts by type ---
    out("\n  --- Compositions ---")
    expected_compositions = count_expected_compositions(sources)
    db_compositions = get_db_composition_counts_by_type(encounter_id)
    expected_comp_total = sum(expected_compositions.values())
    db_comp_total = sum(db_compositions.values())

    for comp_type, exp_count in expected_compositions.items():
        db_count = db_compositions.get(comp_type, 0)
        status = "OK" if exp_count == db_count else "MISMATCH"
        if status == "MISMATCH":
            all_ok = False
        out(f"  {comp_type:35} expected={exp_count:>3}  db={db_count:>3}  {status}")

    comp_total_status = "OK" if expected_comp_total == db_comp_total else "MISMATCH"
    if comp_total_status == "MISMATCH":
        all_ok = False
    out(
        f"  {'TOTAL':35} expected={expected_comp_total:>3}  db={db_comp_total:>3}  {comp_total_status}"
    )

    # --- Level 3: Composition section counts ---
    out("\n  --- Composition Sections ---")
    expected_sections = count_expected_composition_sections(sources)
    db_sections = get_db_composition_section_count(encounter_id)
    sec_status = "OK" if expected_sections == db_sections else "MISMATCH"
    if sec_status == "MISMATCH":
        all_ok = False
    out(
        f"  {'total sections':35} expected={expected_sections:>3}  db={db_sections:>3}  {sec_status}"
    )

    # --- Summary ---
    overall = "PASS" if all_ok else "FAIL"
    out(f"\n  RESULT: {overall}")
    return overall


def verify(filepath):
    fp = Path(filepath)
    log.info("Loading payload from %s", fp)
    with open(fp) as f:
        data = json.load(f)

    if isinstance(data, list):
        items = data
    else:
        items = [data]

    output_lines = []

    def out(line=""):
        print(line)
        output_lines.append(line)

    out(f"File: {filepath}")
    out(f"Total records: {len(items)}")
    out(f"Organization: {ORGANIZATION_ID}")
    out()

    results = []
    for i, enc in enumerate(items, 1):
        out(f"{'=' * 70}")
        out(f"[{i}/{len(items)}]")
        result = verify_encounter(enc, out)
        results.append(result)
        out()

    # Final summary
    out("=" * 70)
    out("SUMMARY")
    out("=" * 70)
    passed = results.count("PASS")
    failed = results.count("FAIL")
    not_found = results.count("NOT_FOUND")
    out(f"  Total : {len(results)}")
    out(f"  PASS  : {passed}")
    out(f"  FAIL  : {failed}")
    out(f"  MISSING : {not_found}")
    out("=" * 70)

    # Write markdown file
    verify_dir = Path("verify_check")
    verify_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = verify_dir / f"verify_{timestamp}.md"

    md_lines = [
        "# Verify Data Result\n",
        f"- **File**: `{filepath}`",
        f"- **Organization**: `{ORGANIZATION_ID}`",
        f"- **Timestamp**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Total Records**: {len(items)}",
        "",
        "## Output\n",
        "```",
        *output_lines,
        "```\n",
        "## Summary\n",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total | {len(results)} |",
        f"| PASS | {passed} |",
        f"| FAIL | {failed} |",
        f"| MISSING | {not_found} |",
    ]

    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    out(f"\nMarkdown report saved to: {md_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verify payload data vs database (resource, composition, section counts)"
    )
    parser.add_argument(
        "--file", type=str, required=True, help="Path to update encounters JSON file"
    )
    args = parser.parse_args()
    verify(args.file)
