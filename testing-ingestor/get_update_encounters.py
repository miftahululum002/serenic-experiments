import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "coba"
OUTPUT_DIR = BASE_DIR / "output"


def get_new_encounters_noregistrasi():
    new_file = OUTPUT_DIR / "new_encounters_dedup.json"
    if new_file.exists():
        with open(new_file) as f:
            encounters = json.load(f)
        return {enc["noregistrasi"] for enc in encounters if enc.get("noregistrasi")}
    return set()


def parse_timestamp_from_filename(filename):
    parts = filename.stem.split("_")
    if len(parts) >= 4:
        date_str = parts[2]
        time_str = parts[3]
        return date_str + time_str
    return ""


def get_update_encounters():
    new_noregistrasi = get_new_encounters_noregistrasi()
    print(f"New encounters noregistrasi: {len(new_noregistrasi)}")

    noreg_to_update = {}
    files = sorted(DATA_DIR.rglob("update_encounters_*.json"))

    for filepath in files:
        with open(filepath) as f:
            data = json.load(f)

        updates = data.get("request_data", {}).get("updates", [])
        ts = parse_timestamp_from_filename(filepath)

        for update in updates:
            noreg = update.get("noregistrasi")
            if noreg and noreg in new_noregistrasi:
                if noreg not in noreg_to_update or ts > noreg_to_update[noreg]["_ts"]:
                    update["_ts"] = ts
                    noreg_to_update[noreg] = update

    return list(noreg_to_update.values())


def save_to_file(encounters):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / "update_encounters_matched.json"
    with open(output_file, "w") as f:
        json.dump(encounters, f, indent=2)
    print(f"Saved {len(encounters)} matched updates to {output_file}")


if __name__ == "__main__":
    updates = get_update_encounters()
    print(f"Total matched updates (latest per noregistrasi): {len(updates)}")
    save_to_file(updates)
