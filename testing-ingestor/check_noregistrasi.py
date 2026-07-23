import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
NEW_DIR = BASE_DIR / "final_data" / "10"
UPDATE_DIR = BASE_DIR / "testing-data"
CHUNK_SIZE = 10


def get_noregistrasi_from_new():
    noregistrasi_set = set()
    files = sorted(NEW_DIR.glob("new_encounters_*.json"))

    for filepath in files:
        with open(filepath) as f:
            data = json.load(f)
        for enc in data:
            noreg = enc.get("noregistrasi")
            if noreg:
                noregistrasi_set.add(noreg)

    return noregistrasi_set


def find_matching_updates(noregistrasi_set):
    matches = []
    files = sorted(UPDATE_DIR.rglob("update_encounters_*.json"))

    for filepath in files:
        with open(filepath) as f:
            data = json.load(f)
        updates = data.get("request_data", {}).get("updates", [])
        for update in updates:
            noreg = update.get("noregistrasi")
            if noreg and noreg in noregistrasi_set:
                matches.append(update)

    return matches


def chunk_updates(matches):
    output_dir = BASE_DIR / "final_data" / "10"
    output_dir.mkdir(parents=True, exist_ok=True)

    chunks = [matches[i:i + CHUNK_SIZE] for i in range(0, len(matches), CHUNK_SIZE)]

    for idx, chunk in enumerate(chunks, 1):
        output_file = output_dir / f"matched_update_encounters_{idx:03d}.json"
        with open(output_file, "w") as f:
            json.dump(chunk, f, indent=2)
        print(f"  Saved chunk {idx}: {len(chunk)} updates -> {output_file.name}")

    print(f"\nTotal chunks: {len(chunks)}")
    print(f"Saved to: {output_dir}")


if __name__ == "__main__":
    noregistrasi_set = get_noregistrasi_from_new()
    print(f"Unique noregistrasi from new_encounters: {len(noregistrasi_set)}")

    matches = find_matching_updates(noregistrasi_set)
    print(f"Matching updates: {len(matches)}")

    print("\nChunking updates...")
    chunk_updates(matches)
