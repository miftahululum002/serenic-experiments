import json
import os
from datetime import datetime
import hashlib

# [1] Define folders and inputs
# Configuration for each folder
FOLDER_CONFIGS = {
    "1_djamil": {
        "folder_large_number": "1_djamil/3_large_number",
        "input_update_json": "1_djamil/0_raw/update_encounters_20260108_071337.json",
        "input_new_json": "1_djamil/0_raw/new_encounters_20260108_164704.json"
    },
    "2_rsabhk": {
        "folder_large_number": "2_rsabhk/3_large_number",
        "input_update_json": "2_rsabhk/0_raw/update_encounters_20260205_013251_164811.json",
        "input_new_json": "2_rsabhk/0_raw/new_encounters_20260205_013003_909741.json"
    }
}

# Fields that reference location
LOCATION_FIELDS = ["location_id", "location", "organizational_team_id"]

# Fields that reference performer/practitioner
PERFORMER_FIELDS = [
    "performer_id", "dpjp_id", "requester_id", "recorder_id", 
    "asserter_id", "result_interpreter_id"
]

# Sample patient data
GENDERS = ["laki-laki", "perempuan"]
BIRTH_YEARS = list(range(1950, 2020))
BIRTH_MONTHS = list(range(1, 13))
BIRTH_DAYS = list(range(1, 29))

# Sample diagnosis codes
DIAGNOSIS_CODES = [
    ("R40.2", "Coma, unspecified"),
    ("T14", "Injury of unspecified body region"),
    ("H81.1", "Benign paroxysmal vertigo"),
    ("J18.9", "Pneumonia, unspecified organism"),
    ("I63.9", "Cerebral infarction, unspecified"),
    ("K92.2", "Gastrointestinal haemorrhage, unspecified"),
    ("N18.9", "Chronic kidney disease, unspecified"),
    ("E11.9", "Type 2 diabetes mellitus without complications"),
]

def generate_patient_info(noregistrasi):
    """Generate consistent patient info based on noregistrasi"""
    # Use hash to get consistent patient info for same noregistrasi
    hash_val = int(hashlib.md5(noregistrasi.encode()).hexdigest(), 16)
    
    gender = GENDERS[hash_val % len(GENDERS)]
    year = BIRTH_YEARS[hash_val % len(BIRTH_YEARS)]
    month = BIRTH_MONTHS[(hash_val // 100) % len(BIRTH_MONTHS)]
    day = BIRTH_DAYS[(hash_val // 10000) % len(BIRTH_DAYS)]
    
    date_of_birth = f"{year:04d}-{month:02d}-{day:02d}"
    
    return {
        "dateOfBirth": date_of_birth,
        "gender": gender
    }

def generate_sep_data(noregistrasi):
    """Generate consistent sep_data based on noregistrasi"""
    hash_val = int(hashlib.md5(noregistrasi.encode()).hexdigest(), 16)
    
    # Generate nosep
    nosep_suffix = str(hash_val)[:6]
    nosep = f"0301R0010126V{nosep_suffix}"
    
    # Generate kelasBPJS (1, 2, or 3)
    kelas_bpjs = (hash_val % 3) + 1
    
    # Select diagnosis
    diagnosis_code, diagnosis_text = DIAGNOSIS_CODES[hash_val % len(DIAGNOSIS_CODES)]
    
    return {
        "nosep": nosep,
        "kelasBPJS": kelas_bpjs,
        "sep_diagnosis_code": diagnosis_code,
        "sep_diagnosis_text": diagnosis_text
    }

def replace_ids_recursive(obj, field_names, replacement="0-test"):
    """Recursively replace ID fields in nested structures"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in field_names:
                if isinstance(value, str):
                    obj[key] = replacement
                elif isinstance(value, dict) and "id" in value:
                    value["id"] = replacement
            else:
                replace_ids_recursive(value, field_names, replacement)
    elif isinstance(obj, list):
        for item in obj:
            replace_ids_recursive(item, field_names, replacement)

def delete_items_from_sources(sources, encounter_id, delete_ratio=0.3):
    """Delete some items from sources and log deletions, maintaining internal references"""
    log = []
    
    for source_name, source_data in sources.items():
        # Handle list of items (e.g., triase_igd, billing)
        if isinstance(source_data, list):
            # Check if it's a list of data source units (like cppt, asesmen_awal)
            if len(source_data) > 0 and isinstance(source_data[0], dict):
                # Check if first item has sections (not just id field)
                first_item_keys = set(source_data[0].keys())
                is_data_source_unit = not ('id' in first_item_keys and len(first_item_keys) < 5)
                
                if is_data_source_unit:
                    # Process each unit in the list (e.g., each cppt entry)
                    for unit_idx, unit in enumerate(source_data):
                        deleted_ids = set()
                        
                        # Delete items and track deleted IDs
                        for section_name, section_items in list(unit.items()):
                            if isinstance(section_items, list) and len(section_items) > 1:
                                num_to_delete = max(1, int(len(section_items) * delete_ratio))
                                deleted = section_items[:num_to_delete]
                                unit[section_name] = section_items[num_to_delete:]
                                
                                for item in deleted:
                                    item_id = item.get('id', 'N/A')
                                    if item_id != 'N/A':
                                        deleted_ids.add(item_id)
                                
                                if deleted:
                                    log.append(f"Encounter '{encounter_id}' - Source '{source_name}[{unit_idx}]' section '{section_name}': Deleted {num_to_delete} items")
                                    for item in deleted:
                                        item_id = item.get('id', 'N/A')
                                        log.append(f"    - Deleted item id: {item_id}")
                        
                        # Clean up references to deleted items
                        clean_references_in_unit(unit, deleted_ids, log, encounter_id, source_name, unit_idx)
                else:
                    # Simple list of items with IDs
                    if len(source_data) > 1:
                        num_to_delete = max(1, int(len(source_data) * delete_ratio))
                        deleted = source_data[:num_to_delete]
                        sources[source_name] = source_data[num_to_delete:]
                        log.append(f"Encounter '{encounter_id}' - Source '{source_name}': Deleted {num_to_delete} items")
                        for item in deleted:
                            item_id = item.get('id', 'N/A')
                            log.append(f"    - Deleted item id: {item_id}")
    
    return log

def clean_references_in_unit(unit, deleted_ids, log, encounter_id, source_name, unit_idx):
    """Remove items that reference deleted items"""
    reference_fields = {
        'medication_id', 'medication_request_id', 'service_request_id', 
        'procedure_id', 'performer_id', 'requester_id'
    }
    
    for section_name, section_items in list(unit.items()):
        if isinstance(section_items, list):
            original_count = len(section_items)
            # Filter out items that reference deleted items
            section_items[:] = [
                item for item in section_items
                if not any(
                    item.get(ref_field) in deleted_ids 
                    for ref_field in reference_fields
                )
            ]
            removed_count = original_count - len(section_items)
            if removed_count > 0:
                log.append(f"    - Cleaned {removed_count} items from '{section_name}' that referenced deleted items")

# [2-1] Construct first part - take 125 updates with deletions
def construct_first_part(folder_name, config):
    print(f"\n{'='*80}")
    print(f"Processing folder: {folder_name}")
    print(f"{'='*80}")
    print("Constructing first part (125 updates with deletions)...")
    
    folder_large_number = config["folder_large_number"]
    input_update_json = config["input_update_json"]
    
    os.makedirs(folder_large_number, exist_ok=True)
    
    # Process update file
    with open(input_update_json, 'r') as f:
        update_data = json.load(f)
    
    request_data = update_data["request_data"]
    all_updates = request_data["updates"]
    total_updates = len(all_updates)
    
    print(f"  Total original updates: {total_updates}")
    
    # Take only first 125 updates
    NUM_UPDATES_FIRST = 125
    updates = all_updates[:NUM_UPDATES_FIRST].copy()
    
    print(f"  Taking first {NUM_UPDATES_FIRST} updates")
    
    log = [
        "="*80,
        "PARTIAL DELETIONS FROM UPDATES:",
        "="*80,
    ]
    
    # Process updates - delete some items from sources
    for update in updates:
        # Delete some items from sources
        if "sources" in update:
            encounter_id = update.get("noregistrasi", "N/A")
            source_log = delete_items_from_sources(update["sources"], encounter_id)
            if source_log:  # Only add to log if there were deletions
                log.extend(source_log)
                log.append("")  # Empty line for readability
        
        # Replace location and performer IDs
        replace_ids_recursive(update, LOCATION_FIELDS, "0-test")
        replace_ids_recursive(update, PERFORMER_FIELDS, "0-test")
    
    # Save modified update file
    output_update = {
        "start_timestamp": request_data["start_timestamp"],
        "end_timestamp": request_data["end_timestamp"],
        "updates": updates
    }
    
    with open(f"{folder_large_number}/1_update_encounters.json", 'w') as f:
        json.dump(output_update, f, indent=2)
    
    with open(f"{folder_large_number}/1_update_log.txt", 'w') as f:
        f.write("\n".join(log))
    
    print(f"  Saved {len(updates)} updates to 1_update_encounters.json")
    
    # Process new encounters file - create new based on update metadata
    new_encounters = []
    for i, update in enumerate(updates):
        norec = update.get("norec")
        noregistrasi = update.get("noregistrasi")
        
        encounter = {
            "norec": norec,
            "noregistrasi": noregistrasi,
            "norm": f"TEST{i+1:06d}",
            "sep_data": generate_sep_data(noregistrasi),
            "tglregistrasi": update.get("updated_at", datetime.now().isoformat() + "Z"),
            "admissionType": "ranap",
            "dpjp_id": "0-test",
            "location_id": "0-test",
            "patientInfo": generate_patient_info(noregistrasi)
        }
        new_encounters.append(encounter)
    
    output_new = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "newEncounters": new_encounters
    }
    
    with open(f"{folder_large_number}/1_new_encounters.json", 'w') as f:
        json.dump(output_new, f, indent=2)
    
    print(f"  Saved {len(new_encounters)} new encounters to 1_new_encounters.json")
    print(f"\nFirst part created in {folder_large_number}/")
    
    # Return the first 125 updates (original, unmodified) for use in second part
    return all_updates[:NUM_UPDATES_FIRST]

# [2-2] Construct second part - 150 updates: 125 same from first part + 25 new
def construct_second_part(folder_name, config, first_part_updates):
    print("\nConstructing second part (150 updates: 125 same + 25 new)...")
    
    folder_large_number = config["folder_large_number"]
    input_update_json = config["input_update_json"]
    
    os.makedirs(folder_large_number, exist_ok=True)
    
    # Process update file - get fresh data
    with open(input_update_json, 'r') as f:
        update_data = json.load(f)
    
    request_data = update_data["request_data"]
    all_updates = request_data["updates"]
    
    NUM_UPDATES_FIRST = 125
    NUM_NEW_UPDATES = 25
    
    # Take 125 from first part (same ones, but fresh from file) + 25 new ones
    # First 125 are the same as first part
    same_updates = all_updates[:NUM_UPDATES_FIRST].copy()
    # Next 25 are new
    new_updates = all_updates[NUM_UPDATES_FIRST:NUM_UPDATES_FIRST + NUM_NEW_UPDATES].copy()
    
    updates = same_updates + new_updates
    
    print(f"  Using {len(same_updates)} same updates from first part + {len(new_updates)} new updates (total: {len(updates)})")
    
    # Replace location and performer IDs only (no deletions)
    for update in updates:
        replace_ids_recursive(update, LOCATION_FIELDS, "0-test")
        replace_ids_recursive(update, PERFORMER_FIELDS, "0-test")
    
    output_update = {
        "start_timestamp": request_data["start_timestamp"],
        "end_timestamp": request_data["end_timestamp"],
        "updates": updates
    }
    
    with open(f"{folder_large_number}/2_update_encounters.json", 'w') as f:
        json.dump(output_update, f, indent=2)
    
    print(f"  Saved {len(updates)} updates to 2_update_encounters.json")
    
    # Create new encounters based on update metadata
    new_encounters = []
    for i, update in enumerate(updates):
        norec = update.get("norec")
        noregistrasi = update.get("noregistrasi")
        
        encounter = {
            "norec": norec,
            "noregistrasi": noregistrasi,
            "norm": f"LARGE{i+1:06d}",
            "sep_data": generate_sep_data(noregistrasi),
            "tglregistrasi": update.get("updated_at", datetime.now().isoformat() + "Z"),
            "admissionType": "ranap",
            "dpjp_id": "0-test",
            "location_id": "0-test",
            "patientInfo": generate_patient_info(noregistrasi)
        }
        new_encounters.append(encounter)
    
    output_new = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "newEncounters": new_encounters
    }
    
    with open(f"{folder_large_number}/2_new_encounters.json", 'w') as f:
        json.dump(output_new, f, indent=2)
    
    print(f"  Saved {len(new_encounters)} new encounters to 2_new_encounters.json")
    print(f"\nSecond part created in {folder_large_number}/")

if __name__ == "__main__":
    for folder_name, config in FOLDER_CONFIGS.items():
        first_part_updates = construct_first_part(folder_name, config)
        construct_second_part(folder_name, config, first_part_updates)
    print("\nDone!")
