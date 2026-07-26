import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "coba"
OUTPUT_DIR = BASE_DIR / "output"


def get_new_encounters():
    seen = set()
    unique_encounters = []

    files = sorted(DATA_DIR.rglob("new_encounters_*.json"))

    for filepath in files:
        with open(filepath) as f:
            data = json.load(f)

        encounters = data.get("request_data", {}).get("newEncounters", [])
        for enc in encounters:
            noreg = enc.get("noregistrasi")
            if noreg and noreg not in seen:
                seen.add(noreg)
                unique_encounters.append(enc)

    return unique_encounters


def save_to_file(encounters):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / "new_encounters_dedup.json"
    with open(output_file, "w") as f:
        json.dump(encounters, f, indent=2)
    print(f"Saved {len(encounters)} unique encounters to {output_file}")


if __name__ == "__main__":
    encounters = get_new_encounters()
    print(f"Total unique encounters (by noregistrasi): {len(encounters)}")
    save_to_file(encounters)
