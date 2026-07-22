import json

# Configuration for each folder
FOLDER_CONFIGS = {
    "1_djamil": {
        "update_json": "1_djamil/1_from_empty/update_encounters.json",
        "new_encounters_json": "1_djamil/2_existing/new_encounters.json",
        "output_json": "1_djamil/2_existing/update_encounters_filtered.json"
    },
    "2_rsabhk": {
        "update_json": "2_rsabhk/1_from_empty/update_encounters.json",
        "new_encounters_json": "2_rsabhk/2_existing/new_encounters.json",
        "output_json": "2_rsabhk/2_existing/update_encounters_filtered.json"
    }
}

def filter_updates(folder_name, config):
    print(f"\n{'='*80}")
    print(f"Processing folder: {folder_name}")
    print(f"{'='*80}")
    
    update_json = config["update_json"]
    new_encounters_json = config["new_encounters_json"]
    output_json = config["output_json"]
    
    print("Reading new encounters...")
    with open(new_encounters_json, 'r') as f:
        new_data = json.load(f)
    
    # Get set of noregistrasi from new encounters
    new_encounter_ids = set()
    for encounter in new_data.get("newEncounters", []):
        noregistrasi = encounter.get("noregistrasi")
        if noregistrasi:
            new_encounter_ids.add(noregistrasi)
    
    print(f"Found {len(new_encounter_ids)} encounter IDs in new_encounters.json")
    
    print("Reading update encounters...")
    with open(update_json, 'r') as f:
        update_data = json.load(f)
    
    # Filter updates
    original_updates = update_data.get("updates", [])
    filtered_updates = []
    skipped_count = 0
    
    for update in original_updates:
        noregistrasi = update.get("noregistrasi")
        if noregistrasi in new_encounter_ids:
            filtered_updates.append(update)
        else:
            skipped_count += 1
    
    print(f"Original updates: {len(original_updates)}")
    print(f"Filtered updates: {len(filtered_updates)}")
    print(f"Skipped (not in new_encounters): {skipped_count}")
    
    # Create output
    output_data = {
        "start_timestamp": update_data.get("start_timestamp"),
        "end_timestamp": update_data.get("end_timestamp"),
        "updates": filtered_updates
    }
    
    print(f"Writing to {output_json}...")
    with open(output_json, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    print("Done!")
    
    # Print matched encounter IDs
    print("\nMatched encounter IDs:")
    for update in filtered_updates:
        print(f"  - {update.get('noregistrasi')}")

if __name__ == "__main__":
    for folder_name, config in FOLDER_CONFIGS.items():
        filter_updates(folder_name, config)
    print("\nAll done!")
