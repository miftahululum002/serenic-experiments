import psycopg2
from constant import BASE_PROJECT_PATH

CONFIG_FILE_PATH = BASE_PROJECT_PATH  # "/Users/miftahululum002/projects/serenic/experiments/test-ingestion-data-coherence/config.env"


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

SQL_SCRIPT = """
DO $$
DECLARE
    target_org_id TEXT := '38b9a1e9-37cb-4f51-98ca-59647832015a';
    deleted_count INTEGER;
BEGIN
    RAISE NOTICE 'Starting deletion for organization: %', target_org_id;

    DELETE FROM encounter_verification_comments WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_verification_comments', deleted_count;

    DELETE FROM encounter_verification_checkbox WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_verification_checkbox', deleted_count;

    DELETE FROM encounter_administrative_validation WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_administrative_validation', deleted_count;

    DELETE FROM encounter_additional_details WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_additional_details', deleted_count;

    DELETE FROM encounter_code_history WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_code_history', deleted_count;

    DELETE FROM encounter_code WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_code', deleted_count;

    DELETE FROM encounter_code_recommendation WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_code_recommendation', deleted_count;

    DELETE FROM encounter_code_group_checklist_validation_history WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_code_group_checklist_validation_history', deleted_count;

    DELETE FROM encounter_code_group_checklist_validation WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_code_group_checklist_validation', deleted_count;

    DELETE FROM encounter_code_group WHERE encounter_id IN (SELECT id FROM encounter WHERE managing_organization::text = target_org_id);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_code_group', deleted_count;

    DELETE FROM custom_medication_dispense WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_medication_dispense', deleted_count;

    DELETE FROM custom_medication_request WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_medication_request', deleted_count;

    DELETE FROM custom_medication WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_medication', deleted_count;

    DELETE FROM custom_medication_statement WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_medication_statement', deleted_count;

    DELETE FROM custom_observation WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_observation', deleted_count;

    DELETE FROM custom_procedure WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_procedure', deleted_count;

    DELETE FROM custom_diagnostic_report WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_diagnostic_report', deleted_count;

    DELETE FROM custom_composition_section WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_composition_section', deleted_count;

    DELETE FROM custom_composition WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_composition', deleted_count;

    DELETE FROM custom_service_request WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_service_request', deleted_count;

    DELETE FROM custom_clinical_impression WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_clinical_impression', deleted_count;

    DELETE FROM custom_billing WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_billing', deleted_count;

    DELETE FROM custom_family_member_history WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_family_member_history', deleted_count;

    DELETE FROM codex_manual_analysis WHERE parent_organization_id::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from codex_manual_analysis', deleted_count;

    UPDATE encounter SET sep_diagnosis = NULL WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Updated % rows in encounter (cleared sep_diagnosis)', deleted_count;

    DELETE FROM custom_diagnosis WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from custom_diagnosis', deleted_count;

    DELETE FROM encounter_location_history WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_location_history', deleted_count;

    DELETE FROM encounter_main_practitioner_history WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter_main_practitioner_history', deleted_count;

    DELETE FROM encounter WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from encounter', deleted_count;

    DELETE FROM location WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from location', deleted_count;

    DELETE FROM organizational_team WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from organizational_team', deleted_count;

    DELETE FROM practitioner WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from practitioner', deleted_count;

    DELETE FROM patient WHERE managing_organization::text = target_org_id;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % rows from patient', deleted_count;

    RAISE NOTICE '=== Completed deletion for organization: % ===', target_org_id;
END $$
"""

if __name__ == "__main__":
    conn = psycopg2.connect(CONNECTION_STRING)
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute(SQL_SCRIPT)
    cursor.close()
    conn.close()
    print("Done")
